from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "llm-gateway-transport-reliability-v1"

AFTER_NAME = (
    "llm_gateway_transport_reliability_v1_after.txt"
)

ERROR_NAME = (
    "llm_gateway_transport_reliability_v1_error.txt"
)

EXPECTED_HASHES = {'services/agent_runtime/app/llm/gateway/executor.py': '364edcc1e40e82fbddbcc9857b2b9a097767af50c8b9d459c412cdec6b052b4f', 'services/agent_runtime/app/llm/gateway/gateway.py': '6ff5c70af98e7ed3fa2b5f93d785075088c8c5bf87151d176ea145891837aac8', 'services/agent_runtime/app/llm/providers/openai_compatible.py': '9df8b9d9a8033f2a594e9ea0d3d27694f5d9952ee714e2d0fafd0ac44c033e3a', 'services/agent_runtime/app/llm/providers/bailian_compatible.py': '630c46a7e05dbd61ba75f299f35e67c1a81960db1fbf6896d0cbfa6986baef05'}

EXECUTOR_SOURCE = 'from __future__ import annotations\n\nimport asyncio\nimport random\nfrom collections.abc import Awaitable, Callable\nfrom time import perf_counter\n\nimport httpx\n\nfrom services.agent_runtime.app.llm.client import (\n    LLMClient,\n)\nfrom services.agent_runtime.app.llm.models import (\n    ChatRequest,\n    ChatResponse,\n)\n\n\nclass LLMExecutionError(\n    Exception\n):\n    """\n    Sanitized LLM execution failure.\n\n    Raw provider/transport error text is deliberately not retained in the\n    public message because it may contain endpoint or upstream details.\n    """\n\n    def __init__(\n        self,\n        message: str = "LLM execution failed",\n        *,\n        code: str = "execution_error",\n        retryable: bool = False,\n        attempts: int = 1,\n    ) -> None:\n        super().__init__(\n            message\n        )\n\n        self.code = code\n        self.retryable = retryable\n        self.attempts = attempts\n\n\nclass LLMExecutor:\n    """\n    Reliable LLM execution layer.\n\n    Responsibilities:\n    - timeout control\n    - retry only transient/provider-availability failures\n    - exponential backoff with bounded jitter\n    - sanitized diagnostics\n    - exception normalization\n\n    Not responsible:\n    - provider routing\n    - provider selection\n    - fallback decision\n    """\n\n    _RETRYABLE_HTTP_STATUS = {\n        408,\n        425,\n        429,\n    }\n\n    def __init__(\n        self,\n        retry_attempts: int = 3,\n        timeout: int | float = 30,\n        *,\n        retry_base_delay: float = 0.25,\n        retry_max_delay: float = 2.0,\n        retry_jitter_ratio: float = 0.20,\n        sleep_func: (\n            Callable[\n                [float],\n                Awaitable[None],\n            ]\n            | None\n        ) = None,\n        random_func: (\n            Callable[\n                [],\n                float,\n            ]\n            | None\n        ) = None,\n    ) -> None:\n        if retry_attempts < 1:\n            raise ValueError(\n                "retry_attempts must be >= 1"\n            )\n\n        if timeout <= 0:\n            raise ValueError(\n                "timeout must be > 0"\n            )\n\n        if retry_base_delay < 0:\n            raise ValueError(\n                "retry_base_delay must be >= 0"\n            )\n\n        if retry_max_delay < 0:\n            raise ValueError(\n                "retry_max_delay must be >= 0"\n            )\n\n        if not (\n            0.0\n            <= retry_jitter_ratio\n            <= 1.0\n        ):\n            raise ValueError(\n                "retry_jitter_ratio must be within [0,1]"\n            )\n\n        self.retry_attempts = (\n            retry_attempts\n        )\n\n        self.timeout = float(\n            timeout\n        )\n\n        self.retry_base_delay = float(\n            retry_base_delay\n        )\n\n        self.retry_max_delay = float(\n            retry_max_delay\n        )\n\n        self.retry_jitter_ratio = float(\n            retry_jitter_ratio\n        )\n\n        self._sleep_func = (\n            sleep_func\n            or asyncio.sleep\n        )\n\n        self._random_func = (\n            random_func\n            or random.random\n        )\n\n    async def execute(\n        self,\n        client: LLMClient,\n        request: ChatRequest,\n    ) -> ChatResponse:\n        """\n        Execute one logical Gateway request.\n\n        retry_attempts is the total number of provider attempts, not the\n        number of retries after the first attempt.\n        """\n\n        for attempt_index in range(\n            self.retry_attempts\n        ):\n            start = perf_counter()\n\n            try:\n                response = await asyncio.wait_for(\n                    client.chat(\n                        request\n                    ),\n                    timeout=self.timeout,\n                )\n\n                _duration_ms = round(\n                    (\n                        perf_counter()\n                        - start\n                    )\n                    * 1000,\n                    4,\n                )\n\n                return response\n\n            except Exception as exc:\n                attempt_number = (\n                    attempt_index\n                    + 1\n                )\n\n                retryable = (\n                    self._is_retryable_error(\n                        exc\n                    )\n                )\n\n                code = self._error_code(\n                    exc\n                )\n\n                # Intentionally sanitized: never log str(exc), URL, headers,\n                # credentials or response body here.\n                print(\n                    "LLM execution failed",\n                    {\n                        "attempt": attempt_number,\n                        "error_type": (\n                            type(exc).__name__\n                        ),\n                        "code": code,\n                        "retryable": retryable,\n                    },\n                )\n\n                final_attempt = (\n                    attempt_number\n                    >= self.retry_attempts\n                )\n\n                if (\n                    not retryable\n                    or final_attempt\n                ):\n                    raise LLMExecutionError(\n                        (\n                            "LLM execution failed "\n                            f"[{code}] after "\n                            f"{attempt_number} attempt(s)"\n                        ),\n                        code=code,\n                        retryable=retryable,\n                        attempts=attempt_number,\n                    ) from exc\n\n                delay = (\n                    self._retry_delay(\n                        attempt_index\n                    )\n                )\n\n                await self._sleep_func(\n                    delay\n                )\n\n        raise AssertionError(\n            "LLMExecutor retry loop exited unexpectedly"\n        )\n\n    def _retry_delay(\n        self,\n        attempt_index: int,\n    ) -> float:\n        base = min(\n            self.retry_max_delay,\n            (\n                self.retry_base_delay\n                * (\n                    2\n                    ** attempt_index\n                )\n            ),\n        )\n\n        jitter_window = (\n            base\n            * self.retry_jitter_ratio\n        )\n\n        random_value = min(\n            1.0,\n            max(\n                0.0,\n                float(\n                    self._random_func()\n                ),\n            ),\n        )\n\n        jitter = (\n            (\n                random_value\n                * 2.0\n            )\n            - 1.0\n        ) * jitter_window\n\n        return max(\n            0.0,\n            min(\n                self.retry_max_delay,\n                base + jitter,\n            ),\n        )\n\n    @classmethod\n    def _is_retryable_error(\n        cls,\n        exc: Exception,\n    ) -> bool:\n        if isinstance(\n            exc,\n            (\n                asyncio.TimeoutError,\n                httpx.TimeoutException,\n                httpx.TransportError,\n            ),\n        ):\n            return True\n\n        if isinstance(\n            exc,\n            httpx.HTTPStatusError,\n        ):\n            status = (\n                exc.response.status_code\n            )\n\n            return (\n                status\n                in cls._RETRYABLE_HTTP_STATUS\n                or (\n                    500\n                    <= status\n                    <= 599\n                )\n            )\n\n        return False\n\n    @classmethod\n    def _error_code(\n        cls,\n        exc: Exception,\n    ) -> str:\n        if isinstance(\n            exc,\n            (\n                asyncio.TimeoutError,\n                httpx.TimeoutException,\n            ),\n        ):\n            return "timeout"\n\n        if isinstance(\n            exc,\n            httpx.HTTPStatusError,\n        ):\n            return (\n                "http_"\n                + str(\n                    exc.response.status_code\n                )\n            )\n\n        if isinstance(\n            exc,\n            httpx.TransportError,\n        ):\n            return "transport_error"\n\n        return "non_retryable_error"\n\n\n__all__ = [\n    "LLMExecutionError",\n    "LLMExecutor",\n]\n'
GATEWAY_SOURCE = 'from common.config import (\n    get_settings,\n)\n\nfrom services.agent_runtime.app.llm.client import (\n    LLMClient,\n)\nfrom services.agent_runtime.app.llm.models import (\n    ChatRequest,\n)\nfrom services.agent_runtime.app.llm.gateway.models import (\n    LLMGatewayRequest,\n    LLMGatewayResponse,\n)\nfrom services.agent_runtime.app.llm.gateway.router import (\n    LLMRouter,\n)\nfrom services.agent_runtime.app.llm.gateway.provider_manager import (\n    ProviderManager,\n)\nfrom services.agent_runtime.app.llm.gateway.executor import (\n    LLMExecutionError,\n    LLMExecutor,\n)\nfrom services.agent_runtime.app.llm.gateway.rate_limiter import (\n    RateLimiter,\n)\nfrom services.agent_runtime.app.llm.gateway.fallback import (\n    FallbackManager,\n)\nfrom services.agent_runtime.app.llm.gateway.circuit_breaker import (\n    CircuitBreaker,\n)\nfrom services.agent_runtime.app.llm.gateway.provider_health import (\n    ProviderHealthManager,\n)\n\n\nclass LLMGateway:\n    """\n    Production LLM Gateway.\n\n    Responsibilities:\n    - Route LLM request\n    - Convert gateway request\n    - Manage provider client\n    - Rate limit protection\n    - Circuit breaker protection\n    - Provider health management\n    - Provider fallback\n    - Execute LLM safely\n    """\n\n    def __init__(\n        self,\n        clients: dict[str, LLMClient],\n        router: LLMRouter | None = None,\n        executor: LLMExecutor | None = None,\n        rate_limiter: RateLimiter | None = None,\n        fallback_manager: FallbackManager | None = None,\n        circuit_breaker: CircuitBreaker | None = None,\n        health_manager: ProviderHealthManager | None = None,\n    ) -> None:\n        self.provider_manager = (\n            ProviderManager(\n                clients\n            )\n        )\n\n        self.router = (\n            router\n            or LLMRouter()\n        )\n\n        settings = get_settings()\n\n        self.rate_limiter = (\n            rate_limiter\n            or RateLimiter(\n                enabled=(\n                    settings\n                    .llm\n                    .gateway\n                    .rate_limit\n                    .enabled\n                ),\n                requests_per_minute=(\n                    settings\n                    .llm\n                    .gateway\n                    .rate_limit\n                    .requests_per_minute\n                ),\n            )\n        )\n\n        self.circuit_breaker = (\n            circuit_breaker\n            or CircuitBreaker()\n        )\n\n        self.health_manager = (\n            health_manager\n            or ProviderHealthManager(\n                list(\n                    clients.keys()\n                )\n            )\n        )\n\n        self.fallback_manager = (\n            fallback_manager\n            or FallbackManager(\n                clients,\n                self.health_manager,\n            )\n        )\n\n        self.executor = (\n            executor\n            or LLMExecutor(\n                retry_attempts=(\n                    settings\n                    .llm\n                    .gateway\n                    .retry_attempts\n                ),\n                timeout=(\n                    settings\n                    .llm\n                    .gateway\n                    .request_timeout\n                ),\n            )\n        )\n\n    def _get_client(\n        self,\n        provider: str,\n    ) -> LLMClient:\n        return self.provider_manager.get(\n            provider\n        )\n\n    async def chat(\n        self,\n        request: LLMGatewayRequest,\n    ) -> LLMGatewayResponse:\n        # 1. Rate limit one logical Gateway request. Executor retries do not\n        # consume additional Gateway rate-limit slots.\n        self.rate_limiter.check()\n\n        # 2. Route.\n        route = self.router.route(\n            request.context\n        )\n\n        # 3. Circuit check.\n        self.circuit_breaker.check()\n\n        # 4. Primary provider.\n        client = self._get_client(\n            route.provider\n        )\n\n        fallback_used = False\n        provider_used = (\n            route.provider\n        )\n\n        chat_request = ChatRequest(\n            system_prompt=(\n                request.system_prompt\n            ),\n            user_prompt=(\n                request.prompt\n            ),\n            temperature=(\n                request.temperature\n            ),\n        )\n\n        try:\n            response = await self.executor.execute(\n                client,\n                chat_request,\n            )\n\n            self.circuit_breaker.record_success()\n\n            self.health_manager.mark_healthy(\n                route.provider\n            )\n\n        except LLMExecutionError as exc:\n            # Only transient/provider-availability failures affect provider\n            # health or the circuit. Configuration/client-contract failures\n            # are not availability signals.\n            if exc.retryable:\n                self.circuit_breaker.record_failure()\n\n                self.health_manager.mark_unhealthy(\n                    route.provider\n                )\n\n            fallback_client = None\n\n            if (\n                exc.retryable\n                and request.context.enable_fallback\n            ):\n                fallback_client = (\n                    self.fallback_manager\n                    .get_fallback(\n                        route.provider\n                    )\n                )\n\n            if fallback_client is None:\n                raise\n\n            response = await self.executor.execute(\n                fallback_client,\n                chat_request,\n            )\n\n            fallback_used = True\n\n            provider_used = (\n                fallback_client.provider.name\n                if hasattr(\n                    fallback_client,\n                    "provider",\n                )\n                else "fallback"\n            )\n\n            self.health_manager.mark_healthy(\n                provider_used\n            )\n\n        return LLMGatewayResponse(\n            content=response.content,\n            provider=provider_used,\n            model=response.model,\n            fallback_used=fallback_used,\n        )\n'
OPENAI_SOURCE = 'from __future__ import annotations\n\nimport asyncio\nimport os\nfrom typing import Any\n\nimport httpx\n\nfrom services.agent_runtime.app.llm.base import (\n    BaseLLMProvider,\n)\nfrom services.agent_runtime.app.llm.models import (\n    ChatRequest,\n    ChatResponse,\n)\n\n\nclass OpenAICompatibleProvider(\n    BaseLLMProvider\n):\n    """\n    OpenAI-compatible LLM provider.\n\n    One provider instance owns one lazily-created persistent AsyncClient so\n    repeated calls reuse the HTTP connection pool.\n\n    Compatible with:\n    - OpenAI\n    - vLLM\n    - Ollama\n    - other OpenAI API compatible servers\n    """\n\n    @property\n    def name(\n        self,\n    ) -> str:\n        return "openai"\n\n    def __init__(\n        self,\n        *,\n        http_client: (\n            httpx.AsyncClient\n            | None\n        ) = None,\n    ) -> None:\n        self.base_url = os.getenv(\n            "OPENAI_BASE_URL",\n            "https://api.openai.com/v1",\n        ).strip().rstrip("/")\n\n        self.api_key = os.getenv(\n            "OPENAI_API_KEY",\n            "",\n        ).strip()\n\n        self.model = os.getenv(\n            "OPENAI_MODEL",\n            "gpt-5",\n        ).strip()\n\n        self._http_client = (\n            http_client\n        )\n\n        self._owns_http_client = (\n            http_client is None\n        )\n\n        self._client_lock = (\n            asyncio.Lock()\n        )\n\n    async def _get_http_client(\n        self,\n    ) -> httpx.AsyncClient:\n        if self._http_client is not None:\n            return self._http_client\n\n        async with self._client_lock:\n            if self._http_client is None:\n                self._http_client = (\n                    httpx.AsyncClient(\n                        timeout=httpx.Timeout(\n                            30.0,\n                            connect=10.0,\n                            pool=5.0,\n                        ),\n                        limits=httpx.Limits(\n                            max_connections=20,\n                            max_keepalive_connections=10,\n                            keepalive_expiry=30.0,\n                        ),\n                    )\n                )\n\n        return self._http_client\n\n    async def aclose(\n        self,\n    ) -> None:\n        if not self._owns_http_client:\n            return\n\n        async with self._client_lock:\n            client = self._http_client\n            self._http_client = None\n\n            if client is not None:\n                close = getattr(\n                    client,\n                    "aclose",\n                    None,\n                )\n\n                if callable(\n                    close\n                ):\n                    await close()\n\n    async def chat(\n        self,\n        request: ChatRequest,\n    ) -> ChatResponse:\n        messages: list[\n            dict[str, Any]\n        ] = []\n\n        if request.system_prompt:\n            messages.append(\n                {\n                    "role": "system",\n                    "content": request.system_prompt,\n                }\n            )\n\n        messages.append(\n            {\n                "role": "user",\n                "content": request.user_prompt,\n            }\n        )\n\n        payload = {\n            "model": self.model,\n            "messages": messages,\n            "temperature": request.temperature,\n        }\n\n        headers = {\n            "Content-Type": "application/json",\n        }\n\n        if self.api_key:\n            headers[\n                "Authorization"\n            ] = (\n                f"Bearer {self.api_key}"\n            )\n\n        client = await self._get_http_client()\n\n        response = await client.post(\n            (\n                f"{self.base_url}"\n                "/chat/completions"\n            ),\n            json=payload,\n            headers=headers,\n        )\n\n        response.raise_for_status()\n\n        data = response.json()\n\n        message = (\n            data[\n                "choices"\n            ][\n                0\n            ][\n                "message"\n            ][\n                "content"\n            ]\n        )\n\n        usage = data.get(\n            "usage",\n            {},\n        )\n\n        return ChatResponse(\n            content=message,\n            model=data.get(\n                "model",\n                self.model,\n            ),\n            prompt_tokens=usage.get(\n                "prompt_tokens",\n                0,\n            ),\n            completion_tokens=usage.get(\n                "completion_tokens",\n                0,\n            ),\n            total_tokens=usage.get(\n                "total_tokens",\n                0,\n            ),\n        )\n\n\n__all__ = [\n    "OpenAICompatibleProvider",\n]\n'
BAILIAN_SOURCE = 'from __future__ import annotations\n\nimport asyncio\nimport os\nfrom typing import Any\nfrom urllib.parse import urlparse\n\nimport httpx\n\nfrom services.agent_runtime.app.llm.base import (\n    BaseLLMProvider,\n)\nfrom services.agent_runtime.app.llm.models import (\n    ChatRequest,\n    ChatResponse,\n)\n\n\nclass BailianCompatibleProvider(\n    BaseLLMProvider\n):\n    """\n    Alibaba Cloud Model Studio (Bailian) OpenAI-compatible provider.\n\n    One provider instance owns one lazily-created persistent AsyncClient.\n    Requests therefore reuse the HTTP connection pool and keep-alive\n    connections instead of rebuilding the transport for every chat() call.\n\n    Configuration:\n    - BAILIAN_BASE_URL\n    - DASHSCOPE_API_KEY\n    - BAILIAN_MODEL\n\n    BAILIAN_BASE_URL must already include /compatible-mode/v1.\n    Configuration is validated only when chat() is invoked so registry\n    construction never breaks the safe default mock development path.\n    """\n\n    @property\n    def name(\n        self,\n    ) -> str:\n        return "bailian"\n\n    def __init__(\n        self,\n        *,\n        http_client: (\n            httpx.AsyncClient\n            | None\n        ) = None,\n    ) -> None:\n        self.base_url = os.getenv(\n            "BAILIAN_BASE_URL",\n            "",\n        ).strip().rstrip("/")\n\n        self.api_key = os.getenv(\n            "DASHSCOPE_API_KEY",\n            "",\n        ).strip()\n\n        self.model = os.getenv(\n            "BAILIAN_MODEL",\n            "",\n        ).strip()\n\n        self._http_client = (\n            http_client\n        )\n\n        self._owns_http_client = (\n            http_client is None\n        )\n\n        self._client_lock = (\n            asyncio.Lock()\n        )\n\n    def validate_configuration(\n        self,\n    ) -> None:\n        if not self.base_url:\n            raise RuntimeError(\n                "BAILIAN_BASE_URL is not configured"\n            )\n\n        parsed = urlparse(\n            self.base_url\n        )\n\n        if (\n            parsed.scheme != "https"\n            or not parsed.netloc\n            or parsed.username is not None\n            or parsed.password is not None\n            or parsed.query\n            or parsed.fragment\n        ):\n            raise RuntimeError(\n                "BAILIAN_BASE_URL must be a clean HTTPS URL"\n            )\n\n        if not (\n            parsed.path.rstrip("/")\n            .endswith(\n                "/compatible-mode/v1"\n            )\n        ):\n            raise RuntimeError(\n                "BAILIAN_BASE_URL must end with /compatible-mode/v1"\n            )\n\n        if not self.api_key:\n            raise RuntimeError(\n                "DASHSCOPE_API_KEY is not configured"\n            )\n\n        if not self.model:\n            raise RuntimeError(\n                "BAILIAN_MODEL is not configured"\n            )\n\n    async def _get_http_client(\n        self,\n    ) -> httpx.AsyncClient:\n        if self._http_client is not None:\n            return self._http_client\n\n        async with self._client_lock:\n            if self._http_client is None:\n                self._http_client = (\n                    httpx.AsyncClient(\n                        timeout=httpx.Timeout(\n                            30.0,\n                            connect=10.0,\n                            pool=5.0,\n                        ),\n                        limits=httpx.Limits(\n                            max_connections=20,\n                            max_keepalive_connections=10,\n                            keepalive_expiry=30.0,\n                        ),\n                    )\n                )\n\n        return self._http_client\n\n    async def aclose(\n        self,\n    ) -> None:\n        if not self._owns_http_client:\n            return\n\n        async with self._client_lock:\n            client = self._http_client\n            self._http_client = None\n\n            if client is not None:\n                close = getattr(\n                    client,\n                    "aclose",\n                    None,\n                )\n\n                if callable(\n                    close\n                ):\n                    await close()\n\n    async def chat(\n        self,\n        request: ChatRequest,\n    ) -> ChatResponse:\n        self.validate_configuration()\n\n        messages: list[\n            dict[str, Any]\n        ] = []\n\n        if request.system_prompt:\n            messages.append(\n                {\n                    "role": "system",\n                    "content": request.system_prompt,\n                }\n            )\n\n        messages.append(\n            {\n                "role": "user",\n                "content": request.user_prompt,\n            }\n        )\n\n        payload = {\n            "model": self.model,\n            "messages": messages,\n            "temperature": request.temperature,\n        }\n\n        headers = {\n            "Content-Type": "application/json",\n            "Authorization": (\n                f"Bearer {self.api_key}"\n            ),\n        }\n\n        client = await self._get_http_client()\n\n        response = await client.post(\n            (\n                f"{self.base_url}"\n                "/chat/completions"\n            ),\n            json=payload,\n            headers=headers,\n        )\n\n        response.raise_for_status()\n\n        data = response.json()\n\n        choices = data.get(\n            "choices"\n        )\n\n        if (\n            not isinstance(\n                choices,\n                list,\n            )\n            or not choices\n            or not isinstance(\n                choices[0],\n                dict,\n            )\n        ):\n            raise RuntimeError(\n                "Bailian response choices are invalid"\n            )\n\n        message = choices[0].get(\n            "message"\n        )\n\n        if not isinstance(\n            message,\n            dict,\n        ):\n            raise RuntimeError(\n                "Bailian response message is invalid"\n            )\n\n        content = message.get(\n            "content"\n        )\n\n        if (\n            not isinstance(\n                content,\n                str,\n            )\n            or not content.strip()\n        ):\n            raise RuntimeError(\n                "Bailian response content is invalid"\n            )\n\n        usage = data.get(\n            "usage",\n            {},\n        )\n\n        if not isinstance(\n            usage,\n            dict,\n        ):\n            usage = {}\n\n        return ChatResponse(\n            content=content,\n            model=data.get(\n                "model",\n                self.model,\n            ),\n            prompt_tokens=usage.get(\n                "prompt_tokens",\n                0,\n            ),\n            completion_tokens=usage.get(\n                "completion_tokens",\n                0,\n            ),\n            total_tokens=usage.get(\n                "total_tokens",\n                0,\n            ),\n        )\n\n\n__all__ = [\n    "BailianCompatibleProvider",\n]\n'
TEST_SOURCE = 'from __future__ import annotations\n\nfrom types import SimpleNamespace\n\nimport httpx\nimport pytest\n\nimport services.agent_runtime.app.llm.gateway.gateway as gateway_module\n\nfrom services.agent_runtime.app.llm.gateway.executor import (\n    LLMExecutionError,\n    LLMExecutor,\n)\nfrom services.agent_runtime.app.llm.gateway.gateway import (\n    LLMGateway,\n)\nfrom services.agent_runtime.app.llm.gateway.models import (\n    LLMGatewayRequest,\n    LLMInvocationContext,\n    LLMPriority,\n    LLMTaskType,\n)\nfrom services.agent_runtime.app.llm.gateway.provider_health import (\n    ProviderHealthManager,\n)\nfrom services.agent_runtime.app.llm.models import (\n    ChatRequest,\n    ChatResponse,\n)\nfrom services.agent_runtime.app.llm.providers.bailian_compatible import (\n    BailianCompatibleProvider,\n)\nfrom services.agent_runtime.app.llm.providers.openai_compatible import (\n    OpenAICompatibleProvider,\n)\n\n\ndef ok_response(\n    content: str = "{}",\n) -> ChatResponse:\n    return ChatResponse(\n        content=content,\n        model="unit-model",\n        prompt_tokens=1,\n        completion_tokens=1,\n        total_tokens=2,\n    )\n\n\nclass SequenceClient:\n    def __init__(\n        self,\n        values,\n    ) -> None:\n        self.values = list(\n            values\n        )\n\n        self.calls = 0\n\n    async def chat(\n        self,\n        request,\n    ):\n        self.calls += 1\n\n        value = self.values.pop(\n            0\n        )\n\n        if isinstance(\n            value,\n            BaseException,\n        ):\n            raise value\n\n        return value\n\n\ndef status_error(\n    status_code: int,\n) -> httpx.HTTPStatusError:\n    request = httpx.Request(\n        "POST",\n        "https://unit.invalid/v1/chat/completions",\n    )\n\n    response = httpx.Response(\n        status_code,\n        request=request,\n    )\n\n    return httpx.HTTPStatusError(\n        "upstream status",\n        request=request,\n        response=response,\n    )\n\n\n@pytest.mark.asyncio\nasync def test_executor_retries_transport_error_with_exponential_backoff():\n    delays = []\n\n    async def sleep(\n        delay: float,\n    ) -> None:\n        delays.append(\n            delay\n        )\n\n    client = SequenceClient(\n        [\n            httpx.RemoteProtocolError(\n                "server disconnected"\n            ),\n            httpx.ConnectError(\n                "connect reset"\n            ),\n            ok_response(),\n        ]\n    )\n\n    executor = LLMExecutor(\n        retry_attempts=3,\n        timeout=30,\n        retry_base_delay=0.25,\n        retry_max_delay=2.0,\n        retry_jitter_ratio=0.2,\n        sleep_func=sleep,\n        random_func=lambda: 0.5,\n    )\n\n    response = await executor.execute(\n        client,\n        ChatRequest(\n            system_prompt="system",\n            user_prompt="user",\n            temperature=0.0,\n        ),\n    )\n\n    assert response.model == "unit-model"\n    assert client.calls == 3\n\n    assert delays == [\n        0.25,\n        0.5,\n    ]\n\n\n@pytest.mark.asyncio\nasync def test_executor_retries_503_but_not_401():\n    delays = []\n\n    async def sleep(\n        delay: float,\n    ) -> None:\n        delays.append(\n            delay\n        )\n\n    retry_client = SequenceClient(\n        [\n            status_error(\n                503\n            ),\n            ok_response(),\n        ]\n    )\n\n    executor = LLMExecutor(\n        retry_attempts=3,\n        sleep_func=sleep,\n        random_func=lambda: 0.5,\n    )\n\n    await executor.execute(\n        retry_client,\n        ChatRequest(\n            system_prompt="s",\n            user_prompt="u",\n            temperature=0.0,\n        ),\n    )\n\n    assert retry_client.calls == 2\n    assert delays == [\n        0.25\n    ]\n\n    non_retry_client = SequenceClient(\n        [\n            status_error(\n                401\n            ),\n            ok_response(),\n        ]\n    )\n\n    with pytest.raises(\n        LLMExecutionError,\n    ) as exc_info:\n        await executor.execute(\n            non_retry_client,\n            ChatRequest(\n                system_prompt="s",\n                user_prompt="u",\n                temperature=0.0,\n            ),\n        )\n\n    assert non_retry_client.calls == 1\n    assert exc_info.value.retryable is False\n    assert exc_info.value.code == "http_401"\n    assert exc_info.value.attempts == 1\n\n\n@pytest.mark.asyncio\nasync def test_executor_logs_are_sanitized(\n    capsys,\n):\n    secret = (\n        "https://secret-host.invalid/"\n        "?token=do-not-log"\n    )\n\n    client = SequenceClient(\n        [\n            httpx.ConnectError(\n                secret\n            ),\n        ]\n    )\n\n    executor = LLMExecutor(\n        retry_attempts=1,\n    )\n\n    with pytest.raises(\n        LLMExecutionError,\n    ):\n        await executor.execute(\n            client,\n            ChatRequest(\n                system_prompt="s",\n                user_prompt="u",\n                temperature=0.0,\n            ),\n        )\n\n    output = capsys.readouterr().out\n\n    assert (\n        "do-not-log"\n        not in output\n    )\n\n    assert (\n        "secret-host"\n        not in output\n    )\n\n    assert (\n        "transport_error"\n        in output\n    )\n\n\ndef configure_bailian(\n    monkeypatch,\n) -> None:\n    monkeypatch.setenv(\n        "BAILIAN_BASE_URL",\n        (\n            "https://example.aliyuncs.com"\n            "/compatible-mode/v1"\n        ),\n    )\n\n    monkeypatch.setenv(\n        "DASHSCOPE_API_KEY",\n        "unit-secret",\n    )\n\n    monkeypatch.setenv(\n        "BAILIAN_MODEL",\n        "qwen-plus",\n    )\n\n\nclass FakeResponse:\n    def raise_for_status(\n        self,\n    ) -> None:\n        return None\n\n    def json(\n        self,\n    ):\n        return {\n            "model": "unit-model",\n            "choices": [\n                {\n                    "message": {\n                        "content": "{}",\n                    }\n                }\n            ],\n            "usage": {},\n        }\n\n\nclass CountingFakeClient:\n    def __init__(\n        self,\n        *args,\n        **kwargs,\n    ) -> None:\n        self.posts = 0\n        self.closed = False\n\n    async def post(\n        self,\n        *args,\n        **kwargs,\n    ):\n        self.posts += 1\n        return FakeResponse()\n\n    async def aclose(\n        self,\n    ) -> None:\n        self.closed = True\n\n\n@pytest.mark.asyncio\nasync def test_bailian_provider_reuses_one_http_client(\n    monkeypatch,\n):\n    configure_bailian(\n        monkeypatch\n    )\n\n    created = []\n\n    def factory(\n        *args,\n        **kwargs,\n    ):\n        client = CountingFakeClient(\n            *args,\n            **kwargs,\n        )\n\n        created.append(\n            client\n        )\n\n        return client\n\n    monkeypatch.setattr(\n        httpx,\n        "AsyncClient",\n        factory,\n    )\n\n    provider = (\n        BailianCompatibleProvider()\n    )\n\n    request = ChatRequest(\n        system_prompt="s",\n        user_prompt="u",\n        temperature=0.0,\n    )\n\n    await provider.chat(\n        request\n    )\n\n    await provider.chat(\n        request\n    )\n\n    assert len(\n        created\n    ) == 1\n\n    assert created[\n        0\n    ].posts == 2\n\n    await provider.aclose()\n\n    assert created[\n        0\n    ].closed is True\n\n\n@pytest.mark.asyncio\nasync def test_openai_provider_reuses_one_http_client(\n    monkeypatch,\n):\n    monkeypatch.setenv(\n        "OPENAI_BASE_URL",\n        "https://api.example.invalid/v1",\n    )\n\n    monkeypatch.setenv(\n        "OPENAI_API_KEY",\n        "unit-secret",\n    )\n\n    monkeypatch.setenv(\n        "OPENAI_MODEL",\n        "unit-model",\n    )\n\n    created = []\n\n    def factory(\n        *args,\n        **kwargs,\n    ):\n        client = CountingFakeClient(\n            *args,\n            **kwargs,\n        )\n\n        created.append(\n            client\n        )\n\n        return client\n\n    monkeypatch.setattr(\n        httpx,\n        "AsyncClient",\n        factory,\n    )\n\n    provider = (\n        OpenAICompatibleProvider()\n    )\n\n    request = ChatRequest(\n        system_prompt="s",\n        user_prompt="u",\n        temperature=0.0,\n    )\n\n    await provider.chat(\n        request\n    )\n\n    await provider.chat(\n        request\n    )\n\n    assert len(\n        created\n    ) == 1\n\n    assert created[\n        0\n    ].posts == 2\n\n    await provider.aclose()\n\n    assert created[\n        0\n    ].closed is True\n\n\ndef gateway_settings():\n    return SimpleNamespace(\n        llm=SimpleNamespace(\n            gateway=SimpleNamespace(\n                retry_attempts=3,\n                request_timeout=30,\n                rate_limit=SimpleNamespace(\n                    enabled=False,\n                    requests_per_minute=60,\n                ),\n            )\n        )\n    )\n\n\nclass FixedRouter:\n    def __init__(\n        self,\n        provider: str = "openai",\n    ) -> None:\n        self.provider = provider\n\n    def route(\n        self,\n        context,\n    ):\n        return SimpleNamespace(\n            provider=self.provider,\n        )\n\n\nclass RaisingExecutor:\n    def __init__(\n        self,\n        error,\n    ) -> None:\n        self.error = error\n        self.calls = 0\n\n    async def execute(\n        self,\n        client,\n        request,\n    ):\n        self.calls += 1\n        raise self.error\n\n\nclass NeverFallback:\n    def __init__(\n        self,\n    ) -> None:\n        self.calls = 0\n\n    def get_fallback(\n        self,\n        failed_provider,\n    ):\n        self.calls += 1\n        return None\n\n\ndef request(\n    *,\n    enable_fallback: bool = True,\n) -> LLMGatewayRequest:\n    return LLMGatewayRequest(\n        system_prompt="s",\n        prompt="u",\n        context=LLMInvocationContext(\n            agent="unit",\n            task=LLMTaskType.ANALYSIS,\n            priority=LLMPriority.HIGH,\n            require_json=True,\n            preferred_provider=None,\n            preferred_model=None,\n            enable_fallback=enable_fallback,\n        ),\n        temperature=0.0,\n    )\n\n\n@pytest.mark.asyncio\nasync def test_gateway_does_not_mark_provider_unhealthy_for_non_retryable_error(\n    monkeypatch,\n):\n    monkeypatch.setattr(\n        gateway_module,\n        "get_settings",\n        gateway_settings,\n    )\n\n    health = ProviderHealthManager(\n        [\n            "openai"\n        ]\n    )\n\n    fallback = NeverFallback()\n\n    gateway = LLMGateway(\n        clients={\n            "openai": object(),\n        },\n        router=FixedRouter(),\n        executor=RaisingExecutor(\n            LLMExecutionError(\n                "bad request",\n                code="http_401",\n                retryable=False,\n                attempts=1,\n            )\n        ),\n        fallback_manager=fallback,\n        health_manager=health,\n    )\n\n    with pytest.raises(\n        LLMExecutionError,\n    ):\n        await gateway.chat(\n            request()\n        )\n\n    assert health.is_healthy(\n        "openai"\n    )\n\n    assert (\n        gateway\n        .circuit_breaker\n        .failure_count\n        == 0\n    )\n\n    assert fallback.calls == 0\n\n\n@pytest.mark.asyncio\nasync def test_gateway_marks_transient_provider_failure_and_respects_fallback_flag(\n    monkeypatch,\n):\n    monkeypatch.setattr(\n        gateway_module,\n        "get_settings",\n        gateway_settings,\n    )\n\n    health = ProviderHealthManager(\n        [\n            "openai"\n        ]\n    )\n\n    fallback = NeverFallback()\n\n    gateway = LLMGateway(\n        clients={\n            "openai": object(),\n        },\n        router=FixedRouter(),\n        executor=RaisingExecutor(\n            LLMExecutionError(\n                "transport",\n                code="transport_error",\n                retryable=True,\n                attempts=3,\n            )\n        ),\n        fallback_manager=fallback,\n        health_manager=health,\n    )\n\n    with pytest.raises(\n        LLMExecutionError,\n    ):\n        await gateway.chat(\n            request(\n                enable_fallback=False\n            )\n        )\n\n    assert not health.is_healthy(\n        "openai"\n    )\n\n    assert (\n        gateway\n        .circuit_breaker\n        .failure_count\n        == 1\n    )\n\n    assert fallback.calls == 0\n'


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def find_repo_root(
    start: Path,
) -> Path:
    for candidate in (
        start,
        *start.parents,
    ):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate

    raise RuntimeError(
        "Repository root not found. "
        "Run from inside ai-reliability-platform."
    )


