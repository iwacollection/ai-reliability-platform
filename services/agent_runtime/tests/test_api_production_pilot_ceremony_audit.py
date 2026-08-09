import pytest

from services.agent_runtime.app.security.models import (
    OperatorRole,
)
from services.agent_runtime.tests.production_action_expiry_support import (
    APPROVAL_ID,
)
from services.agent_runtime.tests.test_api_kubernetes_production_resume import (
    activate_ceremony,
    api_client,
    approve,
    prepared_api,
    successful_outcome,
)


@pytest.mark.asyncio
async def test_workflow_queries_expose_read_only_ceremony_recovery_states(
    monkeypatch,
    tmp_path,
):
    (
        app,
        runtime,
        security,
        executor,
        collector,
        record,
        _,
        incident,
    ) = await prepared_api(
        monkeypatch,
        tmp_path,
        outcome=successful_outcome(),
    )
    workflow_path = (
        f"/workflows/approvals/{APPROVAL_ID}"
    )
    incident_path = (
        f"/incidents/{incident.id}/workflows"
    )
    viewer_headers = security.headers(
        OperatorRole.VIEWER
    )

    async with api_client(app) as client:
        await approve(
            client,
            security,
        )
        await activate_ceremony(
            client,
            security,
            runtime,
        )

        ready_response = await client.get(
            workflow_path,
            headers=viewer_headers,
        )
        assert ready_response.status_code == 200
        ready_audit = ready_response.json()[
            "production_pilot_ceremony_audit"
        ]
        assert ready_audit[
            "recovery_state"
        ] == "ready_for_first_resume"
        assert ready_audit[
            "manual_reconciliation_required"
        ] is False

        approval = await runtime.approval.get(
            APPROVAL_ID
        )
        assert approval is not None
        claim = await (
            runtime.action_execution_service.claim(
                approval_id=approval.id,
                incident_id=approval.incident_id,
                operator_id=(
                    security.principal_id(
                        OperatorRole.EXECUTOR
                    )
                ),
                idempotency_key=(
                    "execute-production-api-0001"
                ),
                action=approval.action,
                metadata=(
                    runtime.action_runtime
                    ._execution_claim_metadata(
                        approval
                    )
                ),
            )
        )
        assert claim.created is True

        claim_response = await client.get(
            workflow_path,
            headers=viewer_headers,
        )
        claim_body = claim_response.json()
        claim_audit = claim_body[
            "production_pilot_ceremony_audit"
        ]
        assert claim_body[
            "manual_reconciliation_required"
        ] is True
        assert claim_audit[
            "recovery_state"
        ] == "claim_not_activated"
        assert claim_audit[
            "automatic_resume_allowed"
        ] is False

        activation = await (
            runtime.production_pilot_ceremony
            .activate_for_execution(
                execution=claim.execution,
                preflight_record=record,
            )
        )
        assert activation.applied is True

        activated_response = await client.get(
            workflow_path,
            headers=viewer_headers,
        )
        activated_replay = await client.get(
            workflow_path,
            headers=viewer_headers,
        )
        incident_response = await client.get(
            incident_path,
            headers=viewer_headers,
        )

    assert activated_response.status_code == 200
    assert activated_replay.json() == activated_response.json()
    assert incident_response.status_code == 200
    activated_body = activated_response.json()
    activated_audit = activated_body[
        "production_pilot_ceremony_audit"
    ]
    assert activated_body[
        "workflow_status"
    ] == "action_running"
    assert activated_body[
        "manual_reconciliation_required"
    ] is True
    assert activated_audit[
        "recovery_state"
    ] == "activated_outcome_unconfirmed"
    assert activated_audit[
        "binding_consistent"
    ] is True
    assert activated_audit[
        "clock_consistent"
    ] is True
    assert activated_audit[
        "execution_id"
    ] == str(claim.execution.id)
    assert activated_audit[
        "automatic_resume_allowed"
    ] is False
    assert activated_audit[
        "operator_guidance"
    ] == [
        "engage_kill_switch",
        "do_not_retry_resume",
        "inspect_deployment_state_read_only",
        "reconcile_existing_action_execution",
        "start_verification_only_after_confirmed_success",
    ]
    assert incident_response.json()[
        "workflows"
    ][0][
        "production_pilot_ceremony_audit"
    ] == activated_audit

    text = str(
        activated_audit
    ).lower()
    for forbidden in (
        "patch_json",
        "workload_uid",
        "resource_version",
        "idempotency_key",
        "authorization",
        "credential",
        "bearer",
        "token",
    ):
        assert forbidden not in text

    persisted = await (
        runtime.action_execution_service
        .get_by_approval(
            APPROVAL_ID
        )
    )
    assert persisted is not None
    assert persisted.status.value == "running"
    persisted_ceremony = await (
        runtime.production_pilot_ceremony
        .get_by_approval(
            APPROVAL_ID
        )
    )
    assert persisted_ceremony == activation.record
    assert executor.calls == 0
    assert collector.calls == 0
    assert await (
        runtime.production_pilot_budget_service.get(
            "oom-api-resume-test"
        )
    ) is None


@pytest.mark.asyncio
async def test_configured_audit_marks_missing_ceremony_inconsistent(
    monkeypatch,
    tmp_path,
):
    (
        app,
        runtime,
        security,
        _,
        _,
        _,
        _,
        incident,
    ) = await prepared_api(
        monkeypatch,
        tmp_path,
        outcome=successful_outcome(),
    )

    async with api_client(app) as client:
        await approve(
            client,
            security,
        )
        approval = await runtime.approval.get(
            APPROVAL_ID
        )
        assert approval is not None
        await runtime.action_execution_service.claim(
            approval_id=approval.id,
            incident_id=approval.incident_id,
            operator_id=(
                security.principal_id(
                    OperatorRole.EXECUTOR
                )
            ),
            idempotency_key=(
                "execute-missing-ceremony-0001"
            ),
            action=approval.action,
            metadata=(
                runtime.action_runtime
                ._execution_claim_metadata(
                    approval
                )
            ),
        )
        response = await client.get(
            (
                f"/workflows/approvals/{APPROVAL_ID}"
            ),
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["workflow_status"] == "inconsistent"
    assert body[
        "manual_reconciliation_required"
    ] is True
    assert body[
        "production_pilot_ceremony_audit"
    ] is None
    assert (
        "production_execution_missing_pilot_ceremony"
        in body["consistency"]["issues"]
    )
    assert incident.status.value == "confirmed"


@pytest.mark.asyncio
async def test_ceremony_audit_store_failure_returns_503_without_execution(
    monkeypatch,
    tmp_path,
):
    (
        app,
        runtime,
        security,
        executor,
        collector,
        _,
        _,
        _,
    ) = await prepared_api(
        monkeypatch,
        tmp_path,
        outcome=successful_outcome(),
    )

    async def unavailable(_):
        raise RuntimeError(
            "secret backend detail"
        )

    runtime.production_pilot_ceremony.get_by_approval = (
        unavailable
    )

    async with api_client(app) as client:
        response = await client.get(
            (
                f"/workflows/approvals/{APPROVAL_ID}"
            ),
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "Production Pilot Ceremony audit is unavailable"
        ),
    }
    assert "secret" not in response.text.lower()
    assert executor.calls == 0
    assert collector.calls == 0
    assert await (
        runtime.action_execution_service
        .get_by_approval(
            APPROVAL_ID
        )
    ) is None
