from datetime import UTC, datetime

import httpx
import pytest

from services.agent_runtime.app.tools.kubernetes.tool import (
    KubernetesAuthorizationError,
    KubernetesConfigurationError,
    KubernetesOperationNotAllowedError,
    KubernetesQueryError,
    KubernetesResourceNotFoundError,
    KubernetesTool,
    KubernetesToolError,
)


NOW = datetime(
    2026,
    8,
    1,
    8,
    0,
    tzinfo=UTC,
)


KUBERNETES_ENV_NAMES = [
    "KUBERNETES_API_URL",
    "KUBERNETES_SERVICE_HOST",
    "KUBERNETES_SERVICE_PORT",
    "KUBERNETES_SERVICE_PORT_HTTPS",
    "KUBERNETES_TIMEOUT_SECONDS",
    "KUBERNETES_VERIFY_TLS",
    "KUBERNETES_BEARER_TOKEN",
    "KUBERNETES_TOKEN_FILE",
    "KUBERNETES_CA_FILE",
    "KUBERNETES_CLUSTER_NAME",
    "KUBERNETES_ALLOW_DRY_RUN_FALLBACK",
]


def clear_kubernetes_environment(
    monkeypatch,
):
    for name in KUBERNETES_ENV_NAMES:
        monkeypatch.delenv(
            name,
            raising=False,
        )


def pod_payload(
    *,
    phase="Running",
    ready_condition=True,
    scheduled_condition=True,
    container_ready=True,
    restart_count=3,
    state_reason=None,
    last_termination_reason="OOMKilled",
):
    state = (
        {
            "waiting": {
                "reason": state_reason,
            }
        }
        if state_reason
        else {
            "running": {
                "startedAt": (
                    "2026-08-01T07:55:00Z"
                )
            }
        }
    )

    last_state = {}

    if last_termination_reason:
        last_state = {
            "terminated": {
                "reason": (
                    last_termination_reason
                ),
                "finishedAt": (
                    "2026-08-01T07:50:00Z"
                ),
            }
        }

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "payment-api",
            "namespace": "payment",
            "uid": "pod-uid",
            "resourceVersion": "12345",
            "creationTimestamp": (
                "2026-08-01T07:00:00Z"
            ),
            "labels": {
                "app": "payment-api"
            },
        },
        "spec": {
            "nodeName": "worker-1"
        },
        "status": {
            "phase": phase,
            "podIP": "10.0.0.10",
            "hostIP": "10.0.0.1",
            "conditions": [
                {
                    "type": "PodScheduled",
                    "status": (
                        "True"
                        if scheduled_condition
                        else "False"
                    ),
                    "lastTransitionTime": (
                        "2026-08-01T07:00:10Z"
                    ),
                },
                {
                    "type": "Ready",
                    "status": (
                        "True"
                        if ready_condition
                        else "False"
                    ),
                    "reason": (
                        None
                        if ready_condition
                        else "ContainersNotReady"
                    ),
                    "lastTransitionTime": (
                        "2026-08-01T07:55:10Z"
                    ),
                },
            ],
            "containerStatuses": [
                {
                    "name": "payment-api",
                    "ready": container_ready,
                    "restartCount": restart_count,
                    "state": state,
                    "lastState": last_state,
                    "image": "payment-api:v2",
                    "imageID": "sha256:abc",
                }
            ],
        },
    }


async def execute_live(
    handler,
    *,
    action="describe",
    resource="pod",
    target="payment-api",
    namespace="payment",
):
    transport = httpx.MockTransport(
        handler
    )

    async with httpx.AsyncClient(
        transport=transport
    ) as client:
        tool = KubernetesTool(
            api_url="https://kubernetes.test/",
            bearer_token="test-token",
            cluster_name="production-a",
            client=client,
            clock=lambda: NOW,
        )

        return await tool.execute(
            action=action,
            resource=resource,
            target=target,
            namespace=namespace,
        )


