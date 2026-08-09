from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from services.agent_runtime.app.llm.base import (
    BaseLLMProvider,
)
from services.agent_runtime.app.llm.models import (
    ChatRequest,
    ChatResponse,
)


class OpenAICompatibleProvider(
    BaseLLMProvider
):
    """
    OpenAI-compatible LLM provider.

    One provider instance owns one lazily-created persistent AsyncClient so
    repeated calls reuse the HTTP connection pool.

    Compatible with:
    - OpenAI
    - vLLM
    - Ollama
    - other OpenAI API compatible servers
    """

    @property
    def name(
        self,
    ) -> str:
        return "openai"

    def __init__(
        self,
        *,
        http_client: (
            httpx.AsyncClient
            | None
        ) = None,
    ) -> None:
        self.base_url = os.getenv(
            "OPENAI_BASE_URL",
            "https://api.openai.com/v1",
        ).strip().rstrip("/")

        self.api_key = os.getenv(
            "OPENAI_API_KEY",
            "",
        ).strip()

        self.model = os.getenv(
            "OPENAI_MODEL",
            "gpt-5",
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
        }

        if self.api_key:
            headers[
                "Authorization"
            ] = (
                f"Bearer {self.api_key}"
            )

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

        message = (
            data[
                "choices"
            ][
                0
            ][
                "message"
            ][
                "content"
            ]
        )

        usage = data.get(
            "usage",
            {},
        )

        return ChatResponse(
            content=message,
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
    "OpenAICompatibleProvider",
]
