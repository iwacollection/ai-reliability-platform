import asyncio
from datetime import timedelta

import httpx
import pytest
from pydantic import ValidationError

from common.config.settings import (
    KUBERNETES_PRODUCTION_WRITE_ACKNOWLEDGEMENT,
    KubernetesProductionExecutionConfig,
)
from services.agent_runtime.app.action.production_pilot_go_no_go_models import (
    PRODUCTION_PILOT_GO_NO_GO_ACKNOWLEDGEMENT,
    PRODUCTION_PILOT_LIVE_PROBE_ACKNOWLEDGEMENT,
    ProductionPilotGoNoGoRequest,
    ProductionPilotLiveProbeRequest,
)
from services.agent_runtime.app.action.production_pilot_go_no_go_service import (
    ProductionPilotGoNoGoError,
    ProductionPilotGoNoGoService,
    ProductionPilotGoNoGoStaleEvidenceError,
)
from services.agent_runtime.app.action.production_pilot_go_no_go_store import (
    ProductionPilotGoNoGoStore,
)
from services.agent_runtime.app.action.production_pilot_live_probe import (
    PRODUCTION_PILOT_LIVE_PROBE_GATE_ACKNOWLEDGEMENT,
    ProductionPilotLiveProbeFactoryError,
    ProductionPilotLiveReadinessProbe,
    create_production_pilot_live_readiness_probe,
)
from services.agent_runtime.tests.production_action_expiry_support import (
    APPROVAL_ID,
    NOW,
    WORKLOAD_UID,
)
from services.agent_runtime.tests.test_production_pilot import (
    execution_config,
    preflight_config,
)
from services.agent_runtime.tests.test_production_pilot_final_handoff import (
    handoff_request,
    handoff_service,
)
from services.agent_runtime.tests.test_production_pilot_pre_enable_evidence import (
    EXECUTOR_ID,
    pre_enable_environment,
    sqlite_logical_snapshot,
)


PREFLIGHT_TOKEN = "preflight-live-probe-token-0001"
PRODUCTION_TOKEN = "production-live-probe-token-0001"
GO_REVIEWER = "test-admin-operator"


def deployment_payload(
    *,
    memory: str = "512Mi",
    resource_version: str = "500",
    generation: int = 9,
) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "namespace": "payment",
            "name": "payment-api",
            "uid": str(WORKLOAD_UID),
            "resourceVersion": resource_version,
            "generation": generation,
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "payment-api",
                            "resources": {
                                "limits": {
                                    "memory": memory,
                                }
                            },
                        }
                    ]
                }
            }
        },
    }


def live_probe(
    calls: list[httpx.Request],
    *,
    status_code: int = 200,
    payload: dict | None = None,
) -> tuple[ProductionPilotLiveReadinessProbe, httpx.AsyncClient]:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            status_code,
            json=payload or deployment_payload(),
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    probe = ProductionPilotLiveReadinessProbe(
        api_url="https://kubernetes.test",
        cluster_name="production-a",
        namespace="payment",
        deployment="payment-api",
        container="payment-api",
        preflight_token=PREFLIGHT_TOKEN,
        production_token=PRODUCTION_TOKEN,
        client=client,
        injected_client_tls_verified=True,
    )
    return probe, client


async def go_no_go_environment(
    tmp_path,
    *,
    status_code: int = 200,
    payload: dict | None = None,
):
    environment = await pre_enable_environment(tmp_path)
    evidence = await environment["service"].get(APPROVAL_ID)
    assert evidence is not None
    calls: list[httpx.Request] = []
    probe, client = live_probe(
        calls,
        status_code=status_code,
        payload=payload,
    )
    final_service = handoff_service(
        environment,
        reference_probe=lambda kind, reference: True,
    )
    store = ProductionPilotGoNoGoStore(
        tmp_path / "production_pilot_go_no_go.db"
    )
    service = ProductionPilotGoNoGoService(
        store=store,
        live_probe=probe,
        final_handoff_service=final_service,
        artifact_service=environment["artifact"],
        pilot_control=environment["control"],
        clock=environment["clock"],
    )
    handoff = await final_service.rehearse(
        approval_id=APPROVAL_ID,
        operator_id=EXECUTOR_ID,
        request=handoff_request(evidence.evidence_sha256),
    )
    request = ProductionPilotLiveProbeRequest(
        expected_handoff_report_sha256=handoff.report_sha256,
        handoff=handoff_request(evidence.evidence_sha256),
        acknowledgement=(
            PRODUCTION_PILOT_LIVE_PROBE_ACKNOWLEDGEMENT
        ),
    )
    return {
        "environment": environment,
        "service": service,
        "store": store,
        "client": client,
        "calls": calls,
        "handoff": handoff,
        "request": request,
    }


