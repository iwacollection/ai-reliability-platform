from __future__ import annotations

import hashlib
import json

from enum import Enum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from services.agent_runtime.app.action.execution_models import (
    ActionExecutionRecord,
)
from services.agent_runtime.app.action.execution_service import (
    ActionExecutionService,
)
from services.agent_runtime.app.approval.manager import (
    ApprovalDecisionConflictError,
)
from services.agent_runtime.app.approval.models import (
    ApprovalRequest,
    ApprovalStatus,
)
from services.agent_runtime.app.approval.service import (
    ApprovalService,
)
from services.agent_runtime.app.conversation.chatops import (
    ChatOpsInboundMessage,
)
from services.agent_runtime.app.conversation.identity import (
    ChatOpsActorVerificationError,
    ChatOpsAuthorizationDeniedError,
    ChatOpsIdentityAuthenticationError,
    ChatOpsIdentityBindingError,
    ChatOpsSecurityAdapter,
    ChatOpsSecurityContext,
)
from services.agent_runtime.app.conversation.models import (
    ConversationIntent,
)
from services.agent_runtime.app.conversation.orchestrator import (
    ConversationOrchestrator,
)
from services.agent_runtime.app.incident.store import (
    IncidentStore,
)
from services.agent_runtime.app.runtime.action_runtime import (
    ActionRuntime,
)
from services.agent_runtime.app.security.models import (
    ProtectedOperation,
)
from services.agent_runtime.app.verification.coordinator import (
    VerificationCoordinator,
)
from services.agent_runtime.app.verification.service import (
    VerificationService,
)


class ChatOpsWriteStatus(str, Enum):
    NO_WRITE_INTENT = "no_write_intent"
    INCIDENT_REQUIRED = "incident_required"
    ACTOR_VERIFICATION_FAILED = (
        "actor_verification_failed"
    )
    AUTHENTICATION_FAILED = (
        "authentication_failed"
    )
    AUTHORIZATION_DENIED = (
        "authorization_denied"
    )
    APPROVAL_NOT_FOUND = (
        "approval_not_found"
    )
    APPROVAL_AMBIGUOUS = (
        "approval_ambiguous"
    )
    APPROVAL_REQUIRED = (
        "approval_required"
    )
    APPROVED = "approved"
    REJECTED = "rejected"
    EXISTING_EXECUTION = (
        "existing_execution"
    )
    EXECUTION_COMPLETED = (
        "execution_completed"
    )
    EXECUTION_BLOCKED = (
        "execution_blocked"
    )
    VERIFICATION_FOLLOW_UP_REQUIRED = (
        "verification_follow_up_required"
    )
    CONFLICT = "conflict"
    WRITE_FAILED = "write_failed"


