from services.agent_runtime.app.agents.noise.agent import (
    NoiseAgent,
)

from services.agent_runtime.app.agents.rca.agent import (
    RCAAgent,
)

from services.agent_runtime.app.agents.healing.agent import (
    HealingAgent,
)

from services.agent_runtime.app.llm.client import (
    LLMClient,
)

from services.agent_runtime.app.llm.provider_factory import (
    create_llm_provider,
)

from services.agent_runtime.app.registry.agent_registry import (
    AgentRegistry,
)


def create_agent_registry() -> AgentRegistry:
    """
    Create and initialize agent registry.
    """

    registry = AgentRegistry()


    provider = create_llm_provider()


    llm_client = LLMClient(
        provider,
    )


    registry.register(
        NoiseAgent(
            llm_client,
        )
    )


    registry.register(
        RCAAgent(
            llm_client,
        )
    )


    registry.register(
        HealingAgent(
            llm_client,
        )
    )


    return registry