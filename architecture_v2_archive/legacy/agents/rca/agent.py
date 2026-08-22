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

from services.agent_runtime.app.agents.rca.prompt import (
    build_rca_prompt,
)

from services.agent_runtime.app.agents.rca.parser import (
    parse_rca_result,
)



class RCAAgent(BaseAgent):
    """
    Root cause analysis agent.

    Analyze incident based on alert
    and collected evidence.
    """


    @property
    def agent_type(self):

        return "analysis"



    @property
    def depends_on(self):

        return [
            "kubernetes_evidence",
            "deployment_change",
        ]



    @property
    def provides(self):

        return [
            "root_cause"
        ]



    def __init__(
        self,
        llm_gateway: LLMGateway,
    ) -> None:

        self.llm_gateway = llm_gateway



    @property
    def name(
        self,
    ) -> str:

        return "rca"



    async def run(
        self,
        context: AgentContext,
    ) -> AgentResult:


        #
        # Collect evidence
        #

        evidence = []


        kubernetes_evidence = context.variables.get(
            "evidence",
            [],
        )


        deployment_change = context.variables.get(
            "deployment_change",
        )


        evidence.extend(
            kubernetes_evidence
        )


        if deployment_change:

            evidence.append(
                deployment_change
            )



        #
        # Load historical memory
        #

        history = None


        if context.memory:


            service = (
                context
                .event
                .resources[0]
                .name
            )


            memory_key = (
                f"incident:{service}:"
                f"{context.event.signal.name}"
            )


            history = await context.memory.get(
                memory_key
            )



        if history:

            print()

            print(
                "Historical Memory"
            )

            print(
                history
            )



        #
        # Build RCA prompt
        #

        prompt = build_rca_prompt(
            context.event,
            evidence,
            history,
        )



        response = await self.llm_gateway.chat(

            LLMGatewayRequest(

                system_prompt=(

                    "You are an SRE RCA assistant. "

                    "Analyze incidents using evidence "

                    "and historical incidents."

                ),


                prompt=prompt,


                context=LLMInvocationContext(

                    agent="rca",

                    task=LLMTaskType.RCA,

                    priority=LLMPriority.HIGH,

                    require_json=True,

                ),

            )

        )



        result = parse_rca_result(
            response.content,
        )



        #
        # Save current RCA result
        #

        context.variables[
            "rca"
        ] = result.data



        #
        # Save to memory
        #

        if context.memory:


            service = (
                context
                .event
                .resources[0]
                .name
            )


            memory_key = (
                f"incident:{service}:"
                f"{context.event.signal.name}"
            )


            await context.memory.save(
                memory_key,
                result.data,
            )



        return result