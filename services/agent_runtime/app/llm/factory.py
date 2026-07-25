from services.agent_runtime.app.llm.registry import (
    LLMProviderRegistry,
)


from services.agent_runtime.app.llm.providers.mock import (
    MockProvider,
)


from services.agent_runtime.app.llm.providers.openai_compatible import (
    OpenAICompatibleProvider,
)



def create_llm_registry() -> LLMProviderRegistry:
    """
    Create default LLM provider registry.
    """


    registry = LLMProviderRegistry()



    registry.register(
        MockProvider(),
    )


    registry.register(
        OpenAICompatibleProvider(),
    )



    return registry