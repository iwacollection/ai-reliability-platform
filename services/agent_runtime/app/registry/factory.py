from services.agent_runtime.app.agents.change.agent import (
    ChangeAgent,
)

from services.agent_runtime.app.agents.noise.agent import (
    NoiseAgent,
)

from services.agent_runtime.app.agents.rca.agent import (
    RCAAgent,
)

from services.agent_runtime.app.agents.healing.agent import (
    HealingAgent,
)

from services.agent_runtime.app.agents.diagnosis.agent import (
    DiagnosisAgent,
)


from services.agent_runtime.app.llm.client import (
    LLMClient,
)

from services.agent_runtime.app.llm.observed_client import (
    ObservedLLMClient,
)

from services.agent_runtime.app.llm.provider_factory import (
    create_llm_provider,
)


from services.agent_runtime.app.llm.gateway.gateway import (
    LLMGateway,
)


from services.agent_runtime.app.llm.gateway.router import (
    LLMRouter,
)


from services.agent_runtime.app.observation.factory import (
    create_observation_manager,
)

from services.agent_runtime.app.registry.agent_registry import (
    AgentRegistry,
)



def create_agent_registry() -> AgentRegistry:
    """
    Create and initialize agent registry.
    """


    registry = AgentRegistry()



    # =========================
    # LLM Dependency
    # =========================


    provider = create_llm_provider()



    base_llm_client = LLMClient(
        provider,
    )



    observed_llm_client = ObservedLLMClient(
        base_llm_client,
    )



    #
    # LLM Gateway
    #
    # Gateway owns:
    #
    # - routing
    # - provider selection
    #
    #

    llm_gateway = LLMGateway(

        clients={

            "openai":
                observed_llm_client,

        },

        router=LLMRouter(),

    )



    # =========================
    # Observation Dependency
    # =========================


    observation_manager = (
        create_observation_manager()
    )



    # =========================
    # Register Agents
    # =========================


    registry.register(
        NoiseAgent(
            llm_gateway,
        )
    )



    registry.register(
        DiagnosisAgent(
            observation_manager,
        )
    )



    registry.register(
        RCAAgent(
            llm_gateway,
        )
    )



    registry.register(
        HealingAgent(
            llm_gateway,
        )
    )



    registry.register(
        ChangeAgent()
    )


    return registry