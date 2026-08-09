from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

import services.agent_runtime.app.llm.gateway.gateway as gateway_module

from services.agent_runtime.app.llm.gateway.executor import (
    LLMExecutionError,
    LLMExecutor,
)
from services.agent_runtime.app.llm.gateway.gateway import (
    LLMGateway,
)
from services.agent_runtime.app.llm.gateway.models import (
    LLMGatewayRequest,
    LLMInvocationContext,
    LLMPriority,
    LLMTaskType,
)
from services.agent_runtime.app.llm.gateway.provider_health import (
    ProviderHealthManager,
)
from services.agent_runtime.app.llm.models import (
    ChatRequest,
    ChatResponse,
)
from services.agent_runtime.app.llm.providers.bailian_compatible import (
    BailianCompatibleProvider,
)
from services.agent_runtime.app.llm.providers.openai_compatible import (
    OpenAICompatibleProvider,
)


def ok_response(
    content: str = "{}",
) -> ChatResponse:
    return ChatResponse(
        content=content,
        model="unit-model",
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
    )


class SequenceClient:
    def __init__(
        self,
        values,
    ) -> None:
        self.values = list(
            values
        )

        self.calls = 0

    async def chat(
        self,
        request,
    ):
        self.calls += 1

        value = self.values.pop(
            0
        )

        if isinstance(
            value,
            BaseException,
        ):
            raise value

        return value


def status_error(
    status_code: int,
) -> httpx.HTTPStatusError:
    request = httpx.Request(
        "POST",
        "https://unit.invalid/v1/chat/completions",
    )

    response = httpx.Response(
        status_code,
        request=request,
    )

    return httpx.HTTPStatusError(
        "upstream status",
        request=request,
        response=response,
    )


@pytest.mark.asyncio
async def test_executor_retries_transport_error_with_exponential_backoff():
    delays = []

    async def sleep(
        delay: float,
    ) -> None:
        delays.append(
            delay
        )

    client = SequenceClient(
        [
            httpx.RemoteProtocolError(
                "server disconnected"
            ),
            httpx.ConnectError(
                "connect reset"
            ),
            ok_response(),
        ]
    )

    executor = LLMExecutor(
        retry_attempts=3,
        timeout=30,
        retry_base_delay=0.25,
        retry_max_delay=2.0,
        retry_jitter_ratio=0.2,
        sleep_func=sleep,
        random_func=lambda: 0.5,
    )

    response = await executor.execute(
        client,
        ChatRequest(
            system_prompt="system",
            user_prompt="user",
            temperature=0.0,
        ),
    )

    assert response.model == "unit-model"
    assert client.calls == 3

    assert delays == [
        0.25,
        0.5,
    ]


@pytest.mark.asyncio
async def test_executor_retries_503_but_not_401():
    delays = []

    async def sleep(
        delay: float,
    ) -> None:
        delays.append(
            delay
        )

    retry_client = SequenceClient(
        [
            status_error(
                503
            ),
            ok_response(),
        ]
    )

    executor = LLMExecutor(
        retry_attempts=3,
        sleep_func=sleep,
        random_func=lambda: 0.5,
    )

    await executor.execute(
        retry_client,
        ChatRequest(
            system_prompt="s",
            user_prompt="u",
            temperature=0.0,
        ),
    )

    assert retry_client.calls == 2
    assert delays == [
        0.25
    ]

    non_retry_client = SequenceClient(
        [
            status_error(
                401
            ),
            ok_response(),
        ]
    )

    with pytest.raises(
        LLMExecutionError,
    ) as exc_info:
        await executor.execute(
            non_retry_client,
            ChatRequest(
                system_prompt="s",
                user_prompt="u",
                temperature=0.0,
            ),
        )

    assert non_retry_client.calls == 1
    assert exc_info.value.retryable is False
    assert exc_info.value.code == "http_401"
    assert exc_info.value.attempts == 1


@pytest.mark.asyncio
async def test_executor_logs_are_sanitized(
    capsys,
):
    secret = (
        "https://secret-host.invalid/"
        "?token=do-not-log"
    )

    client = SequenceClient(
        [
            httpx.ConnectError(
                secret
            ),
        ]
    )

    executor = LLMExecutor(
        retry_attempts=1,
    )

    with pytest.raises(
        LLMExecutionError,
    ):
        await executor.execute(
            client,
            ChatRequest(
                system_prompt="s",
                user_prompt="u",
                temperature=0.0,
            ),
        )

    output = capsys.readouterr().out

    assert (
        "do-not-log"
        not in output
    )

    assert (
        "secret-host"
        not in output
    )

    assert (
        "transport_error"
        in output
    )


