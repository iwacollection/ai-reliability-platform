import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI

from services.agent_runtime.app.action.production_pilot_pre_enable_evidence import (
    PRODUCTION_PILOT_PRE_ENABLE_SIGN_OFF_ACKNOWLEDGEMENT,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)
from services.agent_runtime.app.security.models import (
    OperatorRole,
)
from services.agent_runtime.tests.api_security_support import (
    wire_api_test_security,
)
from services.agent_runtime.tests.production_action_expiry_support import (
    APPROVAL_ID,
)
from services.agent_runtime.tests.test_production_pilot_pre_enable_evidence import (
    EXECUTOR_ID,
    pre_enable_environment,
    sqlite_logical_snapshot,
)


EVIDENCE_PATH = (
    f"/production-actions/{APPROVAL_ID}"
    "/pilot-pre-enable-evidence"
)
SIGN_OFF_PATH = (
    f"/production-actions/{APPROVAL_ID}"
    "/pilot-pre-enable-sign-off"
)


def api_client(
    app: FastAPI,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app
        ),
        base_url="http://test",
    )


def sign_off_body(
    evidence_sha256: str,
) -> dict[str, str]:
    return {
        "expected_evidence_sha256": (
            evidence_sha256
        ),
        "acknowledgement": (
            PRODUCTION_PILOT_PRE_ENABLE_SIGN_OFF_ACKNOWLEDGEMENT
        ),
    }


