import json

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from services.agent_runtime.app.action.production_pilot_go_no_go_models import (
    PRODUCTION_PILOT_GO_NO_GO_ACKNOWLEDGEMENT,
)
from services.agent_runtime.app.runtime.runtime import AgentRuntime
from services.agent_runtime.app.security.models import OperatorRole
from services.agent_runtime.tests.api_security_support import (
    wire_api_test_security,
)
from services.agent_runtime.tests.production_action_expiry_support import (
    APPROVAL_ID,
)
from services.agent_runtime.tests.test_production_pilot_go_no_go import (
    GO_REVIEWER,
    PRODUCTION_TOKEN,
    PREFLIGHT_TOKEN,
    go_no_go_environment,
)
from services.agent_runtime.tests.test_production_pilot_pre_enable_evidence import (
    EXECUTOR_ID,
    sqlite_logical_snapshot,
)


LIVE_PROBE_PATH = (
    f"/production-actions/{APPROVAL_ID}"
    "/pilot-live-readiness-probe"
)
DECISION_PATH = (
    f"/production-actions/{APPROVAL_ID}"
    "/pilot-go-no-go-decision"
)


def api_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


def live_probe_body(context: dict) -> dict:
    return context["request"].model_dump(mode="json")


def decision_body(
    probe_record_sha256: str,
    *,
    decision: str = "go",
    reason: str = "Final bounded OOMKilled Pilot review passed",
) -> dict[str, object]:
    return {
        "expected_probe_record_sha256": probe_record_sha256,
        "decision": decision,
        "reason": reason,
        "live_probe_reviewed": True,
        "monitoring_owner_confirmed": True,
        "rollback_owner_confirmed": True,
        "reconciliation_owner_confirmed": True,
        "controlled_change_window_confirmed": True,
        "acknowledgement": (
            PRODUCTION_PILOT_GO_NO_GO_ACKNOWLEDGEMENT
        ),
    }


@pytest_asyncio.fixture
async def api_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    context = await go_no_go_environment(tmp_path)

    from services.agent_runtime.app.api import runtime as api_module

    runtime = AgentRuntime()
    runtime.production_pilot_go_no_go = context["service"]
    security = wire_api_test_security(
        monkeypatch,
        api_module,
        runtime,
    )
    app = FastAPI()
    app.include_router(api_module.router)
    try:
        yield app, runtime, security, context
    finally:
        await context["client"].aclose()