@pytest.mark.asyncio
async def test_dry_run_fallback_is_explicitly_untrusted(
    monkeypatch,
):
    clear_kubernetes_environment(
        monkeypatch
    )
    tool = KubernetesTool(
        allow_dry_run_fallback=True,
        clock=lambda: NOW,
    )

    result = await tool.execute(
        action="describe",
        resource="pod",
        target="payment-api",
    )

    assert result["success"] is True
    assert result["source"] == (
        "mock_kubernetes"
    )
    assert result["mode"] == "dry_run"
    assert result["production_signal"] is False
    assert result["observed_at"] == (
        NOW.isoformat()
    )


@pytest.mark.asyncio
async def test_missing_api_can_fail_closed(
    monkeypatch,
):
    clear_kubernetes_environment(
        monkeypatch
    )
    tool = KubernetesTool(
        allow_dry_run_fallback=False
    )

    with pytest.raises(
        KubernetesConfigurationError,
        match="KUBERNETES_API_URL",
    ):
        await tool.execute(
            action="describe",
            resource="pod",
            target="payment-api",
        )


@pytest.mark.asyncio
async def test_mutating_action_is_dry_run_only(
    monkeypatch,
):
    clear_kubernetes_environment(
        monkeypatch
    )
    tool = KubernetesTool(
        allow_dry_run_fallback=True,
        clock=lambda: NOW,
    )

    result = await tool.execute(
        action="delete",
        resource="pod",
        target="payment-api",
    )

    assert result["mode"] == "dry_run"
    assert result["action"] == "delete"
    assert result["production_signal"] is False


@pytest.mark.asyncio
async def test_mutating_action_can_be_rejected(
    monkeypatch,
):
    clear_kubernetes_environment(
        monkeypatch
    )
    tool = KubernetesTool(
        allow_dry_run_fallback=False
    )

    with pytest.raises(
        KubernetesOperationNotAllowedError,
        match="read-only",
    ):
        await tool.execute(
            action="delete",
            resource="pod",
            target="payment-api",
        )


@pytest.mark.asyncio
async def test_unsupported_resource_is_rejected():
    tool = KubernetesTool(
        api_url="https://kubernetes.test"
    )

    with pytest.raises(
        KubernetesOperationNotAllowedError,
        match="Pod evidence only",
    ):
        await tool.execute(
            action="get",
            resource="deployment",
            target="payment-api",
        )


@pytest.mark.asyncio
async def test_live_pod_response_is_normalized():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["authorization"] = (
            request.headers.get(
                "authorization"
            )
        )
        captured["accept"] = (
            request.headers.get("accept")
        )

        return httpx.Response(
            200,
            json=pod_payload(),
        )

    result = await execute_live(
        handler
    )

    assert captured["path"] == (
        "/api/v1/namespaces/payment/"
        "pods/payment-api"
    )
    assert captured["authorization"] == (
        "Bearer test-token"
    )
    assert captured["accept"] == (
        "application/json"
    )
    assert result["success"] is True
    assert result["source"] == "kubernetes"
    assert result["mode"] == "read_only"
    assert result["production_signal"] is True
    assert result["observed_at"] == (
        NOW.isoformat()
    )
    assert result["cluster"] == "production-a"

    data = result["data"]

    assert data["phase"] == "Running"
    assert data["ready"] is True
    assert data["scheduled"] is True
    assert data["oom_killed"] is True
    assert data["node_name"] == "worker-1"
    assert data["pod_ip"] == "10.0.0.10"
    assert data["host_ip"] == "10.0.0.1"

    container = data["containers"][0]

    assert container["ready"] is True
    assert container["restart_count"] == 3
    assert container["state"] == "running"
    assert container["state_reason"] is None
    assert container[
        "last_termination_reason"
    ] == "OOMKilled"