def configure_bailian(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "BAILIAN_BASE_URL",
        (
            "https://example.aliyuncs.com"
            "/compatible-mode/v1"
        ),
    )

    monkeypatch.setenv(
        "DASHSCOPE_API_KEY",
        "unit-secret",
    )

    monkeypatch.setenv(
        "BAILIAN_MODEL",
        "qwen-plus",
    )


class FakeResponse:
    def raise_for_status(
        self,
    ) -> None:
        return None

    def json(
        self,
    ):
        return {
            "model": "unit-model",
            "choices": [
                {
                    "message": {
                        "content": "{}",
                    }
                }
            ],
            "usage": {},
        }


class CountingFakeClient:
    def __init__(
        self,
        *args,
        **kwargs,
    ) -> None:
        self.posts = 0
        self.closed = False

    async def post(
        self,
        *args,
        **kwargs,
    ):
        self.posts += 1
        return FakeResponse()

    async def aclose(
        self,
    ) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_bailian_provider_reuses_one_http_client(
    monkeypatch,
):
    configure_bailian(
        monkeypatch
    )

    created = []

    def factory(
        *args,
        **kwargs,
    ):
        client = CountingFakeClient(
            *args,
            **kwargs,
        )

        created.append(
            client
        )

        return client

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        factory,
    )

    provider = (
        BailianCompatibleProvider()
    )

    request = ChatRequest(
        system_prompt="s",
        user_prompt="u",
        temperature=0.0,
    )

    await provider.chat(
        request
    )

    await provider.chat(
        request
    )

    assert len(
        created
    ) == 1

    assert created[
        0
    ].posts == 2

    await provider.aclose()

    assert created[
        0
    ].closed is True


@pytest.mark.asyncio
async def test_openai_provider_reuses_one_http_client(
    monkeypatch,
):
    monkeypatch.setenv(
        "OPENAI_BASE_URL",
        "https://api.example.invalid/v1",
    )

    monkeypatch.setenv(
        "OPENAI_API_KEY",
        "unit-secret",
    )

    monkeypatch.setenv(
        "OPENAI_MODEL",
        "unit-model",
    )

    created = []

    def factory(
        *args,
        **kwargs,
    ):
        client = CountingFakeClient(
            *args,
            **kwargs,
        )

        created.append(
            client
        )

        return client

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        factory,
    )

    provider = (
        OpenAICompatibleProvider()
    )

    request = ChatRequest(
        system_prompt="s",
        user_prompt="u",
        temperature=0.0,
    )

    await provider.chat(
        request
    )

    await provider.chat(
        request
    )

    assert len(
        created
    ) == 1

    assert created[
        0
    ].posts == 2

    await provider.aclose()

    assert created[
        0
    ].closed is True


def gateway_settings():
    return SimpleNamespace(
        llm=SimpleNamespace(
            gateway=SimpleNamespace(
                retry_attempts=3,
                request_timeout=30,
                rate_limit=SimpleNamespace(
                    enabled=False,
                    requests_per_minute=60,
                ),
            )
        )
    )


class FixedRouter:
    def __init__(
        self,
        provider: str = "openai",
    ) -> None:
        self.provider = provider

    def route(
        self,
        context,
    ):
        return SimpleNamespace(
            provider=self.provider,
        )


class RaisingExecutor:
    def __init__(
        self,
        error,
    ) -> None:
        self.error = error
        self.calls = 0

    async def execute(
        self,
        client,
        request,
    ):
        self.calls += 1
        raise self.error


class NeverFallback:
    def __init__(
        self,
    ) -> None:
        self.calls = 0

    def get_fallback(
        self,
        failed_provider,
    ):
        self.calls += 1
        return None


def request(
    *,
    enable_fallback: bool = True,
) -> LLMGatewayRequest:
    return LLMGatewayRequest(
        system_prompt="s",
        prompt="u",
        context=LLMInvocationContext(
            agent="unit",
            task=LLMTaskType.ANALYSIS,
            priority=LLMPriority.HIGH,
            require_json=True,
            preferred_provider=None,
            preferred_model=None,
            enable_fallback=enable_fallback,
        ),
        temperature=0.0,
    )


