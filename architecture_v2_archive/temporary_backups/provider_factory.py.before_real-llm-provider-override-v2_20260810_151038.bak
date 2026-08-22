from common.config import get_settings

from services.agent_runtime.app.llm.factory import (
    create_llm_registry,
)

from services.agent_runtime.app.llm.base import (
    BaseLLMProvider,
)


def create_llm_provider() -> BaseLLMProvider:
    """
    Create LLM provider from application config.
    """

    settings = get_settings()

    registry = create_llm_registry()

    provider = registry.get(
        settings.llm.provider,
    )

    return provider