@pytest.mark.asyncio
async def test_not_ready_and_waiting_state_are_normalized():
    def handler(request):
        return httpx.Response(
            200,
            json=pod_payload(
                ready_condition=False,
                container_ready=False,
                restart_count=15,
                state_reason="CrashLoopBackOff",
                last_termination_reason=None,
            ),
        )

    result = await execute_live(
        handler
    )
    data = result["data"]
    container = data["containers"][0]

    assert data["ready"] is False
    assert data["oom_killed"] is False
    assert container["ready"] is False
    assert container["restart_count"] == 15
    assert container["state"] == "waiting"
    assert container["state_reason"] == (
        "CrashLoopBackOff"
    )


@pytest.mark.asyncio
async def test_namespace_and_target_are_url_encoded():
    captured = {}

    def handler(request):
        captured["raw_path"] = (
            request.url.raw_path.decode()
        )
        return httpx.Response(
            200,
            json=pod_payload(),
        )

    await execute_live(
        handler,
        namespace="team/a",
        target="payment/api",
    )

    assert "team%2Fa" in captured["raw_path"]
    assert "payment%2Fapi" in (
        captured["raw_path"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_type", "message"),
    [
        (
            401,
            KubernetesAuthorizationError,
            "authorization failed",
        ),
        (
            403,
            KubernetesAuthorizationError,
            "authorization failed",
        ),
        (
            404,
            KubernetesResourceNotFoundError,
            "not found",
        ),
        (
            500,
            KubernetesQueryError,
            "HTTP 500",
        ),
    ],
)
async def test_http_errors_are_normalized(
    status_code,
    error_type,
    message,
):
    def handler(request):
        return httpx.Response(
            status_code,
            json={
                "kind": "Status",
                "status": "Failure",
            },
        )

    with pytest.raises(
        error_type,
        match=message,
    ):
        await execute_live(handler)


@pytest.mark.asyncio
async def test_timeout_is_normalized():
    def handler(request):
        raise httpx.ReadTimeout(
            "timed out",
            request=request,
        )

    with pytest.raises(
        KubernetesQueryError,
        match="timed out",
    ):
        await execute_live(handler)


@pytest.mark.asyncio
async def test_invalid_json_is_rejected():
    def handler(request):
        return httpx.Response(
            200,
            content=b"not-json",
            headers={
                "content-type": (
                    "application/json"
                )
            },
        )

    with pytest.raises(
        KubernetesQueryError,
        match="invalid JSON",
    ):
        await execute_live(handler)


@pytest.mark.asyncio
async def test_status_failure_is_rejected():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "kind": "Status",
                "status": "Failure",
                "reason": "Forbidden",
            },
        )

    with pytest.raises(
        KubernetesQueryError,
        match="Forbidden",
    ):
        await execute_live(handler)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "kind": "Pod",
                "status": {},
            },
            "metadata is invalid",
        ),
        (
            {
                "kind": "Pod",
                "metadata": {},
            },
            "status is invalid",
        ),
    ],
)
async def test_invalid_pod_structure_is_rejected(
    payload,
    message,
):
    def handler(request):
        return httpx.Response(
            200,
            json=payload,
        )

    with pytest.raises(
        KubernetesQueryError,
        match=message,
    ):
        await execute_live(handler)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {
            "action": "",
            "resource": "pod",
            "target": "payment-api",
            "namespace": "default",
        },
        {
            "action": "get",
            "resource": "",
            "target": "payment-api",
            "namespace": "default",
        },
        {
            "action": "get",
            "resource": "pod",
            "target": "",
            "namespace": "default",
        },
        {
            "action": "get",
            "resource": "pod",
            "target": "payment-api",
            "namespace": "",
        },
    ],
)
async def test_required_arguments_are_validated(
    arguments,
):
    tool = KubernetesTool(
        allow_dry_run_fallback=True
    )

    with pytest.raises(
        KubernetesToolError,
        match="cannot be empty",
    ):
        await tool.execute(
            **arguments
        )