@pytest.mark.asyncio
async def test_gateway_does_not_mark_provider_unhealthy_for_non_retryable_error(
    monkeypatch,
):
    monkeypatch.setattr(
        gateway_module,
        "get_settings",
        gateway_settings,
    )

    health = ProviderHealthManager(
        [
            "openai"
        ]
    )

    fallback = NeverFallback()

    gateway = LLMGateway(
        clients={
            "openai": object(),
        },
        router=FixedRouter(),
        executor=RaisingExecutor(
            LLMExecutionError(
                "bad request",
                code="http_401",
                retryable=False,
                attempts=1,
            )
        ),
        fallback_manager=fallback,
        health_manager=health,
    )

    with pytest.raises(
        LLMExecutionError,
    ):
        await gateway.chat(
            request()
        )

    assert health.is_healthy(
        "openai"
    )

    assert (
        gateway
        .circuit_breaker
        .failure_count
        == 0
    )

    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_gateway_marks_transient_provider_failure_and_respects_fallback_flag(
    monkeypatch,
):
    monkeypatch.setattr(
        gateway_module,
        "get_settings",
        gateway_settings,
    )

    health = ProviderHealthManager(
        [
            "openai"
        ]
    )

    fallback = NeverFallback()

    gateway = LLMGateway(
        clients={
            "openai": object(),
        },
        router=FixedRouter(),
        executor=RaisingExecutor(
            LLMExecutionError(
                "transport",
                code="transport_error",
                retryable=True,
                attempts=3,
            )
        ),
        fallback_manager=fallback,
        health_manager=health,
    )

    with pytest.raises(
        LLMExecutionError,
    ):
        await gateway.chat(
            request(
                enable_fallback=False
            )
        )

    assert not health.is_healthy(
        "openai"
    )

    assert (
        gateway
        .circuit_breaker
        .failure_count
        == 1
    )

    assert fallback.calls == 0

class ResettableFakeClient:
    def __init__(
        self,
        *,
        fail_protocol: bool,
    ) -> None:
        self.fail_protocol = fail_protocol
        self.closed = False

    async def post(
        self,
        *args,
        **kwargs,
    ):
        if self.fail_protocol:
            raise httpx.RemoteProtocolError(
                "stale keep-alive connection"
            )

        return FakeResponse()

    async def aclose(
        self,
    ) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_bailian_protocol_error_discards_owned_pool_before_next_attempt(
    monkeypatch,
):
    configure_bailian(
        monkeypatch
    )

    created = []

    def factory(
        *args,
        **kwargs,
    ):
        client = ResettableFakeClient(
            fail_protocol=(
                len(created)
                == 0
            )
        )

        created.append(
            client
        )

        return client

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        factory,
    )

    provider = (
        BailianCompatibleProvider()
    )

    request_value = ChatRequest(
        system_prompt="s",
        user_prompt="u",
        temperature=0.0,
    )

    with pytest.raises(
        httpx.RemoteProtocolError,
    ):
        await provider.chat(
            request_value
        )

    assert len(
        created
    ) == 1

    assert created[
        0
    ].closed is True

    response = await provider.chat(
        request_value
    )

    assert response.model == "unit-model"
    assert len(
        created
    ) == 2


@pytest.mark.asyncio
async def test_openai_protocol_error_discards_owned_pool_before_next_attempt(
    monkeypatch,
):
    monkeypatch.setenv(
        "OPENAI_BASE_URL",
        "https://api.example.invalid/v1",
    )

    monkeypatch.setenv(
        "OPENAI_API_KEY",
        "unit-secret",
    )

    monkeypatch.setenv(
        "OPENAI_MODEL",
        "unit-model",
    )

    created = []

    def factory(
        *args,
        **kwargs,
    ):
        client = ResettableFakeClient(
            fail_protocol=(
                len(created)
                == 0
            )
        )

        created.append(
            client
        )

        return client

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        factory,
    )

    provider = (
        OpenAICompatibleProvider()
    )

    request_value = ChatRequest(
        system_prompt="s",
        user_prompt="u",
        temperature=0.0,
    )

    with pytest.raises(
        httpx.RemoteProtocolError,
    ):
        await provider.chat(
            request_value
        )

    assert len(
        created
    ) == 1

    assert created[
        0
    ].closed is True

    response = await provider.chat(
        request_value
    )

    assert response.model == "unit-model"
    assert len(
        created
    ) == 2