def go_request(
    probe_sha256: str,
    **overrides,
) -> ProductionPilotGoNoGoRequest:
    values = {
        "expected_probe_record_sha256": probe_sha256,
        "decision": "go",
        "reason": "Final bounded OOMKilled Pilot review passed",
        "live_probe_reviewed": True,
        "monitoring_owner_confirmed": True,
        "rollback_owner_confirmed": True,
        "reconciliation_owner_confirmed": True,
        "controlled_change_window_confirmed": True,
        "acknowledgement": (
            PRODUCTION_PILOT_GO_NO_GO_ACKNOWLEDGEMENT
        ),
    }
    values.update(overrides)
    return ProductionPilotGoNoGoRequest(**values)


@pytest.mark.asyncio
async def test_live_probe_is_two_gets_durable_and_exact_replay(
    tmp_path,
):
    context = await go_no_go_environment(tmp_path)
    service = context["service"]
    before = sqlite_logical_snapshot(tmp_path)
    first = await service.run_live_probe(
        approval_id=APPROVAL_ID,
        operator_id=EXECUTOR_ID,
        idempotency_key="live-probe-0001",
        request=context["request"],
    )
    replay = await service.run_live_probe(
        approval_id=APPROVAL_ID,
        operator_id=EXECUTOR_ID,
        idempotency_key="live-probe-0001",
        request=context["request"],
    )
    after = sqlite_logical_snapshot(tmp_path)
    await context["client"].aclose()

    assert first.claim_created is True
    assert first.live_probe_executed is True
    assert replay.idempotent_replay is True
    assert replay.live_probe_executed is False
    assert first.record == replay.record
    assert first.record.status == "passed"
    assert first.record.network_call_count == 2
    assert first.record.kubernetes_read_count == 2
    assert first.record.kubernetes_write_count == 0
    assert first.record.patch_request_count == 0
    assert first.record.production_feature_gate_changed is False
    assert [item.method for item in context["calls"]] == ["GET", "GET"]
    assert context["calls"][0].headers["authorization"] == (
        f"Bearer {PREFLIGHT_TOKEN}"
    )
    assert context["calls"][1].headers["authorization"] == (
        f"Bearer {PRODUCTION_TOKEN}"
    )
    assert all(
        item.url.path.endswith(
            "/apis/apps/v1/namespaces/payment/deployments/payment-api"
        )
        for item in context["calls"]
    )
    changed = {
        key
        for key in after
        if before.get(key) != after.get(key)
    }
    assert changed
    assert all(
        key.startswith("production_pilot_go_no_go.db:")
        for key in changed
    )
    serialized = first.record.model_dump_json()
    assert PREFLIGHT_TOKEN not in serialized
    assert PRODUCTION_TOKEN not in serialized
    assert "Authorization" not in serialized


@pytest.mark.asyncio
async def test_live_probe_failure_is_terminal_and_never_retried(
    tmp_path,
):
    context = await go_no_go_environment(
        tmp_path,
        status_code=403,
    )
    first = await context["service"].run_live_probe(
        approval_id=APPROVAL_ID,
        operator_id=EXECUTOR_ID,
        idempotency_key="live-probe-failed-0001",
        request=context["request"],
    )
    replay = await context["service"].run_live_probe(
        approval_id=APPROVAL_ID,
        operator_id=EXECUTOR_ID,
        idempotency_key="live-probe-failed-0001",
        request=context["request"],
    )
    await context["client"].aclose()

    assert first.record.status == "failed"
    assert first.record.blocker_code == (
        "kubernetes_live_probe_unauthorized"
    )
    assert first.record.network_call_count == 1
    assert replay.record == first.record
    assert len(context["calls"]) == 1