def test_in_cluster_discovery_and_token_file(
    monkeypatch,
    tmp_path,
):
    clear_kubernetes_environment(
        monkeypatch
    )
    token_file = tmp_path / "token"
    token_file.write_text(
        "service-account-token\n",
        encoding="utf-8",
    )

    monkeypatch.setenv(
        "KUBERNETES_SERVICE_HOST",
        "2001:db8::1",
    )
    monkeypatch.setenv(
        "KUBERNETES_SERVICE_PORT_HTTPS",
        "6443",
    )
    monkeypatch.setenv(
        "KUBERNETES_TOKEN_FILE",
        str(token_file),
    )

    tool = KubernetesTool()

    assert tool.in_cluster is True
    assert tool.api_url == (
        "https://[2001:db8::1]:6443"
    )
    assert tool.bearer_token == (
        "service-account-token"
    )


def test_missing_token_file_is_rejected(
    tmp_path,
):
    missing = tmp_path / "missing-token"

    with pytest.raises(
        KubernetesConfigurationError,
        match="could not be read",
    ):
        KubernetesTool(
            api_url="https://kubernetes.test",
            token_file=missing,
        )


def test_environment_configuration(
    monkeypatch,
):
    clear_kubernetes_environment(
        monkeypatch
    )
    monkeypatch.setenv(
        "KUBERNETES_API_URL",
        "https://kubernetes.example/",
    )
    monkeypatch.setenv(
        "KUBERNETES_TIMEOUT_SECONDS",
        "7.5",
    )
    monkeypatch.setenv(
        "KUBERNETES_VERIFY_TLS",
        "false",
    )
    monkeypatch.setenv(
        "KUBERNETES_BEARER_TOKEN",
        "secret-token",
    )
    monkeypatch.setenv(
        "KUBERNETES_CLUSTER_NAME",
        "production-a",
    )
    monkeypatch.setenv(
        "KUBERNETES_ALLOW_DRY_RUN_FALLBACK",
        "false",
    )

    tool = KubernetesTool()

    assert tool.api_url == (
        "https://kubernetes.example"
    )
    assert tool.timeout_seconds == 7.5
    assert tool.verify_tls is False
    assert tool.bearer_token == "secret-token"
    assert tool.cluster_name == "production-a"
    assert tool.allow_dry_run_fallback is False


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        (
            "KUBERNETES_TIMEOUT_SECONDS",
            "invalid",
            "must be a number",
        ),
        (
            "KUBERNETES_VERIFY_TLS",
            "invalid",
            "must be a boolean",
        ),
        (
            "KUBERNETES_ALLOW_DRY_RUN_FALLBACK",
            "invalid",
            "must be a boolean",
        ),
    ],
)
def test_invalid_environment_configuration(
    monkeypatch,
    name,
    value,
    message,
):
    clear_kubernetes_environment(
        monkeypatch
    )
    monkeypatch.setenv(
        name,
        value,
    )

    with pytest.raises(
        KubernetesConfigurationError,
        match=message,
    ):
        KubernetesTool()


@pytest.mark.parametrize(
    "api_url",
    [
        "kubernetes.example",
        "ftp://kubernetes.example",
        "https://",
    ],
)
def test_invalid_api_url_is_rejected(
    api_url,
):
    with pytest.raises(
        KubernetesConfigurationError,
        match="API URL is invalid",
    ):
        KubernetesTool(
            api_url=api_url
        )


def test_missing_ca_file_is_rejected(
    tmp_path,
):
    tool = KubernetesTool(
        api_url="https://kubernetes.test",
        ca_file=(
            tmp_path / "missing-ca.crt"
        ),
    )

    with pytest.raises(
        KubernetesConfigurationError,
        match="CA file was not found",
    ):
        _ = tool._httpx_verify


@pytest.mark.asyncio
async def test_naive_clock_is_rejected(
    monkeypatch,
):
    clear_kubernetes_environment(
        monkeypatch
    )
    tool = KubernetesTool(
        allow_dry_run_fallback=True,
        clock=lambda: datetime(
            2026,
            8,
            1,
            8,
            0,
        ),
    )

    with pytest.raises(
        KubernetesConfigurationError,
        match="timezone-aware",
    ):
        await tool.execute(
            action="describe",
            resource="pod",
            target="payment-api",
        )