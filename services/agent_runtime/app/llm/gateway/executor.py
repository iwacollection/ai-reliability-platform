from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from time import perf_counter

import httpx

from services.agent_runtime.app.llm.client import (
    LLMClient,
)
from services.agent_runtime.app.llm.models import (
    ChatRequest,
    ChatResponse,
)


class LLMExecutionError(
    Exception
):
    """
    Sanitized LLM execution failure.

    Raw provider/transport error text is deliberately not retained in the
    public message because it may contain endpoint or upstream details.
    """

    def __init__(
        self,
        message: str = "LLM execution failed",
        *,
        code: str = "execution_error",
        retryable: bool = False,
        attempts: int = 1,
    ) -> None:
        super().__init__(
            message
        )

        self.code = code
        self.retryable = retryable
        self.attempts = attempts


class LLMExecutor:
    """
    Reliable LLM execution layer.

    Responsibilities:
    - timeout control
    - retry only transient/provider-availability failures
    - exponential backoff with bounded jitter
    - sanitized diagnostics
    - exception normalization

    Not responsible:
    - provider routing
    - provider selection
    - fallback decision
    """

    _RETRYABLE_HTTP_STATUS = {
        408,
        425,
        429,
    }

    def __init__(
        self,
        retry_attempts: int = 3,
        timeout: int | float = 30,
        *,
        retry_base_delay: float = 0.25,
        retry_max_delay: float = 2.0,
        retry_jitter_ratio: float = 0.20,
        sleep_func: (
            Callable[
                [float],
                Awaitable[None],
            ]
            | None
        ) = None,
        random_func: (
            Callable[
                [],
                float,
            ]
            | None
        ) = None,
    ) -> None:
        if retry_attempts < 1:
            raise ValueError(
                "retry_attempts must be >= 1"
            )

        if timeout <= 0:
            raise ValueError(
                "timeout must be > 0"
            )

        if retry_base_delay < 0:
            raise ValueError(
                "retry_base_delay must be >= 0"
            )

        if retry_max_delay < 0:
            raise ValueError(
                "retry_max_delay must be >= 0"
            )

        if not (
            0.0
            <= retry_jitter_ratio
            <= 1.0
        ):
            raise ValueError(
                "retry_jitter_ratio must be within [0,1]"
            )

        self.retry_attempts = (
            retry_attempts
        )

        self.timeout = float(
            timeout
        )

        self.retry_base_delay = float(
            retry_base_delay
        )

        self.retry_max_delay = float(
            retry_max_delay
        )

        self.retry_jitter_ratio = float(
            retry_jitter_ratio
        )

        self._sleep_func = (
            sleep_func
            or asyncio.sleep
        )

        self._random_func = (
            random_func
            or random.random
        )

    async def execute(
        self,
        client: LLMClient,
        request: ChatRequest,
    ) -> ChatResponse:
        """
        Execute one logical Gateway request.

        retry_attempts is the total number of provider attempts, not the
        number of retries after the first attempt.
        """

        for attempt_index in range(
            self.retry_attempts
        ):
            start = perf_counter()

            try:
                response = await asyncio.wait_for(
                    client.chat(
                        request
                    ),
                    timeout=self.timeout,
                )

                _duration_ms = round(
                    (
                        perf_counter()
                        - start
                    )
                    * 1000,
                    4,
                )

                return response

            except Exception as exc:
                attempt_number = (
                    attempt_index
                    + 1
                )

                retryable = (
                    self._is_retryable_error(
                        exc
                    )
                )

                code = self._error_code(
                    exc
                )

                # Intentionally sanitized: never log str(exc), URL, headers,
                # credentials or response body here.
                print(
                    "LLM execution failed",
                    {
                        "attempt": attempt_number,
                        "error_type": (
                            type(exc).__name__
                        ),
                        "code": code,
                        "retryable": retryable,
                    },
                )

                final_attempt = (
                    attempt_number
                    >= self.retry_attempts
                )

                if (
                    not retryable
                    or final_attempt
                ):
                    raise LLMExecutionError(
                        (
                            "LLM execution failed "
                            f"[{code}] after "
                            f"{attempt_number} attempt(s)"
                        ),
                        code=code,
                        retryable=retryable,
                        attempts=attempt_number,
                    ) from exc

                delay = (
                    self._retry_delay(
                        attempt_index
                    )
                )

                await self._sleep_func(
                    delay
                )

        raise AssertionError(
            "LLMExecutor retry loop exited unexpectedly"
        )

    def _retry_delay(
        self,
        attempt_index: int,
    ) -> float:
        base = min(
            self.retry_max_delay,
            (
                self.retry_base_delay
                * (
                    2
                    ** attempt_index
                )
            ),
        )

        jitter_window = (
            base
            * self.retry_jitter_ratio
        )

        random_value = min(
            1.0,
            max(
                0.0,
                float(
                    self._random_func()
                ),
            ),
        )

        jitter = (
            (
                random_value
                * 2.0
            )
            - 1.0
        ) * jitter_window

        return max(
            0.0,
            min(
                self.retry_max_delay,
                base + jitter,
            ),
        )

    @classmethod
    def _is_retryable_error(
        cls,
        exc: Exception,
    ) -> bool:
        if isinstance(
            exc,
            (
                asyncio.TimeoutError,
                httpx.TimeoutException,
                httpx.TransportError,
            ),
        ):
            return True

        if isinstance(
            exc,
            httpx.HTTPStatusError,
        ):
            status = (
                exc.response.status_code
            )

            return (
                status
                in cls._RETRYABLE_HTTP_STATUS
                or (
                    500
                    <= status
                    <= 599
                )
            )

        return False

    @classmethod
    def _error_code(
        cls,
        exc: Exception,
    ) -> str:
        if isinstance(
            exc,
            (
                asyncio.TimeoutError,
                httpx.TimeoutException,
            ),
        ):
            return "timeout"

        if isinstance(
            exc,
            httpx.HTTPStatusError,
        ):
            return (
                "http_"
                + str(
                    exc.response.status_code
                )
            )

        if isinstance(
            exc,
            httpx.TransportError,
        ):
            return "transport_error"

        return "non_retryable_error"


__all__ = [
    "LLMExecutionError",
    "LLMExecutor",
]
