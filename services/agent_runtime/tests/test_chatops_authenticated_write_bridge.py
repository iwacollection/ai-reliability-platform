from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionType,
)
from services.agent_runtime.app.approval.models import (
    ApprovalStatus,
)
from services.agent_runtime.app.conversation import (
    BaseChatOpsActorVerifier,
    ChatOpsActorVerificationError,
    ChatOpsConversationRef,
    ChatOpsIdentityBinding,
    ChatOpsIdentityBindingRegistry,
    ChatOpsInboundMessage,
    ChatOpsVerifiedActor,
    ChatOpsWriteStatus,
)
from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)
from services.agent_runtime.app.incident.state import (
    IncidentState,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)
from services.agent_runtime.app.security.authentication import (
    ApiKeyAuthenticationProvider,
    ApiKeyRecord,
)
from services.agent_runtime.app.security.models import (
    OperatorRole,
)
from services.agent_runtime.app.security.service import (
    AuthenticationProviderRegistry,
    AuthenticationService,
)


APPROVER_SECRET = (
    "chatops-approver-secret-000000000000001"
)
EXECUTOR_SECRET = (
    "chatops-executor-secret-000000000000001"
)


class ExactActorVerifier(
    BaseChatOpsActorVerifier
):
    def __init__(
        self,
        allowed,
    ):
        self.allowed = set(
            allowed
        )
        self.calls = []

    async def verify(
        self,
        message,
    ):
        self.calls.append(
            message.message_id
        )

        actor_id = (
            message.external_actor_id
        )

        if actor_id not in self.allowed:
            raise ChatOpsActorVerificationError(
                "test actor is not verified"
            )

        return ChatOpsVerifiedActor(
            channel=(
                message.conversation.channel
            ),
            tenant_id=(
                message.conversation.tenant_id
            ),
            external_actor_id=actor_id,
            verification_method=(
                "unit_test_verified_transport"
            ),
        )


class MismatchedActorVerifier(
    BaseChatOpsActorVerifier
):
    async def verify(
        self,
        message,
    ):
        return ChatOpsVerifiedActor(
            channel=(
                message.conversation.channel
            ),
            tenant_id=(
                message.conversation.tenant_id
            ),
            external_actor_id=(
                "different-verified-actor"
            ),
            verification_method=(
                "unit_test_verified_transport"
            ),
        )


def authentication_service():
    provider = ApiKeyAuthenticationProvider(
        [
            ApiKeyRecord.from_plaintext(
                key_id="approver-key",
                api_key=APPROVER_SECRET,
                principal_id="approver-1",
                roles={
                    OperatorRole.APPROVER,
                },
            ),
            ApiKeyRecord.from_plaintext(
                key_id="executor-key",
                api_key=EXECUTOR_SECRET,
                principal_id="executor-1",
                roles={
                    OperatorRole.EXECUTOR,
                },
            ),
        ]
    )

    return AuthenticationService(
        AuthenticationProviderRegistry(
            [
                provider
            ]
        )
    )


def bindings():
    return ChatOpsIdentityBindingRegistry(
        [
            ChatOpsIdentityBinding(
                channel="feishu",
                tenant_id="tenant-a",
                external_actor_id=(
                    "actor-approver"
                ),
                expected_principal_id=(
                    "approver-1"
                ),
                credential_env=(
                    "CHATOPS_APPROVER_API_KEY"
                ),
            ),
            ChatOpsIdentityBinding(
                channel="feishu",
                tenant_id="tenant-a",
                external_actor_id=(
                    "actor-executor"
                ),
                expected_principal_id=(
                    "executor-1"
                ),
                credential_env=(
                    "CHATOPS_EXECUTOR_API_KEY"
                ),
            ),
        ]
    )


def conversation():
    return ChatOpsConversationRef(
        channel="feishu",
        tenant_id="tenant-a",
        conversation_id="sre-group",
        thread_id="incident-thread",
    )


def inbound(
    *,
    actor,
    text,
    incident_id,
    message_id,
):
    return ChatOpsInboundMessage(
        conversation=conversation(),
        message_id=message_id,
        external_actor_id=actor,
        text=text,
        incident_id=incident_id,
    )