class ChatOpsWriteOutcome(BaseModel):
    """Sanitized result returned to the future channel renderer."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: str = "v1"
    success: bool
    status: ChatOpsWriteStatus
    operation: str | None = None

    incident_id: str | None = None
    approval_id: str | None = None
    execution_id: str | None = None
    execution_status: str | None = None
    verification_id: str | None = None
    verification_status: str | None = None

    operator_id: str | None = None
    idempotent_replay: bool = False

    failure_code: str | None = Field(
        default=None,
        max_length=256,
    )

    message: str = Field(
        min_length=1,
        max_length=1000,
    )


class ChatOpsAuthenticatedWriteBridge:
    """
    Authenticated Human-in-the-loop ChatOps write bridge.

    Security order is fail-closed:
    1. classify the write intent without domain reads;
    2. resolve only durable conversation binding;
    3. verify channel actor;
    4. authenticate through existing AuthenticationService;
    5. authorize exact ProtectedOperation through SecurityPolicyEngine;
    6. only then read/mutate Approval/Incident/Action/Verification state.

    No raw channel actor identifier, credential, token, or platform
    signature is persisted by this bridge.
    """

    _INTENT_OPERATION = {
        ConversationIntent.APPROVE: (
            "approval.approve",
            ProtectedOperation.DECIDE_APPROVAL,
        ),
        ConversationIntent.REJECT: (
            "approval.reject",
            ProtectedOperation.DECIDE_APPROVAL,
        ),
        ConversationIntent.REMEDIATE: (
            "action.resume",
            ProtectedOperation.RESUME_ACTION,
        ),
    }

    def __init__(
        self,
        *,
        orchestrator: ConversationOrchestrator,
        security: ChatOpsSecurityAdapter,
        approval_service: ApprovalService,
        incident_store: IncidentStore,
        action_runtime: ActionRuntime,
        action_execution_service: ActionExecutionService,
        verification_service: VerificationService,
        verification_coordinator: VerificationCoordinator,
    ) -> None:
        dependencies = (
            (
                orchestrator,
                ConversationOrchestrator,
                "Conversation Orchestrator",
            ),
            (
                security,
                ChatOpsSecurityAdapter,
                "ChatOps security adapter",
            ),
            (
                approval_service,
                ApprovalService,
                "Approval service",
            ),
            (
                incident_store,
                IncidentStore,
                "Incident store",
            ),
            (
                action_runtime,
                ActionRuntime,
                "Action Runtime",
            ),
            (
                action_execution_service,
                ActionExecutionService,
                "Action Execution service",
            ),
            (
                verification_service,
                VerificationService,
                "Verification service",
            ),
            (
                verification_coordinator,
                VerificationCoordinator,
                "Verification Coordinator",
            ),
        )

        for value, expected, label in dependencies:
            if not isinstance(
                value,
                expected,
            ):
                raise TypeError(
                    f"{label} is invalid"
                )

        self.orchestrator = orchestrator
        self.security = security
        self.approval_service = (
            approval_service
        )
        self.incident_store = (
            incident_store
        )
        self.action_runtime = (
            action_runtime
        )
        self.action_execution_service = (
            action_execution_service
        )
        self.verification_service = (
            verification_service
        )
        self.verification_coordinator = (
            verification_coordinator
        )

    async def handle(
        self,
        message: ChatOpsInboundMessage,
    ) -> ChatOpsWriteOutcome:
        if not isinstance(
            message,
            ChatOpsInboundMessage,
        ):
            raise TypeError(
                "ChatOps write requires ChatOpsInboundMessage"
            )

        intent = (
            self.orchestrator.classifier
            .classify(
                message.text
            )
        )

        operation_contract = (
            self._INTENT_OPERATION.get(
                intent
            )
        )

        if operation_contract is None:
            return ChatOpsWriteOutcome(
                success=False,
                status=(
                    ChatOpsWriteStatus
                    .NO_WRITE_INTENT
                ),
                message=(
                    "ChatOps message does not request an authenticated write"
                ),
            )

        (
            operation_name,
            protected_operation,
        ) = operation_contract

        binding_key = (
            message.conversation
            .binding_key()
        )

        session = await (
            self.orchestrator.sessions
            .get(
                binding_key
            )
        )

        incident_id = (
            message.incident_id
            or (
                session.incident_id
                if session is not None
                else None
            )
        )

        if incident_id is None:
            return ChatOpsWriteOutcome(
                success=False,
                status=(
                    ChatOpsWriteStatus
                    .INCIDENT_REQUIRED
                ),
                operation=operation_name,
                message=(
                    "ChatOps write requires an Incident binding"
                ),
            )

        security = await self._require_security(
            message,
            protected_operation,
            operation_name=(
                operation_name
            ),
            incident_id=incident_id,
        )

        if isinstance(
            security,
            ChatOpsWriteOutcome,
        ):
            return security

        # Bind only after the actor is authenticated and authorized.
        await self.orchestrator.sessions.update(
            conversation_id=binding_key,
            incident_id=incident_id,
            intent=intent,
        )

        if intent in {
            ConversationIntent.APPROVE,
            ConversationIntent.REJECT,
        }:
            return await self._decide_approval(
                message=message,
                security=security,
                incident_id=incident_id,
                approve=(
                    intent
                    == ConversationIntent.APPROVE
                ),
            )

        return await self._resume_action(
            message=message,
            security=security,
            incident_id=incident_id,
        )

    async def _require_security(
        self,
        message: ChatOpsInboundMessage,
        protected_operation: ProtectedOperation,
        *,
        operation_name: str,
        incident_id: str,
    ) -> (
        ChatOpsSecurityContext
        | ChatOpsWriteOutcome
    ):
        try:
            return await self.security.require(
                message,
                protected_operation,
            )

        except ChatOpsActorVerificationError:
            return ChatOpsWriteOutcome(
                success=False,
                status=(
                    ChatOpsWriteStatus
                    .ACTOR_VERIFICATION_FAILED
                ),
                operation=operation_name,
                incident_id=None,
                message=(
                    "ChatOps actor could not be verified"
                ),
            )

        except (
            ChatOpsIdentityBindingError,
            ChatOpsIdentityAuthenticationError,
        ):
            return ChatOpsWriteOutcome(
                success=False,
                status=(
                    ChatOpsWriteStatus
                    .AUTHENTICATION_FAILED
                ),
                operation=operation_name,
                incident_id=None,
                message=(
                    "ChatOps Runtime authentication failed"
                ),
            )

        except ChatOpsAuthorizationDeniedError:
            return ChatOpsWriteOutcome(
                success=False,
                status=(
                    ChatOpsWriteStatus
                    .AUTHORIZATION_DENIED
                ),
                operation=operation_name,
                incident_id=None,
                message=(
                    "ChatOps operator is not authorized for this write"
                ),
            )

    async def _decide_approval(
        self,
        *,
        message: ChatOpsInboundMessage,
        security: ChatOpsSecurityContext,
        incident_id: str,
        approve: bool,
    ) -> ChatOpsWriteOutcome:
        approvals = await (
            self.approval_service
            .list_by_incident(
                incident_id
            )
        )

        desired_status = (
            ApprovalStatus.APPROVED
            if approve
            else ApprovalStatus.REJECTED
        )

        operation_name = (
            "approval.approve"
            if approve
            else "approval.reject"
        )

        pending = [
            item
            for item in approvals
            if (
                item.status
                == ApprovalStatus.PENDING
            )
        ]

        if len(
            pending
        ) > 1:
            return self._approval_ambiguous(
                security=security,
                incident_id=incident_id,
                operation_name=(
                    operation_name
                ),
            )

        if len(
            pending
        ) == 1:
            target = pending[
                0
            ]
            replay = False

        else:
            target = self._exact_decision_replay(
                approvals=approvals,
                message=message,
                security=security,
                desired_status=(
                    desired_status
                ),
                operation_name=(
                    operation_name
                ),
            )

            if target is None:
                return ChatOpsWriteOutcome(
                    success=False,
                    status=(
                        ChatOpsWriteStatus
                        .APPROVAL_NOT_FOUND
                    ),
                    operation=operation_name,
                    incident_id=incident_id,
                    operator_id=(
                        security.principal_id
                    ),
                    message=(
                        "No unique pending Approval is available for this Incident"
                    ),
                )

            replay = True

        idempotency_key = (
            self._idempotency_key(
                message=message,
                operation_name=(
                    operation_name
                ),
                target_id=target.id,
            )
        )

        if replay:
            return ChatOpsWriteOutcome(
                success=True,
                status=(
                    ChatOpsWriteStatus.APPROVED
                    if approve
                    else ChatOpsWriteStatus.REJECTED
                ),
                operation=operation_name,
                incident_id=incident_id,
                approval_id=target.id,
                operator_id=(
                    security.principal_id
                ),
                idempotent_replay=True,
                message=(
                    "Exact ChatOps approval decision replayed from durable state"
                ),
            )

        metadata = self._audit_metadata(
            message=message,
            security=security,
        )

        reason = self._decision_reason(
            message.text
        )

        try:
            if approve:
                updated = await (
                    self.approval_service
                    .approve(
                        target.id,
                        operator_id=(
                            security.principal_id
                        ),
                        idempotency_key=(
                            idempotency_key
                        ),
                        reason=reason,
                        metadata=metadata,
                    )
                )

            else:
                updated = await (
                    self.approval_service
                    .reject(
                        target.id,
                        operator_id=(
                            security.principal_id
                        ),
                        idempotency_key=(
                            idempotency_key
                        ),
                        reason=reason,
                        metadata=metadata,
                    )
                )

        except ApprovalDecisionConflictError:
            return ChatOpsWriteOutcome(
                success=False,
                status=(
                    ChatOpsWriteStatus.CONFLICT
                ),
                operation=operation_name,
                incident_id=incident_id,
                approval_id=target.id,
                operator_id=(
                    security.principal_id
                ),
                failure_code=(
                    "ApprovalDecisionConflictError"
                ),
                message=(
                    "Approval already has a conflicting durable decision"
                ),
            )

        except Exception as exc:
            return ChatOpsWriteOutcome(
                success=False,
                status=(
                    ChatOpsWriteStatus.WRITE_FAILED
                ),
                operation=operation_name,
                incident_id=incident_id,
                approval_id=target.id,
                operator_id=(
                    security.principal_id
                ),
                failure_code=(
                    type(
                        exc
                    ).__name__[
                        :256
                    ]
                ),
                message=(
                    "Approval decision could not be completed safely"
                ),
            )

        return ChatOpsWriteOutcome(
            success=True,
            status=(
                ChatOpsWriteStatus.APPROVED
                if updated.status
                == ApprovalStatus.APPROVED
                else ChatOpsWriteStatus.REJECTED
            ),
            operation=operation_name,
            incident_id=incident_id,
            approval_id=updated.id,
            operator_id=(
                security.principal_id
            ),
            idempotent_replay=False,
            message=(
                "Approval decision persisted through the authenticated ChatOps bridge"
            ),
        )

    async def _resume_action(
        self,
        *,
        message: ChatOpsInboundMessage,
        security: ChatOpsSecurityContext,
        incident_id: str,
    ) -> ChatOpsWriteOutcome:
        operation_name = (
            "action.resume"
        )

        approvals = await (
            self.approval_service
            .list_by_incident(
                incident_id
            )
        )

        approved = [
            item
            for item in approvals
            if (
                item.status
                == ApprovalStatus.APPROVED
                and item.action.approved
                is True
            )
        ]

        if len(
            approved
        ) > 1:
            return self._approval_ambiguous(
                security=security,
                incident_id=incident_id,
                operation_name=operation_name,
            )

        if not approved:
            pending = any(
                item.status
                == ApprovalStatus.PENDING
                for item in approvals
            )

            return ChatOpsWriteOutcome(
                success=False,
                status=(
                    ChatOpsWriteStatus
                    .APPROVAL_REQUIRED
                    if pending
                    else ChatOpsWriteStatus
                    .APPROVAL_NOT_FOUND
                ),
                operation=operation_name,
                incident_id=incident_id,
                operator_id=(
                    security.principal_id
                ),
                message=(
                    "Remediation requires one approved Approval before execution"
                ),
            )

        approval = approved[
            0
        ]

        idempotency_key = (
            self._idempotency_key(
                message=message,
                operation_name=(
                    operation_name
                ),
                target_id=approval.id,
            )
        )

        existing_execution = await (
            self.action_execution_service
            .get_by_approval(
                approval.id
            )
        )

        if existing_execution is not None:
            return await self._existing_execution(
                security=security,
                incident_id=incident_id,
                approval=approval,
                execution=existing_execution,
                idempotency_key=(
                    idempotency_key
                ),
            )

        incident = await self.incident_store.get(
            incident_id
        )

        if incident is None:
            return ChatOpsWriteOutcome(
                success=False,
                status=(
                    ChatOpsWriteStatus
                    .WRITE_FAILED
                ),
                operation=operation_name,
                incident_id=incident_id,
                approval_id=approval.id,
                operator_id=(
                    security.principal_id
                ),
                failure_code=(
                    "IncidentNotFound"
                ),
                message=(
                    "Linked Incident could not be loaded for execution"
                ),
            )

        try:
            execution_result = await (
                self.action_runtime
                .resume(
                    approval.id,
                    incident=incident,
                    operator_id=(
                        security.principal_id
                    ),
                    idempotency_key=(
                        idempotency_key
                    ),
                )
            )

        except Exception as exc:
            return ChatOpsWriteOutcome(
                success=False,
                status=(
                    ChatOpsWriteStatus
                    .WRITE_FAILED
                ),
                operation=operation_name,
                incident_id=incident_id,
                approval_id=approval.id,
                operator_id=(
                    security.principal_id
                ),
                failure_code=(
                    type(
                        exc
                    ).__name__[
                        :256
                    ]
                ),
                message=(
                    "Action resume could not complete safely"
                ),
            )

        persisted_execution = await (
            self.action_execution_service
            .get_by_approval(
                approval.id
            )
        )

        if persisted_execution is None:
            return ChatOpsWriteOutcome(
                success=False,
                status=(
                    ChatOpsWriteStatus
                    .WRITE_FAILED
                ),
                operation=operation_name,
                incident_id=incident_id,
                approval_id=approval.id,
                operator_id=(
                    security.principal_id
                ),
                failure_code=(
                    "ActionExecutionNotPersisted"
                ),
                message=(
                    "Action Runtime returned without a durable execution record"
                ),
            )

        execution_status = (
            persisted_execution.status.value
        )

        verification = await (
            self.verification_service
            .get_by_action_execution(
                persisted_execution.id
            )
        )

        if (
            execution_status
            == "succeeded"
            and verification is None
        ):
            try:
                (
                    verification,
                    _,
                ) = await (
                    self.verification_coordinator
                    .run(
                        incident_id=incident_id,
                        plan=approval.action,
                        namespace=(
                            approval.action.namespace
                        ),
                        cluster=(
                            approval.action.cluster
                        ),
                        action_execution_id=(
                            persisted_execution.id
                        ),
                        metadata={
                            **self._audit_metadata(
                                message=message,
                                security=security,
                            ),
                            "source": "chatops",
                            "trigger": (
                                "post_action_execution"
                            ),
                            "approval_id": (
                                approval.id
                            ),
                        },
                    )
                )

            except Exception as exc:
                return ChatOpsWriteOutcome(
                    success=False,
                    status=(
                        ChatOpsWriteStatus
                        .VERIFICATION_FOLLOW_UP_REQUIRED
                    ),
                    operation=operation_name,
                    incident_id=incident_id,
                    approval_id=approval.id,
                    execution_id=str(
                        persisted_execution.id
                    ),
                    execution_status=(
                        execution_status
                    ),
                    operator_id=(
                        security.principal_id
                    ),
                    failure_code=(
                        type(
                            exc
                        ).__name__[
                            :256
                        ]
                    ),
                    message=(
                        "Action completed but Verification requires follow-up"
                    ),
                )

        verification_status = (
            self._verification_status(
                verification
            )
        )

        result_success = (
            execution_status
            == "succeeded"
            and verification_status
            == "passed"
        )

        return ChatOpsWriteOutcome(
            success=result_success,
            status=(
                ChatOpsWriteStatus
                .EXECUTION_COMPLETED
                if result_success
                else (
                    ChatOpsWriteStatus
                    .VERIFICATION_FOLLOW_UP_REQUIRED
                    if execution_status
                    == "succeeded"
                    else ChatOpsWriteStatus
                    .EXECUTION_BLOCKED
                )
            ),
            operation=operation_name,
            incident_id=incident_id,
            approval_id=approval.id,
            execution_id=str(
                persisted_execution.id
            ),
            execution_status=(
                execution_status
            ),
            verification_id=(
                str(
                    verification.id
                )
                if verification
                is not None
                else None
            ),
            verification_status=(
                verification_status
            ),
            operator_id=(
                security.principal_id
            ),
            idempotent_replay=bool(
                execution_result.get(
                    "idempotent_replay",
                    False,
                )
                if isinstance(
                    execution_result,
                    dict,
                )
                else False
            ),
            message=(
                "Action and Verification completed"
                if result_success
                else (
                    "Action completed; Verification is not yet passed"
                    if execution_status
                    == "succeeded"
                    else "Action execution did not succeed"
                )
            ),
        )

    async def _existing_execution(
        self,
        *,
        security: ChatOpsSecurityContext,
        incident_id: str,
        approval: ApprovalRequest,
        execution: ActionExecutionRecord,
        idempotency_key: str,
    ) -> ChatOpsWriteOutcome:
        verification = await (
            self.verification_service
            .get_by_action_execution(
                execution.id
            )
        )

        verification_status = (
            self._verification_status(
                verification
            )
        )

        execution_status = (
            execution.status.value
        )

        return ChatOpsWriteOutcome(
            success=(
                execution_status
                == "succeeded"
                and verification_status
                == "passed"
            ),
            status=(
                ChatOpsWriteStatus
                .EXISTING_EXECUTION
            ),
            operation="action.resume",
            incident_id=incident_id,
            approval_id=approval.id,
            execution_id=str(
                execution.id
            ),
            execution_status=(
                execution_status
            ),
            verification_id=(
                str(
                    verification.id
                )
                if verification
                is not None
                else None
            ),
            verification_status=(
                verification_status
            ),
            operator_id=(
                security.principal_id
            ),
            idempotent_replay=(
                execution.operator_id
                == security.principal_id
                and execution.idempotency_key
                == idempotency_key
            ),
            message=(
                "A durable Action Execution already exists for this Approval"
            ),
        )

    @staticmethod
    def _approval_ambiguous(
        *,
        security: ChatOpsSecurityContext,
        incident_id: str,
        operation_name: str,
    ) -> ChatOpsWriteOutcome:
        return ChatOpsWriteOutcome(
            success=False,
            status=(
                ChatOpsWriteStatus
                .APPROVAL_AMBIGUOUS
            ),
            operation=operation_name,
            incident_id=incident_id,
            operator_id=(
                security.principal_id
            ),
            message=(
                "Multiple Approval candidates exist; ChatOps refuses to guess"
            ),
        )

    def _exact_decision_replay(
        self,
        *,
        approvals: list[
            ApprovalRequest
        ],
        message: ChatOpsInboundMessage,
        security: ChatOpsSecurityContext,
        desired_status: ApprovalStatus,
        operation_name: str,
    ) -> ApprovalRequest | None:
        matches = []

        for item in approvals:
            decision = item.decision

            if decision is None:
                continue

            expected_key = (
                self._idempotency_key(
                    message=message,
                    operation_name=(
                        operation_name
                    ),
                    target_id=item.id,
                )
            )

            if (
                item.status
                == desired_status
                and decision.status
                == desired_status
                and decision.operator_id
                == security.principal_id
                and decision.idempotency_key
                == expected_key
            ):
                matches.append(
                    item
                )

        if len(
            matches
        ) == 1:
            return matches[
                0
            ]

        return None

    @staticmethod
    def _idempotency_key(
        *,
        message: ChatOpsInboundMessage,
        operation_name: str,
        target_id: str,
    ) -> str:
        payload = json.dumps(
            [
                message.conversation
                .binding_key(),
                message.message_id,
                message.text,
                operation_name,
                target_id,
            ],
            ensure_ascii=False,
            separators=(
                ",",
                ":",
            ),
        )

        return (
            "chatops:"
            + hashlib.sha256(
                payload.encode(
                    "utf-8"
                )
            ).hexdigest()
        )

    @classmethod
    def _audit_metadata(
        cls,
        *,
        message: ChatOpsInboundMessage,
        security: ChatOpsSecurityContext,
    ) -> dict[str, Any]:
        metadata = {
            "source": "chatops",
            "chatops_binding_key": (
                message.conversation
                .binding_key()
            ),
            "chatops_message_fingerprint": (
                hashlib.sha256(
                    (
                        message.message_id
                        + "\n"
                        + message.text
                    ).encode(
                        "utf-8"
                    )
                ).hexdigest()
            ),
        }

        metadata.update(
            security.stable_audit_context()
        )

        return metadata

    @staticmethod
    def _decision_reason(
        text: str,
    ) -> str:
        normalized = text.strip()

        if len(
            normalized
        ) > 1900:
            normalized = (
                normalized[
                    :1900
                ]
            )

        return (
            "ChatOps decision: "
            + normalized
        )

    @staticmethod
    def _verification_status(
        verification,
    ) -> str | None:
        if verification is None:
            return None

        status = getattr(
            verification,
            "status",
            None,
        )

        if status is None:
            return None

        return str(
            getattr(
                status,
                "value",
                status,
            )
        ).strip().lower()


__all__ = [
    "ChatOpsAuthenticatedWriteBridge",
    "ChatOpsWriteOutcome",
    "ChatOpsWriteStatus",
]
