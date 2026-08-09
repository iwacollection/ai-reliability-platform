from __future__ import annotations

from enum import Enum

from services.agent_runtime.app.action.execution_service import (
    ActionExecutionService,
)
from services.agent_runtime.app.approval.service import (
    ApprovalService,
)
from services.agent_runtime.app.conversation.models import (
    ConversationEvidenceView,
    ConversationHypothesisView,
    ConversationIncidentContext,
)
from services.agent_runtime.app.conversation.provider import (
    BaseConversationIncidentContextProvider,
)
from services.agent_runtime.app.incident.store import (
    IncidentStore,
)
from services.agent_runtime.app.investigation.persistence_models import (
    IncidentAnalysisRecord,
)
from services.agent_runtime.app.investigation.store import (
    IncidentAnalysisStore,
)
from services.agent_runtime.app.verification.service import (
    VerificationService,
)


class RuntimeConversationIncidentContextProvider(
    BaseConversationIncidentContextProvider
):
    """
    Read-only ChatOps projection over existing authoritative Runtime stores.

    Source ownership:
    - Incident status/reason: IncidentStore
    - primary RCA + Investigation evidence/hypotheses: IncidentAnalysisStore
    - Approval: ApprovalService / ApprovalStore
    - Action Execution: ActionExecutionService / ActionExecutionStore
    - Verification: VerificationService / VerificationStore

    No write method is exposed.
    """

    def __init__(
        self,
        *,
        incident_store: IncidentStore,
        analysis_store: IncidentAnalysisStore,
        approval_service: ApprovalService,
        action_execution_service: ActionExecutionService,
        verification_service: VerificationService,
    ) -> None:
        if not isinstance(
            incident_store,
            IncidentStore,
        ):
            raise TypeError(
                "Conversation Incident store is invalid"
            )

        if not isinstance(
            analysis_store,
            IncidentAnalysisStore,
        ):
            raise TypeError(
                "Conversation Analysis store is invalid"
            )

        if not isinstance(
            approval_service,
            ApprovalService,
        ):
            raise TypeError(
                "Conversation Approval service is invalid"
            )

        if not isinstance(
            action_execution_service,
            ActionExecutionService,
        ):
            raise TypeError(
                "Conversation Action Execution service is invalid"
            )

        if not isinstance(
            verification_service,
            VerificationService,
        ):
            raise TypeError(
                "Conversation Verification service is invalid"
            )

        self.incident_store = (
            incident_store
        )
        self.analysis_store = (
            analysis_store
        )
        self.approval_service = (
            approval_service
        )
        self.action_execution_service = (
            action_execution_service
        )
        self.verification_service = (
            verification_service
        )

    async def get(
        self,
        incident_id: str,
    ) -> ConversationIncidentContext | None:
        incident = await self.incident_store.get(
            incident_id
        )

        if incident is None:
            return None

        analysis = await self.analysis_store.get(
            incident_id
        )

        approvals = await (
            self.approval_service
            .list_by_incident(
                incident_id
            )
        )

        executions = await (
            self.action_execution_service
            .list_by_incident(
                incident_id
            )
        )

        verifications = await (
            self.verification_service
            .list_by_incident(
                incident_id
            )
        )

        latest_approval = (
            approvals[
                -1
            ]
            if approvals
            else None
        )

        latest_execution = (
            executions[
                -1
            ]
            if executions
            else None
        )

        latest_verification = (
            verifications[
                -1
            ]
            if verifications
            else None
        )

        (
            root_cause,
            root_cause_confidence,
            rca_source,
        ) = self._root_cause(
            analysis
        )

        evidence = self._evidence(
            analysis
        )

        hypotheses = (
            self._hypotheses(
                analysis
            )
        )

        title = self._title(
            analysis
        )

        recommended_action = None
        action_risk = None
        approval_status = None

        if latest_approval is not None:
            action = (
                latest_approval.action
            )

            recommended_action = (
                self._enum_value(
                    action.type
                )
                + " -> "
                + action.target
            )

            action_risk = (
                self._enum_value(
                    action.risk
                )
            )

            approval_status = (
                self._enum_value(
                    latest_approval.status
                )
            )

        action_execution_status = (
            self._enum_value(
                latest_execution.status
            )
            if latest_execution
            is not None
            else None
        )

        verification_status = (
            self._enum_value(
                latest_verification.status
            )
            if latest_verification
            is not None
            else None
        )

        metadata = {
            "rca_source": (
                rca_source
            ),
            "analysis_available": (
                analysis is not None
            ),
            "investigation_status": (
                self._enum_value(
                    analysis.investigation.status
                )
                if (
                    analysis is not None
                    and analysis.investigation
                    is not None
                )
                else None
            ),
        }

        return ConversationIncidentContext(
            incident_id=str(
                incident.id
            ),
            status=self._enum_value(
                incident.status
            ),
            title=title,
            summary=incident.reason,
            root_cause=root_cause,
            root_cause_confidence=(
                root_cause_confidence
            ),
            evidence=evidence,
            hypotheses=hypotheses,
            recommended_action=(
                recommended_action
            ),
            action_risk=action_risk,
            approval_status=(
                approval_status
            ),
            action_execution_status=(
                action_execution_status
            ),
            verification_status=(
                verification_status
            ),
            metadata=metadata,
        )

    @classmethod
    def _root_cause(
        cls,
        analysis: (
            IncidentAnalysisRecord
            | None
        ),
    ) -> tuple[
        str | None,
        float | None,
        str | None,
    ]:
        if analysis is None:
            return (
                None,
                None,
                None,
            )

        if (
            analysis.primary_rca
            is not None
        ):
            return (
                analysis.primary_rca
                .root_cause,
                analysis.primary_rca
                .confidence,
                "planner_rca",
            )

        investigation = (
            analysis.investigation
        )

        if (
            investigation is not None
            and investigation.conclusion
            is not None
        ):
            return (
                investigation.conclusion
                .root_cause,
                investigation.conclusion
                .confidence,
                "investigation_shadow",
            )

        return (
            None,
            None,
            None,
        )

    @classmethod
    def _evidence(
        cls,
        analysis: (
            IncidentAnalysisRecord
            | None
        ),
    ) -> tuple[
        ConversationEvidenceView,
        ...,
    ]:
        if analysis is None:
            return ()

        items = []

        if (
            analysis.primary_rca
            is not None
        ):
            for index, summary in enumerate(
                analysis.primary_rca.evidence,
                start=1,
            ):
                items.append(
                    ConversationEvidenceView(
                        evidence_id=(
                            "planner-rca-"
                            + str(
                                index
                            )
                        ),
                        source="planner_rca",
                        summary=summary,
                        trusted=False,
                        cluster_verified=False,
                    )
                )

        if (
            analysis.investigation
            is not None
        ):
            for item in (
                analysis.investigation
                .evidence
            ):
                items.append(
                    ConversationEvidenceView(
                        evidence_id=(
                            item.evidence_id
                        ),
                        source=item.source,
                        summary=(
                            cls._evidence_summary(
                                item
                            )
                        ),
                        trusted=item.trusted,
                        cluster_verified=(
                            item.cluster_verified
                        ),
                    )
                )

        return tuple(
            items
        )

    @staticmethod
    def _hypotheses(
        analysis: (
            IncidentAnalysisRecord
            | None
        ),
    ) -> tuple[
        ConversationHypothesisView,
        ...,
    ]:
        if (
            analysis is None
            or analysis.investigation
            is None
        ):
            return ()

        return tuple(
            ConversationHypothesisView(
                cause=item.cause,
                confidence=item.confidence,
            )
            for item in (
                analysis.investigation
                .hypotheses
            )
        )

    @staticmethod
    def _title(
        analysis: (
            IncidentAnalysisRecord
            | None
        ),
    ) -> str | None:
        if analysis is None:
            return None

        scope = analysis.scope

        if scope.resource:
            return (
                scope.resource
                + " / "
                + scope.alert_name
            )

        return scope.alert_name

    @staticmethod
    def _evidence_summary(
        item,
    ) -> str:
        if not item.success:
            return (
                item.probe.value
                + ": "
                + (
                    item.error_code
                    or "collection_failed"
                )
            )

        facts = []

        for key in sorted(
            item.facts
        ):
            value = item.facts[
                key
            ]

            facts.append(
                str(
                    key
                )[
                    :128
                ]
                + "="
                + str(
                    value
                )[
                    :256
                ]
            )

        if not facts:
            return (
                item.probe.value
                + ": collected"
            )

        return (
            item.probe.value
            + ": "
            + ", ".join(
                facts
            )[
                :1600
            ]
        )

    @staticmethod
    def _enum_value(
        value,
    ) -> str:
        if isinstance(
            value,
            Enum,
        ):
            return str(
                value.value
            )

        return str(
            value
        )


__all__ = [
    "RuntimeConversationIncidentContextProvider",
]
