import pytest
from pydantic import ValidationError

from services.agent_runtime.app.action.production_pilot import (
    KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED,
)
from services.agent_runtime.app.action.production_pilot_final_handoff import (
    PRODUCTION_PILOT_FINAL_HANDOFF_ACKNOWLEDGEMENT,
    ProductionPilotFinalHandoffConflictError,
    ProductionPilotFinalHandoffError,
    ProductionPilotFinalHandoffRehearsalService,
    ProductionPilotFinalHandoffReport,
    ProductionPilotFinalHandoffRequest,
)
from services.agent_runtime.tests.production_action_expiry_support import (
    APPROVAL_ID,
)
from services.agent_runtime.tests.test_production_pilot_pre_enable_evidence import (
    EXECUTOR_ID,
    pre_enable_environment,
    sqlite_logical_snapshot,
)
from services.agent_runtime.tests.test_runtime_kubernetes_production_wiring import (
    resolver,
)


def handoff_request(
    evidence_sha256: str,
    **overrides,
) -> ProductionPilotFinalHandoffRequest:
    values = {
        "expected_evidence_sha256": evidence_sha256,
        "expected_pilot_id": "oom-pilot-v1",
        "expected_change_ticket": "CHG-6001",
        "expected_runbook_version": "oom-runbook-v1",
        "deployment_release_sha256": "sha256:" + "d" * 64,
        "on_call_owner_id": "pilot-on-call-1",
        "rollback_owner_id": "pilot-rollback-1",
        "reconciliation_owner_id": "pilot-reconcile-1",
        "deployment_release_evidence_reviewed": True,
        "preflight_credential_reference_reviewed": True,
        "production_credential_reference_reviewed": True,
        "tls_policy_evidence_reviewed": True,
        "security_matrix_evidence_reviewed": True,
        "monitoring_evidence_reviewed": True,
        "rollback_evidence_reviewed": True,
        "reconciliation_evidence_reviewed": True,
        "acknowledgement": (
            PRODUCTION_PILOT_FINAL_HANDOFF_ACKNOWLEDGEMENT
        ),
    }
    values.update(overrides)
    return ProductionPilotFinalHandoffRequest(**values)


def handoff_service(
    environment,
    *,
    reference_probe=lambda kind, reference: True,
    production_executor_configured=False,
    action_runtime_production_executor_configured=False,
    preflight_resolver=None,
) -> ProductionPilotFinalHandoffRehearsalService:
    return ProductionPilotFinalHandoffRehearsalService(
        pilot_control=environment["control"],
        pre_enable_evidence_service=environment["service"],
        preflight_resolver=(
            resolver()
            if preflight_resolver is None
            else preflight_resolver
        ),
        production_executor_configured=(
            production_executor_configured
        ),
        action_runtime_production_executor_configured=(
            action_runtime_production_executor_configured
        ),
        reference_probe=reference_probe,
    )


@pytest.mark.asyncio
async def test_final_handoff_passes_without_writes_or_secret_reads(
    tmp_path,
):
    environment = await pre_enable_environment(tmp_path)
    evidence = await environment["service"].get(APPROVAL_ID)
    assert evidence is not None
    probes: list[tuple[str, str]] = []
    service = handoff_service(
        environment,
        reference_probe=lambda kind, reference: (
            probes.append((kind, reference)) or True
        ),
    )
    request = handoff_request(evidence.evidence_sha256)

    before = sqlite_logical_snapshot(tmp_path)
    first = await service.rehearse(
        approval_id=APPROVAL_ID,
        operator_id=EXECUTOR_ID,
        request=request,
    )
    replay = await service.rehearse(
        approval_id=APPROVAL_ID,
        operator_id=EXECUTOR_ID,
        request=request,
    )
    after = sqlite_logical_snapshot(tmp_path)

    assert first == replay
    assert first.passed is True
    assert first.blockers == ()
    assert first.security_route_count == 22
    assert first.security_role_count == 7
    assert first.credential_references_separate is True
    assert first.credential_content_read_count == 0
    assert first.credential_content_validated is False
    assert first.tls_handshake_performed is False
    assert first.storage_write_count == 0
    assert first.durable_claim_created is False
    assert first.budget_reservation_count == 0
    assert first.network_call_count == 0
    assert first.kubernetes_call_count == 0
    assert first.production_executor_call_count == 0
    assert first.verification_call_count == 0
    assert first.authorizes_feature_enablement is False
    assert first.authorizes_execution is False
    assert before == after
    assert probes == [
        ("environment", "K8S_PREFLIGHT_TOKEN"),
        ("environment", "K8S_PRODUCTION_EXECUTION_TOKEN"),
    ] * 2
    serialized = first.model_dump_json()
    assert "K8S_PREFLIGHT_TOKEN" not in serialized
    assert "K8S_PRODUCTION_EXECUTION_TOKEN" not in serialized
    tampered = first.model_dump(mode="json")
    tampered["deployment_release_sha256"] = "sha256:" + "e" * 64
    with pytest.raises(ValidationError, match="report digest is invalid"):
        ProductionPilotFinalHandoffReport.model_validate(tampered)


