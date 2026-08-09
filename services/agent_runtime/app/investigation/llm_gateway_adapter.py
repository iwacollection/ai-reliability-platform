from abc import ABC, abstractmethod

from services.agent_runtime.app.llm.gateway.circuit_breaker import (
    CircuitBreakerOpen,
)
from services.agent_runtime.app.llm.gateway.executor import (
    LLMExecutionError,
)
from services.agent_runtime.app.llm.gateway.models import (
    LLMGatewayRequest,
    LLMInvocationContext,
    LLMPriority,
    LLMTaskType,
)
from services.agent_runtime.app.llm.gateway.provider_manager import (
    ProviderNotFoundError,
)
from services.agent_runtime.app.llm.gateway.rate_limiter import (
    RateLimitExceeded,
)


class InvestigationLLMError(RuntimeError):
    """
    Base sanitized error for Investigation LLM access.

    Provider, credential, endpoint and raw model error messages must not
    cross this boundary.
    """


class InvestigationLLMRateLimitError(
    InvestigationLLMError
):
    """
    The shared LLM Gateway rejected the request because of rate limiting.
    """


class InvestigationLLMUnavailableError(
    InvestigationLLMError
):
    """
    The shared LLM Gateway cannot currently provide an available route.
    """


class InvestigationLLMExecutionError(
    InvestigationLLMError
):
    """
    The shared LLM Gateway failed to execute the request successfully.
    """


class InvestigationLLMInvalidResponseError(
    InvestigationLLMError
):
    """
    The shared LLM Gateway returned an unusable response.
    """


class BaseInvestigationLLM(ABC):
    """
    Investigation-owned abstraction for bounded LLM reasoning.

    Investigation reasoners depend on this interface rather than on
    LLMGateway request, routing, provider or execution details.
    """

    @abstractmethod
    async def complete(
        self,
        *,
        system_prompt: str,
        prompt: str,
    ) -> str:
        """
        Return one bounded model response as text.
        """
        ...


class InvestigationLLMGatewayAdapter(
    BaseInvestigationLLM
):
    """
    Adapt the existing shared LLM Gateway for Investigation reasoning.

    This adapter:

    - never constructs an LLM provider;
    - never owns routing or fallback policy;
    - never owns rate-limit or circuit-breaker state;
    - never invokes Investigation probes;
    - never invokes Approval, Incident, Action or Verification;
    - never performs Kubernetes writes;
    - fixes the Investigation Gateway context to bounded analysis;
    - returns only model content to the Investigation reasoner;
    - sanitizes Gateway exceptions at the Investigation boundary.

    The injected Gateway is expected to be the same shared Gateway used by
    the rest of Agent Runtime once composition wiring is added later.
    """

    def __init__(
        self,
        llm_gateway,
    ) -> None:
        if (
            llm_gateway is None
            or not callable(
                getattr(
                    llm_gateway,
                    "chat",
                    None,
                )
            )
        ):
            raise TypeError(
                "Investigation shared LLM gateway is invalid"
            )

        self.llm_gateway = llm_gateway

    async def complete(
        self,
        *,
        system_prompt: str,
        prompt: str,
    ) -> str:
        if (
            not isinstance(system_prompt, str)
            or not system_prompt.strip()
        ):
            raise ValueError(
                "Investigation system prompt is invalid"
            )

        if (
            not isinstance(prompt, str)
            or not prompt.strip()
        ):
            raise ValueError(
                "Investigation prompt is invalid"
            )

        request = LLMGatewayRequest(
            system_prompt=system_prompt,
            prompt=prompt,
            context=LLMInvocationContext(
                agent="investigation",
                task=LLMTaskType.ANALYSIS,
                priority=LLMPriority.HIGH,
                require_json=True,
                preferred_provider=None,
                preferred_model=None,
                enable_fallback=True,
            ),
            temperature=0.0,
        )

        try:
            response = await self.llm_gateway.chat(
                request
            )

        except RateLimitExceeded:
            raise InvestigationLLMRateLimitError(
                "Investigation LLM request was rate limited"
            ) from None

        except CircuitBreakerOpen:
            raise InvestigationLLMUnavailableError(
                "Investigation LLM gateway is unavailable"
            ) from None

        except ProviderNotFoundError:
            raise InvestigationLLMUnavailableError(
                "Investigation LLM provider is unavailable"
            ) from None

        except LLMExecutionError:
            raise InvestigationLLMExecutionError(
                "Investigation LLM execution failed"
            ) from None

        except Exception:
            raise InvestigationLLMError(
                "Investigation LLM request failed"
            ) from None

        content = getattr(
            response,
            "content",
            None,
        )

        if (
            not isinstance(content, str)
            or not content.strip()
        ):
            raise InvestigationLLMInvalidResponseError(
                "Investigation LLM response is invalid"
            )

        return content


__all__ = [
    "BaseInvestigationLLM",
    "InvestigationLLMError",
    "InvestigationLLMExecutionError",
    "InvestigationLLMGatewayAdapter",
    "InvestigationLLMInvalidResponseError",
    "InvestigationLLMRateLimitError",
    "InvestigationLLMUnavailableError",
]