@pytest.mark.asyncio
async def test_go_decision_is_independent_durable_and_non_executing(
    tmp_path,
):
    context = await go_no_go_environment(tmp_path)
    probe = await context["service"].run_live_probe(
        approval_id=APPROVAL_ID,
        operator_id=EXECUTOR_ID,
        idempotency_key="live-probe-go-0001",
        request=context["request"],
    )
    request = go_request(probe.record.record_sha256)
    first = await context["service"].decide(
        approval_id=APPROVAL_ID,
        reviewer_operator_id=GO_REVIEWER,
        idempotency_key="go-decision-0001",
        request=request,
    )
    replay = await context["service"].decide(
        approval_id=APPROVAL_ID,
        reviewer_operator_id=GO_REVIEWER,
        idempotency_key="go-decision-0001",
        request=request,
    )
    loaded = await context["service"].get_decision(APPROVAL_ID)
    await context["client"].aclose()

    assert first.created is True
    assert replay.is_replay is True
    assert replay.record == first.record == loaded
    assert first.record.decision == "go"
    assert first.record.allows_guarded_enablement_procedure is True
    assert first.record.feature_gate_changed is False
    assert first.record.kill_switch_changed is False
    assert first.record.kubernetes_network_call_count == 0
    assert first.record.kubernetes_write_count == 0
    assert first.record.action_execution_claim_created is False
    assert first.record.authorizes_action_execution is False
    assert first.record.automatic_enablement_allowed is False
    assert (
        first.record.expires_at - first.record.decided_at
    ).total_seconds() <= 300
    assert len(context["calls"]) == 2

    tampered_probe = probe.record.model_dump()
    tampered_probe["network_call_count"] = 1
    with pytest.raises(ValidationError):
        type(probe.record)(**tampered_probe)

    tampered_decision = first.record.model_dump()
    tampered_decision["reason"] = "Tampered final decision"
    with pytest.raises(ValidationError, match="digest"):
        type(first.record)(**tampered_decision)


@pytest.mark.asyncio
async def test_go_rejects_stale_failed_expired_and_unseparated_reviews(
    tmp_path,
):
    context = await go_no_go_environment(tmp_path)
    probe = await context["service"].run_live_probe(
        approval_id=APPROVAL_ID,
        operator_id=EXECUTOR_ID,
        idempotency_key="live-probe-boundary-0001",
        request=context["request"],
    )

    with pytest.raises(
        ProductionPilotGoNoGoStaleEvidenceError,
        match="changed",
    ):
        await context["service"].decide(
            approval_id=APPROVAL_ID,
            reviewer_operator_id=GO_REVIEWER,
            idempotency_key="go-stale-0001",
            request=go_request("0" * 64),
        )

    for reviewer in (
        EXECUTOR_ID,
        "pilot-on-call-1",
        "approver-evidence-1",
        "approver-ceremony-1",
    ):
        with pytest.raises(
            ProductionPilotGoNoGoError,
        ):
            await context["service"].decide(
                approval_id=APPROVAL_ID,
                reviewer_operator_id=reviewer,
                idempotency_key=f"go-reviewer-{reviewer}",
                request=go_request(probe.record.record_sha256),
            )

    context["environment"]["clock"].set(
        NOW + timedelta(minutes=9)
    )
    with pytest.raises(
        ProductionPilotGoNoGoError,
        match="expired",
    ):
        await context["service"].decide(
            approval_id=APPROVAL_ID,
            reviewer_operator_id=GO_REVIEWER,
            idempotency_key="go-expired-0001",
            request=go_request(probe.record.record_sha256),
        )
    await context["client"].aclose()


@pytest.mark.asyncio
async def test_no_go_can_safely_close_a_failed_probe(
    tmp_path,
):
    context = await go_no_go_environment(
        tmp_path,
        status_code=401,
    )
    probe = await context["service"].run_live_probe(
        approval_id=APPROVAL_ID,
        operator_id=EXECUTOR_ID,
        idempotency_key="live-probe-no-go-0001",
        request=context["request"],
    )
    decision = await context["service"].decide(
        approval_id=APPROVAL_ID,
        reviewer_operator_id=GO_REVIEWER,
        idempotency_key="no-go-decision-0001",
        request=go_request(
            probe.record.record_sha256,
            decision="no_go",
            reason="Production credential validation failed",
            monitoring_owner_confirmed=False,
        ),
    )
    await context["client"].aclose()

    assert decision.record.decision == "no_go"
    assert decision.record.expires_at is None
    assert decision.record.allows_guarded_enablement_procedure is False
    assert decision.record.kubernetes_write_count == 0