@pytest.mark.asyncio
async def test_final_handoff_is_digest_bound_and_exact_executor_only(
    tmp_path,
):
    environment = await pre_enable_environment(tmp_path)
    evidence = await environment["service"].get(APPROVAL_ID)
    assert evidence is not None
    service = handoff_service(environment)

    with pytest.raises(
        ProductionPilotFinalHandoffConflictError,
        match="changed",
    ):
        await service.rehearse(
            approval_id=APPROVAL_ID,
            operator_id=EXECUTOR_ID,
            request=handoff_request("0" * 64),
        )

    with pytest.raises(
        ProductionPilotFinalHandoffError,
        match="exact reviewed Executor",
    ):
        await service.rehearse(
            approval_id=APPROVAL_ID,
            operator_id="test-admin-operator",
            request=handoff_request(evidence.evidence_sha256),
        )


@pytest.mark.asyncio
async def test_final_handoff_fails_closed_on_reference_or_tls_boundary(
    tmp_path,
):
    environment = await pre_enable_environment(tmp_path)
    evidence = await environment["service"].get(APPROVAL_ID)
    assert evidence is not None
    request = handoff_request(evidence.evidence_sha256)

    unavailable = handoff_service(
        environment,
        reference_probe=lambda kind, reference: False,
    )
    unavailable_report = await unavailable.rehearse(
        approval_id=APPROVAL_ID,
        operator_id=EXECUTOR_ID,
        request=request,
    )
    assert unavailable_report.passed is False
    assert "preflight_credential_reference_unavailable" in (
        unavailable_report.blockers
    )
    assert "production_credential_reference_unavailable" in (
        unavailable_report.blockers
    )

    missing_resolver = ProductionPilotFinalHandoffRehearsalService(
        pilot_control=environment["control"],
        pre_enable_evidence_service=environment["service"],
        preflight_resolver=None,
        production_executor_configured=False,
        action_runtime_production_executor_configured=False,
        reference_probe=lambda kind, reference: True,
    )
    missing_report = await missing_resolver.rehearse(
        approval_id=APPROVAL_ID,
        operator_id=EXECUTOR_ID,
        request=request,
    )
    assert missing_report.passed is False
    assert "preflight_runtime_unavailable" in missing_report.blockers
    assert "tls_verification_required" in missing_report.blockers


@pytest.mark.asyncio
async def test_final_handoff_requires_disabled_gate_absent_executors_and_switch(
    tmp_path,
):
    environment = await pre_enable_environment(tmp_path)
    evidence = await environment["service"].get(APPROVAL_ID)
    assert evidence is not None
    request = handoff_request(evidence.evidence_sha256)
    configured = handoff_service(
        environment,
        production_executor_configured=True,
        action_runtime_production_executor_configured=True,
    )
    report = await configured.rehearse(
        approval_id=APPROVAL_ID,
        operator_id=EXECUTOR_ID,
        request=request,
    )
    assert report.passed is False
    assert "production_executor_must_remain_absent" in report.blockers
    assert (
        "action_runtime_production_executor_must_remain_absent"
        in report.blockers
    )

    environment["control"]._switch_reader = (
        lambda _: KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED
    )
    switched_evidence = await environment["service"].get(APPROVAL_ID)
    assert switched_evidence is not None
    switched = await handoff_service(environment).rehearse(
        approval_id=APPROVAL_ID,
        operator_id=EXECUTOR_ID,
        request=handoff_request(
            switched_evidence.evidence_sha256
        ),
    )
    assert switched.passed is False
    assert "kill_switch_must_remain_engaged" in switched.blockers


def test_final_handoff_request_validates_acknowledgement_and_owners():
    with pytest.raises(ValidationError, match="acknowledgement"):
        handoff_request("a" * 64, acknowledgement="ACKNOWLEDGED")

    with pytest.raises(ValidationError, match="owners must be distinct"):
        handoff_request(
            "a" * 64,
            rollback_owner_id="pilot-on-call-1",
        )
