from types import SimpleNamespace

import pytest

import services.agent_runtime.app.evaluation.real_incident.llm_run as run_module
import services.agent_runtime.app.llm.gateway.factory as gateway_factory_module
import services.agent_runtime.app.llm.provider_factory as provider_factory_module

from services.agent_runtime.app.evaluation.real_incident.llm_run import (
    HistoricalLLMRunConfigurationError,
    create_historical_llm_runtime,
)
from services.agent_runtime.app.llm.gateway.factory import (
    create_llm_gateway,
)
from services.agent_runtime.app.llm.provider_factory import (
    create_llm_provider,
)
from services.agent_runtime.app.llm.providers.mock import (
    MockProvider,
)
from services.agent_runtime.app.llm.providers.openai_compatible import (
    OpenAICompatibleProvider,
)


class NoNetworkGateway:
    async def chat(
        self,
        request,
    ):
        raise AssertionError(
            "Provider override test must not call an external LLM"
        )


def mock_settings():
    return SimpleNamespace(
        llm=SimpleNamespace(
            provider="mock"
        )
    )


def test_default_provider_behavior_remains_mock(
    monkeypatch,
):
    monkeypatch.setattr(
        provider_factory_module,
        "get_settings",
        mock_settings,
    )

    provider = create_llm_provider()

    assert isinstance(
        provider,
        MockProvider,
    )


def test_explicit_provider_override_selects_openai(
    monkeypatch,
):
    monkeypatch.setattr(
        provider_factory_module,
        "get_settings",
        mock_settings,
    )

    provider = create_llm_provider(
        provider_name="openai"
    )

    assert isinstance(
        provider,
        OpenAICompatibleProvider,
    )

    assert (
        mock_settings().llm.provider
        == "mock"
    )


def test_gateway_factory_forwards_override(
    monkeypatch,
):
    captured = []

    def fake_provider_factory(
        provider_name=None,
    ):
        captured.append(
            provider_name
        )
        return MockProvider()

    monkeypatch.setattr(
        gateway_factory_module,
        "create_llm_provider",
        fake_provider_factory,
    )

    gateway = create_llm_gateway(
        provider_name="openai"
    )

    assert captured == [
        "openai"
    ]

    assert gateway is not None


def test_historical_runtime_override_uses_openai(
    monkeypatch,
):
    captured = []

    monkeypatch.setattr(
        run_module,
        "get_settings",
        mock_settings,
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
        provider_name="openai"
    )

    assert captured == [
        "openai"
    ]

    assert (
        runtime.historical_llm_provider_name
        == "openai"
    )


def test_explicit_mock_override_fails_before_gateway(
    monkeypatch,
):
    calls = 0

    monkeypatch.setattr(
        run_module,
        "get_settings",
        mock_settings,
    )

    def forbidden_gateway(
        provider_name=None,
    ):
        nonlocal calls
        calls += 1
        raise AssertionError(
            "Mock override must fail before Gateway construction"
        )

    monkeypatch.setattr(
        run_module,
        "create_llm_gateway",
        forbidden_gateway,
    )

    with pytest.raises(
        HistoricalLLMRunConfigurationError,
        match="refuses the mock provider",
    ):
        create_historical_llm_runtime(
            provider_name="mock"
        )

    assert calls == 0


@pytest.mark.parametrize(
    "provider_name",
    [
        "",
        "   ",
    ],
)
def test_blank_override_fails_closed(
    provider_name,
):
    with pytest.raises(
        HistoricalLLMRunConfigurationError,
        match="cannot be blank",
    ):
        create_historical_llm_runtime(
            provider_name=provider_name
        )