@pytest_asyncio.fixture
async def api_environment(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(
        tmp_path
    )
    evidence_environment = (
        await pre_enable_environment(
            tmp_path
        )
    )

    from services.agent_runtime.app.api import (
        runtime as api_module,
    )

    runtime = AgentRuntime()
    runtime.production_pilot_pre_enable_evidence = (
        evidence_environment["service"]
    )
    security = wire_api_test_security(
        monkeypatch,
        api_module,
        runtime,
    )
    app = FastAPI()
    app.include_router(
        api_module.router
    )
    return (
        app,
        runtime,
        security,
        evidence_environment,
    )


@pytest.mark.asyncio
async def test_api_returns_bounded_evidence_and_zero_write_sign_off_replay(
    api_environment,
    tmp_path,
):
    app, _, security, _ = api_environment
    viewer_headers = security.headers(
        OperatorRole.VIEWER
    )
    executor_headers = security.headers(
        OperatorRole.EXECUTOR,
        include_operator_id=True,
    )
    assert security.principal_id(
        OperatorRole.EXECUTOR
    ) == EXECUTOR_ID

    async with api_client(app) as client:
        evidence_response = await client.get(
            EVIDENCE_PATH,
            headers=viewer_headers,
        )
        assert evidence_response.status_code == 200
        evidence = evidence_response.json()[
            "evidence"
        ]
        before = sqlite_logical_snapshot(
            tmp_path
        )
        first = await client.post(
            SIGN_OFF_PATH,
            json=sign_off_body(
                evidence["evidence_sha256"]
            ),
            headers=executor_headers,
        )
        replay = await client.post(
            SIGN_OFF_PATH,
            json=sign_off_body(
                evidence["evidence_sha256"]
            ),
            headers=executor_headers,
        )
        after = sqlite_logical_snapshot(
            tmp_path
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json() == replay.json()
    body = first.json()
    sign_off = body["sign_off"]
    assert body["read_only"] is True
    assert body["zero_write"] is True
    assert sign_off["operator_id"] == EXECUTOR_ID
    assert sign_off["persisted"] is False
    assert sign_off["storage_write_count"] == 0
    assert sign_off["durable_claim_created"] is False
    assert sign_off["budget_reservation_count"] == 0
    assert sign_off["external_call_count"] == 0
    assert sign_off["kubernetes_call_count"] == 0
    assert sign_off["production_executor_call_count"] == 0
    assert sign_off["verification_call_count"] == 0
    assert sign_off["real_write_attempted"] is False
    assert sign_off["authorizes_enablement"] is False
    assert sign_off["authorizes_execution"] is False
    assert before == after


@pytest.mark.asyncio
async def test_api_sign_off_rbac_spoofing_and_exact_executor_boundary(
    api_environment,
):
    app, _, security, _ = api_environment
    async with api_client(app) as client:
        evidence_response = await client.get(
            EVIDENCE_PATH,
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )
        digest = evidence_response.json()[
            "evidence"
        ]["evidence_sha256"]
        anonymous = await client.post(
            SIGN_OFF_PATH,
            json=sign_off_body(digest),
            headers={
                "X-Operator-ID": EXECUTOR_ID,
            },
        )
        viewer = await client.post(
            SIGN_OFF_PATH,
            json=sign_off_body(digest),
            headers=security.headers(
                OperatorRole.VIEWER,
                include_operator_id=True,
            ),
        )
        spoofed = await client.post(
            SIGN_OFF_PATH,
            json=sign_off_body(digest),
            headers={
                **security.headers(
                    OperatorRole.EXECUTOR
                ),
                "X-Operator-ID": "spoofed-admin",
            },
        )
        admin = await client.post(
            SIGN_OFF_PATH,
            json=sign_off_body(digest),
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
async def test_api_rejects_stale_digest_without_side_effects(
    api_environment,
    tmp_path,
):
    app, _, security, _ = api_environment
    before = sqlite_logical_snapshot(
        tmp_path
    )

    async with api_client(app) as client:
        response = await client.post(
            SIGN_OFF_PATH,
            json=sign_off_body(
                "0" * 64
            ),
            headers=security.headers(
                OperatorRole.EXECUTOR,
                include_operator_id=True,
            ),
        )

    after = sqlite_logical_snapshot(
        tmp_path
    )
    assert response.status_code == 409
    assert before == after


@pytest.mark.asyncio
async def test_api_evidence_is_bounded_and_omits_sensitive_material(
    api_environment,
):
    app, _, security, _ = api_environment

    async with api_client(app) as client:
        response = await client.get(
            EVIDENCE_PATH,
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert response.status_code == 200
    body = response.json()

    def field_names(value):
        if isinstance(value, dict):
            return {
                *value.keys(),
                *(
                    item
                    for child in value.values()
                    for item in field_names(child)
                ),
            }
        if isinstance(value, list):
            return {
                item
                for child in value
                for item in field_names(child)
            }
        return set()

    fields = {
        item.lower()
        for item in field_names(body)
    }
    for forbidden_name in (
        "patch_json",
        "canonical_patch",
        "workload_uid",
        "resource_version",
        "idempotency_key",
        "authorization",
        "bearer_token",
        "credential_reference",
        "bearer",
        "token",
        "credential",
        "kill_switch_file",
    ):
        assert forbidden_name not in fields

    serialized = response.text.lower()
    assert security.credential(
        OperatorRole.VIEWER
    ).api_key.lower() not in serialized
    for forbidden_value in (
        "test-service-account-token-000001",
        "pilot-switch",
        "30000000-0000-4000-8000-000000000303",
        '\"kind\":\"deployment\"',
    ):
        assert forbidden_value not in serialized


@pytest.mark.asyncio
async def test_api_evidence_failure_is_sanitized_and_fail_closed(
    api_environment,
    monkeypatch,
):
    app, _, security, environment = api_environment
    secret = "secret-kubernetes-credential-value"

    async def broken_get(*args, **kwargs):
        raise RuntimeError(
            secret
        )

    monkeypatch.setattr(
        environment["service"],
        "get",
        broken_get,
    )

    async with api_client(app) as client:
        response = await client.get(
            EVIDENCE_PATH,
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert response.status_code == 503
    assert secret not in response.text


@pytest.mark.asyncio
async def test_api_missing_evidence_is_404_after_authorization(
    api_environment,
):
    app, _, security, _ = api_environment
    path = (
        "/production-actions/missing-approval"
        "/pilot-pre-enable-evidence"
    )

    async with api_client(app) as client:
        response = await client.get(
            path,
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert response.status_code == 404
