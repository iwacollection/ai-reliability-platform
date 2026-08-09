from types import SimpleNamespace

import httpx
import pytest

import services.agent_runtime.app.evaluation.real_incident.llm_run as run_module
import services.agent_runtime.app.llm.provider_factory as provider_factory_module

from services.agent_runtime.app.evaluation.real_incident.llm_run import (
    create_historical_llm_runtime,
)
from services.agent_runtime.app.llm.factory import (
    create_llm_registry,
)
from services.agent_runtime.app.llm.models import (
    ChatRequest,
)
from services.agent_runtime.app.llm.provider_factory import (
    create_llm_provider,
)
from services.agent_runtime.app.llm.providers.bailian_compatible import (
    BailianCompatibleProvider,
)


def mock_settings():
    return SimpleNamespace(
        llm=SimpleNamespace(
            provider="mock"
        )
    )


def configure_bailian(
    monkeypatch,
):
    monkeypatch.setenv(
        "BAILIAN_BASE_URL",
        (
            "https://llm-example."
            "cn-beijing.maas.aliyuncs.com"
            "/compatible-mode/v1"
        ),
    )

    monkeypatch.setenv(
        "DASHSCOPE_API_KEY",
        "unit-test-secret",
    )

    monkeypatch.setenv(
        "BAILIAN_MODEL",
        "qwen-plus",
    )


def test_bailian_provider_is_registered():
    registry = create_llm_registry()

    assert isinstance(
        registry.get(
            "bailian"
        ),
        BailianCompatibleProvider,
    )


def test_explicit_bailian_override_does_not_mutate_mock_default(
    monkeypatch,
):
    configure_bailian(
        monkeypatch
    )

    monkeypatch.setattr(
        provider_factory_module,
        "get_settings",
        mock_settings,
    )

    provider = create_llm_provider(
        provider_name="bailian"
    )

    assert isinstance(
        provider,
        BailianCompatibleProvider,
    )

    assert (
        mock_settings().llm.provider
        == "mock"
    )


@pytest.mark.parametrize(
    (
        "missing_env",
        "message",
    ),
    [
        (
            "BAILIAN_BASE_URL",
            "BAILIAN_BASE_URL is not configured",
        ),
        (
            "DASHSCOPE_API_KEY",
            "DASHSCOPE_API_KEY is not configured",
        ),
        (
            "BAILIAN_MODEL",
            "BAILIAN_MODEL is not configured",
        ),
    ],
)
@pytest.mark.asyncio
async def test_bailian_missing_configuration_fails_before_network(
    monkeypatch,
    missing_env,
    message,
):
    configure_bailian(
        monkeypatch
    )

    monkeypatch.delenv(
        missing_env,
        raising=False,
    )

    network_calls = 0

    class ForbiddenClient:
        async def __aenter__(
            self,
        ):
            return self

        async def __aexit__(
            self,
            exc_type,
            exc,
            tb,
        ):
            return False

        async def post(
            self,
            *args,
            **kwargs,
        ):
            nonlocal network_calls
            network_calls += 1
            raise AssertionError(
                "Network must not be reached with invalid config"
            )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: ForbiddenClient(),
    )

    provider = BailianCompatibleProvider()

    with pytest.raises(
        RuntimeError,
        match=message,
    ):
        await provider.chat(
            ChatRequest(
                system_prompt="system",
                user_prompt="user",
                temperature=0.0,
            )
        )

    assert network_calls == 0


def test_bailian_base_url_must_use_compatible_mode(
    monkeypatch,
):
    configure_bailian(
        monkeypatch
    )

    monkeypatch.setenv(
        "BAILIAN_BASE_URL",
        "https://example.aliyuncs.com/api/v1",
    )

    provider = BailianCompatibleProvider()

    with pytest.raises(
        RuntimeError,
        match="compatible-mode/v1",
    ):
        provider.validate_configuration()


@pytest.mark.asyncio
async def test_bailian_chat_uses_openai_compatible_contract(
    monkeypatch,
):
    configure_bailian(
        monkeypatch
    )

    captured = {}

    class FakeResponse:
        def raise_for_status(
            self,
        ):
            return None

        def json(
            self,
        ):
            return {
                "id": "chatcmpl-unit",
                "model": "qwen-plus",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"stop":false}',
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 3,
                    "total_tokens": 14,
                },
            }

    class FakeClient:
        def __init__(
            self,
            *args,
            **kwargs,
        ):
            captured[
                "client_kwargs"
            ] = kwargs

        async def __aenter__(
            self,
        ):
            return self

        async def __aexit__(
            self,
            exc_type,
            exc,
            tb,
        ):
            return False

        async def post(
            self,
            url,
            *,
            json,
            headers,
        ):
            captured[
                "url"
            ] = url
            captured[
                "json"
            ] = json
            captured[
                "headers"
            ] = headers
            return FakeResponse()

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        FakeClient,
    )

    provider = BailianCompatibleProvider()

    response = await provider.chat(
        ChatRequest(
            system_prompt="You are an SRE.",
            user_prompt="Investigate this incident.",
            temperature=0.0,
        )
    )

    assert captured[
        "url"
    ].endswith(
        "/compatible-mode/v1/chat/completions"
    )

    assert captured[
        "json"
    ] == {
        "model": "qwen-plus",
        "messages": [
            {
                "role": "system",
                "content": "You are an SRE.",
            },
            {
                "role": "user",
                "content": "Investigate this incident.",
            },
        ],
        "temperature": 0.0,
    }

    assert captured[
        "headers"
    ][
        "Authorization"
    ] == "Bearer unit-test-secret"

    assert (
        response.content
        == '{"stop":false}'
    )

    assert (
        response.model
        == "qwen-plus"
    )

    assert (
        response.total_tokens
        == 14
    )


def test_historical_runtime_accepts_bailian_override(
    monkeypatch,
):
    configure_bailian(
        monkeypatch
    )

    captured = []

    class NoNetworkGateway:
        async def chat(
            self,
            request,
        ):
            raise AssertionError(
                "Composition test must not make a real request"
            )

    def fake_gateway_factory(
        provider_name=None,
    ):
        captured.append(
            provider_name
        )
        return NoNetworkGateway()

    monkeypatch.setattr(
        run_module,
        "create_llm_gateway",
        fake_gateway_factory,
    )

    runtime = create_historical_llm_runtime(
        provider_name="bailian"
    )

    assert captured == [
        "bailian"
    ]

    assert (
        runtime.historical_llm_provider_name
        == "bailian"
    )


def test_registry_creation_does_not_require_bailian_secrets(
    monkeypatch,
):
    monkeypatch.delenv(
        "BAILIAN_BASE_URL",
        raising=False,
    )
    monkeypatch.delenv(
        "DASHSCOPE_API_KEY",
        raising=False,
    )
    monkeypatch.delenv(
        "BAILIAN_MODEL",
        raising=False,
    )

    registry = create_llm_registry()

    assert (
        registry.get(
            "bailian"
        ).name
        == "bailian"
    )
