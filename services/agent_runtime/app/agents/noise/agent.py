from services.agent_runtime.app.agent.base import (
    BaseAgent,
)

from services.agent_runtime.app.llm.gateway.gateway import (
    LLMGateway,
)

from services.agent_runtime.app.llm.gateway.models import (
    LLMGatewayRequest,
    LLMInvocationContext,
    LLMTaskType,
    LLMPriority,
)

from services.agent_runtime.app.model.context import (
    AgentContext,
)

from services.agent_runtime.app.model.result import (
    AgentResult,
)

from services.agent_runtime.app.agents.noise.prompt import (
    build_noise_prompt,
)

from services.agent_runtime.app.agents.noise.parser import (
    parse_noise_result,
)



class NoiseAgent(BaseAgent):
    """
    Noise detection agent.
    """



    @property
    def agent_type(self):

        return "general"



    @property
    def depends_on(self):

        return []



    @property
    def provides(self):

        return [
            "alert_classification"
        ]



    def __init__(
        self,
        llm_gateway: LLMGateway,
    ) -> None:

        self.llm_gateway = llm_gateway



    @property
    def name(self) -> str:

        return "noise"



    async def run(
        self,
        context: AgentContext,
    ) -> AgentResult:


        prompt = build_noise_prompt(
            context.event,
        )



        response = await self.llm_gateway.chat(

            LLMGatewayRequest(

                system_prompt=(

                    "You are an SRE assistant."

                ),


                prompt=prompt,


                context=LLMInvocationContext(

                    agent="noise",

                    task=LLMTaskType.CLASSIFICATION,

                    priority=LLMPriority.NORMAL,

                    require_json=True,

                ),

            )

        )



        result = parse_noise_result(
            response.content,
        )


        return result