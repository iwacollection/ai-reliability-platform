import json

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from services.agent_runtime.app.action.production_pilot_final_handoff import (
    PRODUCTION_PILOT_FINAL_HANDOFF_ACKNOWLEDGEMENT,
)
from services.agent_runtime.app.runtime.runtime import AgentRuntime
from services.agent_runtime.app.security.models import OperatorRole
from services.agent_runtime.tests.api_security_support import (
    wire_api_test_security,
)
from services.agent_runtime.tests.production_action_expiry_support import (
    APPROVAL_ID,
)
from services.agent_runtime.tests.test_production_pilot_final_handoff import (
    handoff_service,
)
from services.agent_runtime.tests.test_production_pilot_pre_enable_evidence import (
    EXECUTOR_ID,
    pre_enable_environment,
    sqlite_logical_snapshot,
)


HANDOFF_PATH = (
    f"/production-actions/{APPROVAL_ID}"
    "/pilot-final-handoff-rehearsal"
)


def api_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


def handoff_body(evidence_sha256: str) -> dict[str, object]:
    return {
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


@pytest_asyncio.fixture
async def api_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    evidence_environment = await pre_enable_environment(tmp_path)
    evidence = await evidence_environment["service"].get(APPROVAL_ID)
    assert evidence is not None

    from services.agent_runtime.app.api import runtime as api_module

    runtime = AgentRuntime()
    runtime.production_pilot_final_handoff_rehearsal = handoff_service(
        evidence_environment,
        reference_probe=lambda kind, reference: True,
    )
    security = wire_api_test_security(
        monkeypatch,
        api_module,
        runtime,
    )
    app = FastAPI()
    app.include_router(api_module.router)
    return app, runtime, security, evidence_environment, evidence


@pytest.mark.asyncio
async def test_api_final_handoff_is_bounded_zero_write_and_replay_safe(
    api_environment,
    tmp_path,
):
    app, _, security, _, evidence = api_environment
    headers = security.headers(
        OperatorRole.EXECUTOR,
        include_operator_id=True,
    )
    assert security.principal_id(OperatorRole.EXECUTOR) == EXECUTOR_ID
    before = sqlite_logical_snapshot(tmp_path)

    async with api_client(app) as client:
        first = await client.post(
            HANDOFF_PATH,
            json=handoff_body(evidence.evidence_sha256),
            headers=headers,
        )
        replay = await client.post(
            HANDOFF_PATH,
            json=handoff_body(evidence.evidence_sha256),
            headers=headers,
        )

    after = sqlite_logical_snapshot(tmp_path)
    assert first.status_code == 200
    assert first.json() == replay.json()
    body = first.json()
    report = body["rehearsal"]
    assert body["read_only"] is True
    assert body["zero_write"] is True
    assert report["passed"] is True
    assert report["operator_id"] == EXECUTOR_ID
    assert report["security_route_count"] == 22
    assert report["storage_write_count"] == 0
    assert report["network_call_count"] == 0
    assert report["kubernetes_call_count"] == 0
    assert report["production_executor_call_count"] == 0
    assert report["verification_call_count"] == 0
    assert report["credential_content_read_count"] == 0
    assert report["tls_handshake_performed"] is False
    assert report["authorizes_feature_enablement"] is False
    assert report["authorizes_execution"] is False
    assert before == after

    serialized = json.dumps(body, sort_keys=True)
    for forbidden in (
        "K8S_PREFLIGHT_TOKEN",
        "K8S_PRODUCTION_EXECUTION_TOKEN",
        "bearer_token",
        "resourceVersion",
        "workload_uid",
        "canonical_patch",
        "Authorization",
        "Idempotency-Key",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_api_final_handoff_enforces_rbac_spoofing_and_executor_binding(
    api_environment,
):
    app, _, security, _, evidence = api_environment
    body = handoff_body(evidence.evidence_sha256)
    async with api_client(app) as client:
        anonymous = await client.post(
            HANDOFF_PATH,
            json=body,
            headers={"X-Operator-ID": EXECUTOR_ID},
        )
        viewer = await client.post(
            HANDOFF_PATH,
            json=body,
            headers=security.headers(
                OperatorRole.VIEWER,
                include_operator_id=True,
            ),
        )
        spoofed = await client.post(
            HANDOFF_PATH,
            json=body,
            headers={
                **security.headers(OperatorRole.EXECUTOR),
                "X-Operator-ID": "spoofed-admin",
            },
        )
        admin = await client.post(
            HANDOFF_PATH,
            json=body,
            headers=security.headers(
                OperatorRole.ADMIN,
                include_operator_id=True,
            ),
        )

    assert anonymous.status_code == 401
    assert viewer.status_code == 403
    assert spoofed.status_code == 403
    assert admin.status_code == 409


@pytest.mark.asyncio
async def test_api_final_handoff_stale_and_failure_boundaries_are_sanitized(
    api_environment,
    tmp_path,
):
    app, runtime, security, _, _ = api_environment
    headers = security.headers(
        OperatorRole.EXECUTOR,
        include_operator_id=True,
    )
    before = sqlite_logical_snapshot(tmp_path)

    async with api_client(app) as client:
        stale = await client.post(
            HANDOFF_PATH,
            json=handoff_body("0" * 64),
            headers=headers,
        )

        class ExplodingService:
            async def rehearse(self, **kwargs):
                raise RuntimeError(
                    "secret=production-writer-token internal/path"
                )

        runtime.production_pilot_final_handoff_rehearsal = (
            ExplodingService()
        )
        failed = await client.post(
            HANDOFF_PATH,
            json=handoff_body("a" * 64),
            headers=headers,
        )

    after = sqlite_logical_snapshot(tmp_path)
    assert stale.status_code == 409
    assert failed.status_code == 503
    assert "secret" not in failed.text.lower()
    assert "token" not in failed.text.lower()
    assert "internal/path" not in failed.text
    assert before == after