@pytest.mark.asyncio
async def test_api_live_probe_and_go_decision_are_exactly_once_and_bounded(
    api_environment,
):
    app, _, security, context = api_environment
    assert security.principal_id(OperatorRole.EXECUTOR) == EXECUTOR_ID
    assert security.principal_id(OperatorRole.ADMIN) == GO_REVIEWER
    executor_headers = security.headers(
        OperatorRole.EXECUTOR,
        include_operator_id=True,
        idempotency_key="api-live-probe-0001",
    )

    async with api_client(app) as client:
        first = await client.post(
            LIVE_PROBE_PATH,
            json=live_probe_body(context),
            headers=executor_headers,
        )
        replay = await client.post(
            LIVE_PROBE_PATH,
            json=live_probe_body(context),
            headers=executor_headers,
        )
        assert first.status_code == 200
        assert replay.status_code == 200
        probe = first.json()["probe"]
        decision_headers = security.headers(
            OperatorRole.ADMIN,
            include_operator_id=True,
            idempotency_key="api-go-decision-0001",
        )
        decision = await client.post(
            DECISION_PATH,
            json=decision_body(probe["record_sha256"]),
            headers=decision_headers,
        )
        decision_replay = await client.post(
            DECISION_PATH,
            json=decision_body(probe["record_sha256"]),
            headers=decision_headers,
        )
        query = await client.get(
            DECISION_PATH,
            headers=security.headers(OperatorRole.VIEWER),
        )

    assert first.json()["claim_created"] is True
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["live_probe_executed"] is False
    assert first.json()["probe"] == replay.json()["probe"]
    assert probe["status"] == "passed"
    assert probe["network_call_count"] == 2
    assert probe["kubernetes_write_count"] == 0
    assert len(context["calls"]) == 2

    assert decision.status_code == 200
    assert decision_replay.status_code == 200
    assert query.status_code == 200
    pack = decision.json()["decision"]
    assert pack == query.json()["decision"]
    assert pack == decision_replay.json()["decision"]
    assert pack["decision"] == "go"
    assert pack["reviewer_operator_id"] == GO_REVIEWER
    assert pack["allows_guarded_enablement_procedure"] is True
    assert pack["authorizes_action_execution"] is False
    assert pack["feature_gate_changed"] is False
    assert pack["kubernetes_write_count"] == 0
    assert pack["action_execution_claim_created"] is False

    serialized = json.dumps(
        {
            "probe": first.json(),
            "decision": decision.json(),
            "query": query.json(),
        },
        sort_keys=True,
    )
    for forbidden in (
        PREFLIGHT_TOKEN,
        PRODUCTION_TOKEN,
        "Authorization",
        "idempotency_key",
        "request_sha256",
        "handoff_request",
        "canonical_patch",
        "workload_uid",
        "resourceVersion",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_api_live_probe_rbac_spoofing_and_domain_binding_are_fail_closed(
    api_environment,
    tmp_path,
):
    app, _, security, context = api_environment
    body = live_probe_body(context)
    before = sqlite_logical_snapshot(tmp_path)

    async with api_client(app) as client:
        anonymous = await client.post(
            LIVE_PROBE_PATH,
            json=body,
            headers={
                "X-Operator-ID": EXECUTOR_ID,
                "Idempotency-Key": "anonymous-live-probe",
            },
        )
        viewer = await client.post(
            LIVE_PROBE_PATH,
            json=body,
            headers=security.headers(
                OperatorRole.VIEWER,
                include_operator_id=True,
                idempotency_key="viewer-live-probe",
            ),
        )
        spoofed = await client.post(
            LIVE_PROBE_PATH,
            json=body,
            headers={
                **security.headers(
                    OperatorRole.EXECUTOR,
                    idempotency_key="spoofed-live-probe",
                ),
                "X-Operator-ID": "spoofed-admin",
            },
        )
        admin = await client.post(
            LIVE_PROBE_PATH,
            json=body,
            headers=security.headers(
                OperatorRole.ADMIN,
                include_operator_id=True,
                idempotency_key="admin-live-probe",
            ),
        )

    after = sqlite_logical_snapshot(tmp_path)
    assert anonymous.status_code == 401
    assert viewer.status_code == 403
    assert spoofed.status_code == 403
    assert admin.status_code == 409
    assert context["calls"] == []
    assert before == after


@pytest.mark.asyncio
async def test_api_failed_probe_allows_no_go_but_blocks_go(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)
    context = await go_no_go_environment(tmp_path, status_code=403)

    from services.agent_runtime.app.api import runtime as api_module

    runtime = AgentRuntime()
    runtime.production_pilot_go_no_go = context["service"]
    security = wire_api_test_security(
        monkeypatch,
        api_module,
        runtime,
    )
    app = FastAPI()
    app.include_router(api_module.router)
    try:
        async with api_client(app) as client:
            probe_response = await client.post(
                LIVE_PROBE_PATH,
                json=live_probe_body(context),
                headers=security.headers(
                    OperatorRole.EXECUTOR,
                    include_operator_id=True,
                    idempotency_key="api-failed-live-probe",
                ),
            )
            probe = probe_response.json()["probe"]
            go = await client.post(
                DECISION_PATH,
                json=decision_body(probe["record_sha256"]),
                headers=security.headers(
                    OperatorRole.ADMIN,
                    include_operator_id=True,
                    idempotency_key="api-failed-go",
                ),
            )
            no_go = await client.post(
                DECISION_PATH,
                json=decision_body(
                    probe["record_sha256"],
                    decision="no_go",
                    reason="Production credential validation failed",
                ),
                headers=security.headers(
                    OperatorRole.ADMIN,
                    include_operator_id=True,
                    idempotency_key="api-failed-no-go",
                ),
            )
    finally:
        await context["client"].aclose()

    assert probe_response.status_code == 200
    assert probe["status"] == "failed"
    assert len(context["calls"]) == 1
    assert go.status_code == 409
    assert no_go.status_code == 200
    assert no_go.json()["decision"]["decision"] == "no_go"
    assert no_go.json()["decision"]["expires_at"] is None
    assert (
        no_go.json()["decision"][
            "allows_guarded_enablement_procedure"
        ]
        is False
    )


@pytest.mark.asyncio
async def test_api_go_no_go_failures_are_sanitized(
    api_environment,
):
    app, runtime, security, context = api_environment

    class ExplodingService:
        async def run_live_probe(self, **kwargs):
            raise RuntimeError(
                "secret=production-token internal/credential/path"
            )

        async def decide(self, **kwargs):
            raise RuntimeError(
                "secret=production-token internal/credential/path"
            )

        async def get_decision(self, *args, **kwargs):
            raise RuntimeError(
                "secret=production-token internal/credential/path"
            )

    runtime.production_pilot_go_no_go = ExplodingService()
    async with api_client(app) as client:
        probe = await client.post(
            LIVE_PROBE_PATH,
            json=live_probe_body(context),
            headers=security.headers(
                OperatorRole.EXECUTOR,
                include_operator_id=True,
                idempotency_key="api-exploding-probe",
            ),
        )
        decision = await client.post(
            DECISION_PATH,
            json=decision_body("a" * 64),
            headers=security.headers(
                OperatorRole.ADMIN,
                include_operator_id=True,
                idempotency_key="api-exploding-decision",
            ),
        )
        query = await client.get(
            DECISION_PATH,
            headers=security.headers(OperatorRole.VIEWER),
        )

    assert {probe.status_code, decision.status_code, query.status_code} == {
        503
    }
    serialized = (probe.text + decision.text + query.text).lower()
    assert "production-token" not in serialized
    assert "credential/path" not in serialized
    assert "secret=" not in serialized