def create_runtime(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(
        tmp_path
    )

    monkeypatch.setenv(
        "CHATOPS_APPROVER_API_KEY",
        APPROVER_SECRET,
    )

    monkeypatch.setenv(
        "CHATOPS_EXECUTOR_API_KEY",
        EXECUTOR_SECRET,
    )

    for name in (
        "PROMETHEUS_URL",
        "KUBERNETES_API_URL",
        "KUBERNETES_SERVICE_HOST",
        "KUBERNETES_SERVICE_PORT",
        "KUBERNETES_SERVICE_PORT_HTTPS",
    ):
        monkeypatch.delenv(
            name,
            raising=False,
        )

    monkeypatch.setenv(
        "PROMETHEUS_ALLOW_MOCK_FALLBACK",
        "true",
    )

    monkeypatch.setenv(
        "KUBERNETES_ALLOW_DRY_RUN_FALLBACK",
        "true",
    )

    return AgentRuntime(
        authentication_service=(
            authentication_service()
        )
    )


async def create_incident_and_approval(
    runtime,
):
    incident = IncidentState(
        status=IncidentStatus.CONFIRMED,
        reason="RCA complete",
    )

    incident = await (
        runtime.incident_store.save(
            incident
        )
    )

    plan = ActionPlan(
        type=(
            ActionType.INCREASE_MEMORY_LIMIT
        ),
        target="checkout-api",
        namespace="checkout",
        cluster="prod-us-03",
    )

    approval = await (
        runtime.approval.create_approval(
            action=plan,
            reason="AI recommends remediation",
            incident_id=incident.id,
        )
    )

    return (
        incident,
        approval,
    )


@pytest.mark.asyncio
async def test_verified_approver_can_approve_and_exact_webhook_replay_is_idempotent(
    monkeypatch,
    tmp_path,
):
    runtime = create_runtime(
        monkeypatch,
        tmp_path,
    )

    (
        incident,
        approval,
    ) = await create_incident_and_approval(
        runtime
    )

    verifier = ExactActorVerifier(
        {
            "actor-approver",
            "actor-executor",
        }
    )

    bridge = (
        runtime
        .create_chatops_authenticated_write_bridge(
            actor_verifier=verifier,
            identity_bindings=bindings(),
        )
    )

    message = inbound(
        actor="actor-approver",
        text="批准执行",
        incident_id=str(
            incident.id
        ),
        message_id="msg-approve-1",
    )

    first = await bridge.handle(
        message
    )

    assert first.success is True
    assert (
        first.status
        == ChatOpsWriteStatus.APPROVED
    )
    assert first.approval_id == (
        approval.id
    )
    assert first.operator_id == (
        "approver-1"
    )
    assert (
        first.idempotent_replay
        is False
    )

    stored = await runtime.approval.get(
        approval.id
    )

    assert stored is not None
    assert stored.status == (
        ApprovalStatus.APPROVED
    )
    assert stored.decision is not None
    assert stored.decision.operator_id == (
        "approver-1"
    )
    assert stored.decision.metadata[
        "source"
    ] == "chatops"
    assert stored.decision.metadata[
        "protected_operation"
    ] == "approval.decide"

    serialized = str(
        stored.decision.model_dump(
            mode="json"
        )
    )

    assert "actor-approver" not in (
        serialized
    )
    assert APPROVER_SECRET not in (
        serialized
    )

    second = await bridge.handle(
        message
    )

    assert second.success is True
    assert (
        second.status
        == ChatOpsWriteStatus.APPROVED
    )
    assert (
        second.idempotent_replay
        is True
    )


@pytest.mark.asyncio
async def test_spoofed_external_actor_fails_before_approval_domain_read(
    monkeypatch,
    tmp_path,
):
    runtime = create_runtime(
        monkeypatch,
        tmp_path,
    )

    incident, _ = (
        await create_incident_and_approval(
            runtime
        )
    )

    bridge = (
        runtime
        .create_chatops_authenticated_write_bridge(
            actor_verifier=(
                MismatchedActorVerifier()
            ),
            identity_bindings=bindings(),
        )
    )

    calls = []

    async def forbidden(
        incident_id,
    ):
        calls.append(
            incident_id
        )
        raise AssertionError(
            "Actor spoofing reached Approval domain read"
        )

    monkeypatch.setattr(
        runtime.approval,
        "list_by_incident",
        forbidden,
    )

    outcome = await bridge.handle(
        inbound(
            actor="actor-approver",
            text="批准执行",
            incident_id=str(
                incident.id
            ),
            message_id="msg-spoof-1",
        )
    )

    assert outcome.success is False
    assert outcome.status == (
        ChatOpsWriteStatus
        .ACTOR_VERIFICATION_FAILED
    )
    assert outcome.incident_id is None
    assert calls == []


@pytest.mark.asyncio
async def test_approver_cannot_execute_and_denial_happens_before_domain_read(
    monkeypatch,
    tmp_path,
):
    runtime = create_runtime(
        monkeypatch,
        tmp_path,
    )

    (
        incident,
        approval,
    ) = await create_incident_and_approval(
        runtime
    )

    await runtime.approval.approve(
        approval.id,
        operator_id="approver-1",
        idempotency_key=(
            "preapproved-test-key"
        ),
        reason="approved before executor test",
        metadata={
            "source": "test",
        },
    )

    bridge = (
        runtime
        .create_chatops_authenticated_write_bridge(
            actor_verifier=(
                ExactActorVerifier(
                    {
                        "actor-approver",
                        "actor-executor",
                    }
                )
            ),
            identity_bindings=bindings(),
        )
    )

    calls = []

    async def forbidden(
        incident_id,
    ):
        calls.append(
            incident_id
        )
        raise AssertionError(
            "Unauthorized approver reached Action domain read"
        )

    monkeypatch.setattr(
        runtime.approval,
        "list_by_incident",
        forbidden,
    )

    outcome = await bridge.handle(
        inbound(
            actor="actor-approver",
            text="执行修复",
            incident_id=str(
                incident.id
            ),
            message_id="msg-execute-denied",
        )
    )

    assert outcome.success is False
    assert outcome.status == (
        ChatOpsWriteStatus
        .AUTHORIZATION_DENIED
    )
    assert outcome.incident_id is None
    assert calls == []


@pytest.mark.asyncio
async def test_executor_path_uses_authenticated_principal_and_runs_verification(
    monkeypatch,
    tmp_path,
):
    runtime = create_runtime(
        monkeypatch,
        tmp_path,
    )

    (
        incident,
        approval,
    ) = await create_incident_and_approval(
        runtime
    )

    approved = await (
        runtime.approval.approve(
            approval.id,
            operator_id="approver-1",
            idempotency_key=(
                "approval-before-execution"
            ),
            reason="approved for executor",
            metadata={
                "source": "test",
            },
        )
    )

    assert approved.status == (
        ApprovalStatus.APPROVED
    )

    bridge = (
        runtime
        .create_chatops_authenticated_write_bridge(
            actor_verifier=(
                ExactActorVerifier(
                    {
                        "actor-approver",
                        "actor-executor",
                    }
                )
            ),
            identity_bindings=bindings(),
        )
    )

    get_calls = {
        "count": 0,
    }

    fake_execution = SimpleNamespace(
        id="execution-1",
        status=SimpleNamespace(
            value="succeeded"
        ),
        operator_id="executor-1",
        idempotency_key="placeholder",
    )

    async def fake_get_by_approval(
        approval_id,
    ):
        assert approval_id == (
            approval.id
        )

        get_calls[
            "count"
        ] += 1

        if get_calls[
            "count"
        ] == 1:
            return None

        return fake_execution

    monkeypatch.setattr(
        runtime.action_execution_service,
        "get_by_approval",
        fake_get_by_approval,
    )

    resume_calls = []

    async def fake_resume(
        approval_id,
        incident=None,
        *,
        operator_id=None,
        idempotency_key=None,
    ):
        resume_calls.append(
            {
                "approval_id": approval_id,
                "incident_id": str(
                    incident.id
                ),
                "operator_id": operator_id,
                "idempotency_key": (
                    idempotency_key
                ),
            }
        )

        fake_execution.idempotency_key = (
            idempotency_key
        )

        return {
            "success": True,
            "status": "succeeded",
            "execution_id": (
                "execution-1"
            ),
            "idempotent_replay": False,
        }

    monkeypatch.setattr(
        runtime.action_runtime,
        "resume",
        fake_resume,
    )

    async def no_existing_verification(
        action_execution_id,
    ):
        assert str(
            action_execution_id
        ) == "execution-1"

        return None

    monkeypatch.setattr(
        runtime.verification,
        "get_by_action_execution",
        no_existing_verification,
    )

    verification_calls = []

    async def fake_verification_run(
        **kwargs,
    ):
        verification_calls.append(
            dict(
                kwargs
            )
        )

        return (
            SimpleNamespace(
                id="verification-1",
                status=SimpleNamespace(
                    value="passed"
                ),
            ),
            incident,
        )

    monkeypatch.setattr(
        runtime.verification_coordinator,
        "run",
        fake_verification_run,
    )

    outcome = await bridge.handle(
        inbound(
            actor="actor-executor",
            text="执行修复",
            incident_id=str(
                incident.id
            ),
            message_id="msg-execute-1",
        )
    )

    assert outcome.success is True
    assert outcome.status == (
        ChatOpsWriteStatus
        .EXECUTION_COMPLETED
    )
    assert outcome.operator_id == (
        "executor-1"
    )
    assert outcome.execution_status == (
        "succeeded"
    )
    assert outcome.verification_status == (
        "passed"
    )

    assert len(
        resume_calls
    ) == 1
    assert resume_calls[
        0
    ][
        "operator_id"
    ] == "executor-1"

    assert resume_calls[
        0
    ][
        "idempotency_key"
    ].startswith(
        "chatops:"
    )

    assert len(
        verification_calls
    ) == 1
    assert verification_calls[
        0
    ][
        "metadata"
    ][
        "source"
    ] == "chatops"


@pytest.mark.asyncio
async def test_missing_actor_binding_fails_before_domain_work(
    monkeypatch,
    tmp_path,
):
    runtime = create_runtime(
        monkeypatch,
        tmp_path,
    )

    incident, _ = (
        await create_incident_and_approval(
            runtime
        )
    )

    verifier = ExactActorVerifier(
        {
            "unbound-actor",
        }
    )

    bridge = (
        runtime
        .create_chatops_authenticated_write_bridge(
            actor_verifier=verifier,
            identity_bindings=bindings(),
        )
    )

    calls = []

    async def forbidden(
        incident_id,
    ):
        calls.append(
            incident_id
        )
        raise AssertionError(
            "Unbound actor reached Approval domain"
        )

    monkeypatch.setattr(
        runtime.approval,
        "list_by_incident",
        forbidden,
    )

    outcome = await bridge.handle(
        inbound(
            actor="unbound-actor",
            text="批准执行",
            incident_id=str(
                incident.id
            ),
            message_id="msg-unbound-1",
        )
    )

    assert outcome.success is False
    assert outcome.status == (
        ChatOpsWriteStatus
        .AUTHENTICATION_FAILED
    )
    assert calls == []


def test_chatops_identity_and_write_bridge_do_not_define_roles_or_credentials():
    from pathlib import Path

    import services.agent_runtime.app.conversation.identity as identity_module
    import services.agent_runtime.app.conversation.write_bridge as write_module

    identity_source = Path(
        identity_module.__file__
    ).read_text(
        encoding="utf-8"
    )

    write_source = Path(
        write_module.__file__
    ).read_text(
        encoding="utf-8"
    )

    assert "DEFAULT_ROLE_PERMISSIONS" not in (
        identity_source
        + write_source
    )

    assert "ApiKeyRecord" not in (
        identity_source
        + write_source
    )

    assert "external_actor_id" not in (
        write_source
    )

    forbidden_write_shortcuts = [
        "KubernetesProductionExecutor",
        "httpx",
        "requests.",
        "aiohttp",
    ]

    assert [
        item
        for item in forbidden_write_shortcuts
        if item in write_source
    ] == []