def test_live_probe_factory_is_disabled_before_secret_or_file_access():
    calls = []
    result = create_production_pilot_live_readiness_probe(
        environment={},
        preflight_token_file_reader=lambda path: calls.append(path),
        production_token_file_reader=lambda path: calls.append(path),
    )
    assert result is None
    assert calls == []


def test_live_probe_factory_requires_gate_and_separate_credentials():
    disabled_execution = execution_config(enabled=False)
    base = {
        "KUBERNETES_PRODUCTION_LIVE_PROBE_ENABLED": "true",
        "KUBERNETES_PRODUCTION_LIVE_PROBE_ACKNOWLEDGEMENT": (
            PRODUCTION_PILOT_LIVE_PROBE_GATE_ACKNOWLEDGEMENT
        ),
        "K8S_PREFLIGHT_TOKEN": PREFLIGHT_TOKEN,
        "K8S_PRODUCTION_EXECUTION_TOKEN": PRODUCTION_TOKEN,
    }
    probe = create_production_pilot_live_readiness_probe(
        preflight_config=preflight_config(),
        execution_config=disabled_execution,
        environment=base,
        client=object(),
        injected_client_tls_verified=True,
    )
    assert probe is not None

    with pytest.raises(
        ProductionPilotLiveProbeFactoryError,
        match="acknowledgement",
    ):
        create_production_pilot_live_readiness_probe(
            preflight_config=preflight_config(),
            execution_config=disabled_execution,
            environment={
                **base,
                "KUBERNETES_PRODUCTION_LIVE_PROBE_ACKNOWLEDGEMENT": (
                    "ACKNOWLEDGED"
                ),
            },
        )

    with pytest.raises(
        ProductionPilotLiveProbeFactoryError,
        match="must differ",
    ):
        create_production_pilot_live_readiness_probe(
            preflight_config=preflight_config(),
            execution_config=disabled_execution,
            environment={
                **base,
                "K8S_PRODUCTION_EXECUTION_TOKEN": PREFLIGHT_TOKEN,
            },
        )

    with pytest.raises(
        ProductionPilotLiveProbeFactoryError,
        match="must remain disabled",
    ):
        create_production_pilot_live_readiness_probe(
            preflight_config=preflight_config(),
            execution_config=(
                KubernetesProductionExecutionConfig(
                    enabled=True,
                    write_acknowledgement=(
                        KUBERNETES_PRODUCTION_WRITE_ACKNOWLEDGEMENT
                    ),
                    bearer_token_env=(
                        "K8S_PRODUCTION_EXECUTION_TOKEN"
                    ),
                )
            ),
            environment=base,
        )


def test_go_request_and_records_are_tamper_evident():
    with pytest.raises(ValidationError, match="every human check"):
        go_request(
            "a" * 64,
            monitoring_owner_confirmed=False,
        )


@pytest.mark.asyncio
async def test_cross_instance_probe_claim_and_decision_are_atomic(
    tmp_path,
):
    context = await go_no_go_environment(tmp_path)
    second_store = ProductionPilotGoNoGoStore(
        tmp_path / "production_pilot_go_no_go.db"
    )
    second_service = ProductionPilotGoNoGoService(
        store=second_store,
        live_probe=context["service"].live_probe,
        final_handoff_service=(
            context["service"].final_handoff_service
        ),
        artifact_service=context["environment"]["artifact"],
        pilot_control=context["environment"]["control"],
        clock=context["environment"]["clock"],
    )
    results = await asyncio.gather(
        context["service"].run_live_probe(
            approval_id=APPROVAL_ID,
            operator_id=EXECUTOR_ID,
            idempotency_key="live-probe-concurrent-0001",
            request=context["request"],
        ),
        second_service.run_live_probe(
            approval_id=APPROVAL_ID,
            operator_id=EXECUTOR_ID,
            idempotency_key="live-probe-concurrent-0001",
            request=context["request"],
        ),
    )
    await context["client"].aclose()

    assert sum(item.claim_created for item in results) == 1
    assert sum(item.live_probe_executed for item in results) == 1
    assert len(context["calls"]) == 2
    assert results[0].record.probe_id == results[1].record.probe_id
