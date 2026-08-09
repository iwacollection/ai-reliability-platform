from services.agent_runtime.app.llm.client import (
    LLMClient,
)
from services.agent_runtime.app.llm.gateway.gateway import (
    LLMGateway,
)
from services.agent_runtime.app.llm.gateway.router import (
    LLMRouter,
)
from services.agent_runtime.app.llm.observed_client import (
    ObservedLLMClient,
)
from services.agent_runtime.app.llm.provider_factory import (
    create_llm_provider,
)


def create_llm_gateway(
    provider_name: str | None = None,
) -> LLMGateway:
    # provider_name=None preserves the existing application-config behavior.
    # Explicit provider_name is a construction-time override only.
    provider = create_llm_provider(
        provider_name=provider_name,
    )

    base_llm_client = LLMClient(
        provider,
    )

    observed_llm_client = ObservedLLMClient(
        base_llm_client,
    )

    return LLMGateway(
        clients={
            "openai": observed_llm_client,
        },
        router=LLMRouter(),
    )


__all__ = [
    "create_llm_gateway",
]
