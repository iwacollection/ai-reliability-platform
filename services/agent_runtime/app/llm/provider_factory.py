from common.config import get_settings

from services.agent_runtime.app.llm.factory import (
    create_llm_registry,
)

from services.agent_runtime.app.llm.base import (
    BaseLLMProvider,
)


def create_llm_provider(
    provider_name: str | None = None,
) -> BaseLLMProvider:
    # Default behavior remains application-config driven.
    # Explicit override is only for bounded, intentional entrypoints.
    settings = get_settings()

    if provider_name is None:
        resolved_provider = settings.llm.provider
    else:
        if not isinstance(provider_name, str):
            raise TypeError(
                "LLM provider override must be text"
            )

        resolved_provider = provider_name.strip()

        if not resolved_provider:
            raise ValueError(
                "LLM provider override cannot be blank"
            )

    registry = create_llm_registry()

    return registry.get(
        resolved_provider,
    )