def normalize_text(
    value: str,
) -> str:
    return (
        value
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def read_text(
    path: Path,
) -> str:
    return normalize_text(
        path.read_text(
            encoding="utf-8-sig",
            errors="strict",
        )
    )


def write_text(
    path: Path,
    value: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        normalize_text(
            value
        ),
        encoding="utf-8",
        newline="\n",
    )


def sha256_text(
    value: str,
) -> str:
    return hashlib.sha256(
        normalize_text(
            value
        ).encode(
            "utf-8"
        )
    ).hexdigest()


def backup_file(
    path: Path,
) -> Path:
    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup = path.with_name(
        f"{path.name}.before_{VERSION}_{stamp}.bak"
    )

    shutil.copy2(
        path,
        backup,
    )

    return backup


def run_command(
    *,
    root: Path,
    name: str,
    command: list[str],
) -> CommandResult:
    process = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    return CommandResult(
        name=name,
        command=command,
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )


def section(
    lines: list[str],
    title: str,
) -> None:
    lines.extend(
        [
            "",
            "=" * 120,
            title,
            "=" * 120,
            "",
        ]
    )


def add_command(
    lines: list[str],
    result: CommandResult,
) -> None:
    section(
        lines,
        f"COMMAND: {result.name}",
    )

    lines.extend(
        [
            " ".join(
                result.command
            ),
            "",
            f"ExitCode: {result.returncode}",
            "",
            "STDOUT",
            "-" * 120,
            result.stdout.rstrip()
            or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            result.stderr.rstrip()
            or "<EMPTY>",
        ]
    )


def verify_hash(
    *,
    root: Path,
    relative: str,
) -> None:
    path = root / relative

    if not path.exists():
        raise RuntimeError(
            f"Required current file is missing: {relative}"
        )

    actual = sha256_text(
        read_text(
            path
        )
    )

    expected = EXPECTED_HASHES[
        relative
    ]

    if actual != expected:
        raise RuntimeError(
            (
                f"{relative} changed after the reviewed current snapshot. "
                f"expected_sha256={expected} actual_sha256={actual}. "
                "Refusing to patch stale code."
            )
        )


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after = root / AFTER_NAME
    error = root / ERROR_NAME

    for output in (
        after,
        error,
    ):
        try:
            output.unlink()
        except FileNotFoundError:
            pass

    executor_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "llm"
        / "gateway"
        / "executor.py"
    )

    gateway_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "llm"
        / "gateway"
        / "gateway.py"
    )

    openai_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "llm"
        / "providers"
        / "openai_compatible.py"
    )

    bailian_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "llm"
        / "providers"
        / "bailian_compatible.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_llm_gateway_transport_reliability.py"
    )

    sources = {
        executor_file: EXECUTOR_SOURCE,
        gateway_file: GATEWAY_SOURCE,
        openai_file: OPENAI_SOURCE,
        bailian_file: BAILIAN_SOURCE,
        test_file: TEST_SOURCE,
    }

    targets = list(
        sources.keys()
    )

    preexisting = {
        path: path.exists()
        for path in targets
    }

    backups = []

    report = [
        "LLM Gateway Transport Reliability v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Motivation from Full x3:",
        "- repeated transient LLM transport failures became the dominant final failure mode",
        "- current Bailian/OpenAI providers rebuild AsyncClient per chat call",
        "- current Executor retries every exception with a fixed 0.5 second sleep",
        "",
        "Transport changes:",
        "- Bailian provider reuses one lazy persistent AsyncClient per provider instance",
        "- OpenAI-compatible provider receives the same connection-pool lifecycle",
        "- explicit aclose() is available for owned provider clients",
        "- keep-alive pool limits are bounded",
        "",
        "Retry changes:",
        "- retry_attempts remains the configured total-attempt ceiling",
        "- transient timeout/transport failures are retryable",
        "- HTTP 408/425/429 and 5xx are retryable",
        "- ordinary 4xx/config/response-contract errors are fail-fast",
        "- exponential backoff replaces fixed 0.5 second sleep",
        "- bounded jitter prevents synchronized retry bursts",
        "- Gateway execution logs no longer print raw exception text",
        "",
        "Gateway semantics:",
        "- only retryable availability failures affect provider health/circuit state",
        "- non-retryable client/configuration failures do not poison health state",
        "- fallback is attempted only for retryable failures and only when enable_fallback=True",
        "- single-provider mode cleanly re-raises when no fallback exists",
        "",
        "Unchanged:",
        "- no Investigation Reasoner change",
        "- no Tool/Kubernetes/Prometheus change",
        "- no Action/Approval/Verification change",
        "- no app.yaml/settings schema change",
        "- installer sends no external request",
    ]

    try:
        section(
            report,
            "CURRENT HASH PREFLIGHT",
        )

        for relative in EXPECTED_HASHES:
            verify_hash(
                root=root,
                relative=relative,
            )

            report.append(
                relative
                + "="
                + EXPECTED_HASHES[
                    relative
                ]
            )

        section(
            report,
            "BACKUP",
        )

        for path in targets:
            if path.exists():
                backup = backup_file(
                    path
                )

                backups.append(
                    (
                        path,
                        backup,
                    )
                )

                report.append(
                    "backup="
                    + str(
                        backup.relative_to(
                            root
                        )
                    )
                )

        for path, source in sources.items():
            write_text(
                path,
                source,
            )

        syntax = run_command(
            root=root,
            name="Python syntax",
            command=[
                "uv",
                "run",
                "python",
                "-m",
                "py_compile",
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
                    for path in targets
                ],
            ],
        )

        add_command(
            report,
            syntax,
        )

        if syntax.returncode != 0:
            raise RuntimeError(
                "Python syntax verification failed"
            )

        focused = run_command(
            root=root,
            name="Gateway Transport Reliability focused suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_llm_gateway_transport_reliability.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_bailian_provider.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_llm_config.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_llm_provider_override.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            focused,
        )

        if focused.returncode != 0:
            raise RuntimeError(
                "Gateway Transport Reliability focused tests failed"
            )

        compatibility = run_command(
            root=root,
            name="Shared Gateway / Investigation compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_shared_llm_gateway_ownership.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_execution_resilience.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_llm_shadow_execution.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_real_llm_historical_run.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            compatibility,
        )

        if compatibility.returncode != 0:
            raise RuntimeError(
                "Shared Gateway / Investigation compatibility tests failed"
            )

        preflight = run_command(
            root=root,
            name="Transport semantics preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from services.agent_runtime.app.llm.gateway.executor "
                    "import LLMExecutor; "
                    "from services.agent_runtime.app.llm.providers."
                    "bailian_compatible import BailianCompatibleProvider; "
                    "from services.agent_runtime.app.llm.providers."
                    "openai_compatible import OpenAICompatibleProvider; "
                    "e=LLMExecutor(); "
                    "print('attempts='+str(e.retry_attempts)); "
                    "print('base_delay='+str(e.retry_base_delay)); "
                    "print('max_delay='+str(e.retry_max_delay)); "
                    "print('jitter='+str(e.retry_jitter_ratio)); "
                    "print('bailian_aclose='+str(hasattr(BailianCompatibleProvider,'aclose'))); "
                    "print('openai_aclose='+str(hasattr(OpenAICompatibleProvider,'aclose'))); "
                    "assert e.retry_attempts == 3; "
                    "assert hasattr(BailianCompatibleProvider,'aclose'); "
                    "assert hasattr(OpenAICompatibleProvider,'aclose')"
                ),
            ],
        )

        add_command(
            report,
            preflight,
        )

        if preflight.returncode != 0:
            raise RuntimeError(
                "Transport semantics preflight failed"
            )

        authority = run_command(
            root=root,
            name="Authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "files=["
                    "Path(r'services/agent_runtime/app/llm/gateway/executor.py'),"
                    "Path(r'services/agent_runtime/app/llm/gateway/gateway.py'),"
                    "Path(r'services/agent_runtime/app/llm/providers/openai_compatible.py'),"
                    "Path(r'services/agent_runtime/app/llm/providers/bailian_compatible.py')"
                    "]; "
                    "s='\\n'.join(x.read_text(encoding='utf-8') for x in files); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService',"
                    "'VerificationRuntime','kubectl'] if x in s]; "
                    "print('forbidden_matches='+str(bad)); "
                    "print('raw_exception_log='+str('\"error\": str(exc)' in s)); "
                    "raise SystemExit(1 if bad or '\"error\": str(exc)' in s else 0)"
                ),
            ],
        )

        add_command(
            report,
            authority,
        )

        if authority.returncode != 0:
            raise RuntimeError(
                "Transport authority/sanitization boundary failed"
            )

        status = run_command(
            root=root,
            name="Git status",
            command=[
                "git",
                "status",
                "--short",
                "--",
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
                    for path in targets
                ],
            ],
        )

        add_command(
            report,
            status,
        )

        section(
            report,
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "LLM Gateway Transport Reliability v1 is installed.",
                "",
                "Expected live behavior:",
                "- repeated Bailian decisions reuse one keep-alive connection pool",
                "- transient disconnect/timeout/5xx/429 errors use bounded exponential retry",
                "- permanent 4xx/configuration errors fail immediately",
                "- raw upstream exception text is not printed by Executor",
                "- non-retryable failures do not poison provider health/circuit state",
                "",
                "Next acceptance:",
                "run Full 7 scenarios x3 again through the existing batch bundle runner.",
            ]
        )

        write_text(
            after,
            "\n".join(
                report
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "LLM GATEWAY TRANSPORT RELIABILITY V1 PASSED"
        )
        print("=" * 72)
        print("")
        print(
            "No real LLM/Kubernetes/Prometheus request was sent."
        )
        print("")
        print("Upload only:")
        print(after)

        return 0

    except Exception as exc:
        rollback = []

        for original, backup in reversed(
            backups
        ):
            try:
                shutil.copy2(
                    backup,
                    original,
                )

                rollback.append(
                    "RESTORED "
                    + str(
                        original.relative_to(
                            root
                        )
                    )
                )
            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK FAILED "
                    + str(
                        original.relative_to(
                            root
                        )
                    )
                    + ": "
                    + f"{type(rollback_exc).__name__}: {rollback_exc}"
                )

        for path in targets:
            if (
                not preexisting[
                    path
                ]
                and path.exists()
            ):
                try:
                    path.unlink()

                    rollback.append(
                        "REMOVED newly-created "
                        + str(
                            path.relative_to(
                                root
                            )
                        )
                    )
                except Exception as rollback_exc:
                    rollback.append(
                        "ROLLBACK FAILED removing "
                        + str(
                            path.relative_to(
                                root
                            )
                        )
                        + ": "
                        + f"{type(rollback_exc).__name__}: {rollback_exc}"
                    )

        write_text(
            error,
            "\n".join(
                [
                    "LLM Gateway Transport Reliability v1 FAILED",
                    f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
                    "",
                    "Exception:",
                    f"{type(exc).__name__}: {exc}",
                    "",
                    "Traceback:",
                    traceback.format_exc(),
                    "",
                    "ROLLBACK",
                    "=" * 120,
                    *rollback,
                    "",
                    "PARTIAL REPORT",
                    "=" * 120,
                    *report,
                ]
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "LLM GATEWAY TRANSPORT RELIABILITY V1 FAILED"
        )
        print("=" * 72)
        print("")
        print(
            "Modified files were rolled back where possible."
        )
        print("")
        print("Upload only:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
