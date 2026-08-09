from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib.parse import urlparse

import httpx

from services.agent_runtime.app.llm.base import (
    BaseLLMProvider,
)
from services.agent_runtime.app.llm.models import (
    ChatRequest,
    ChatResponse,
)


class BailianCompatibleProvider(
    BaseLLMProvider
):
    """
    Alibaba Cloud Model Studio (Bailian) OpenAI-compatible provider.

    One provider instance owns one lazily-created persistent AsyncClient.
    Requests therefore reuse the HTTP connection pool and keep-alive
    connections instead of rebuilding the transport for every chat() call.

    Configuration:
    - BAILIAN_BASE_URL
    - DASHSCOPE_API_KEY
    - BAILIAN_MODEL

    BAILIAN_BASE_URL must already include /compatible-mode/v1.
    Configuration is validated only when chat() is invoked so registry
    construction never breaks the safe default mock development path.
    """

    @property
    def name(
        self,
    ) -> str:
        return "bailian"

    def __init__(
        self,
        *,
        http_client: (
            httpx.AsyncClient
            | None
        ) = None,
    ) -> None:
        self.base_url = os.getenv(
            "BAILIAN_BASE_URL",
            "",
        ).strip().rstrip("/")

        self.api_key = os.getenv(
            "DASHSCOPE_API_KEY",
            "",
        ).strip()

        self.model = os.getenv(
            "BAILIAN_MODEL",
            "",
        ).strip()

        self._http_client = (
            http_client
        )

        self._owns_http_client = (
            http_client is None
        )

        self._client_lock = (
            asyncio.Lock()
        )

    def validate_configuration(
        self,
    ) -> None:
        if not self.base_url:
            raise RuntimeError(
                "BAILIAN_BASE_URL is not configured"
            )

        parsed = urlparse(
            self.base_url
        )

        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                "BAILIAN_BASE_URL must be a clean HTTPS URL"
            )

        if not (
            parsed.path.rstrip("/")
            .endswith(
                "/compatible-mode/v1"
            )
        ):
            raise RuntimeError(
                "BAILIAN_BASE_URL must end with /compatible-mode/v1"
            )

        if not self.api_key:
            raise RuntimeError(
                "DASHSCOPE_API_KEY is not configured"
            )

        if not self.model:
            raise RuntimeError(
                "BAILIAN_MODEL is not configured"
            )

    async def _get_http_client(
        self,
    ) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client

        async with self._client_lock:
            if self._http_client is None:
                self._http_client = (
                    httpx.AsyncClient(
                        timeout=httpx.Timeout(
                            30.0,
                            connect=10.0,
                            pool=5.0,
                        ),
                        limits=httpx.Limits(
                            max_connections=20,
                            max_keepalive_connections=10,
                            keepalive_expiry=30.0,
                        ),
                    )
                )

        return self._http_client

    async def _invalidate_http_client(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        """
        Drop an owned persistent pool after protocol/read/write corruption.

        Executor-level retry remains the only retry owner. This method merely
        ensures that the next attempt cannot reuse the same potentially stale
        keep-alive pool.
        """

        if not self._owns_http_client:
            return

        should_close = False

        async with self._client_lock:
            if self._http_client is client:
                self._http_client = None
                should_close = True

        if should_close:
            close = getattr(
                client,
                "aclose",
                None,
            )

            if callable(
                close
            ):
                await close()

    async def aclose(
        self,
    ) -> None:
        if not self._owns_http_client:
            return

        async with self._client_lock:
            client = self._http_client
            self._http_client = None

            if client is not None:
                close = getattr(
                    client,
                    "aclose",
                    None,
                )

                if callable(
                    close
                ):
                    await close()

    async def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:
        self.validate_configuration()

        messages: list[
            dict[str, Any]
        ] = []

        if request.system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": request.system_prompt,
                }
            )

        messages.append(
            {
                "role": "user",
                "content": request.user_prompt,
            }
        )

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": request.temperature,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": (
                f"Bearer {self.api_key}"
            ),
        }

        client = await self._get_http_client()

        try:
            response = await client.post(
                (
                    f"{self.base_url}"
                    "/chat/completions"
                ),
                json=payload,
                headers=headers,
            )

        except (
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.WriteError,
        ):
            await self._invalidate_http_client(
                client
            )
            raise

        response.raise_for_status()

        data = response.json()

        choices = data.get(
            "choices"
        )

        if (
            not isinstance(
                choices,
                list,
            )
            or not choices
            or not isinstance(
                choices[0],
                dict,
            )
        ):
            raise RuntimeError(
                "Bailian response choices are invalid"
            )

        message = choices[0].get(
            "message"
        )

        if not isinstance(
            message,
            dict,
        ):
            raise RuntimeError(
                "Bailian response message is invalid"
            )

        content = message.get(
            "content"
        )

        if (
            not isinstance(
                content,
                str,
            )
            or not content.strip()
        ):
            raise RuntimeError(
                "Bailian response content is invalid"
            )

        usage = data.get(
            "usage",
            {},
        )

        if not isinstance(
            usage,
            dict,
        ):
            usage = {}

        return ChatResponse(
            content=content,
            model=data.get(
                "model",
                self.model,
            ),
            prompt_tokens=usage.get(
                "prompt_tokens",
                0,
            ),
            completion_tokens=usage.get(
                "completion_tokens",
                0,
            ),
            total_tokens=usage.get(
                "total_tokens",
                0,
            ),
        )


__all__ = [
    "BailianCompatibleProvider",
]
