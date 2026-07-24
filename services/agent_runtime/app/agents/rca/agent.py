from services.agent_runtime.app.agent.base import (
    BaseAgent,
)

from services.agent_runtime.app.llm.client import (
    LLMClient,
)

from services.agent_runtime.app.llm.models import (
    ChatRequest,
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
    """


    def __init__(
        self,
        llm_client: LLMClient,
    ) -> None:

        self.llm_client = llm_client


    @property
    def name(self) -> str:

        return "rca"


    async def run(
        self,
        context: AgentContext,
    ) -> AgentResult:


        metrics = {}


        if context.tools:

            metrics = await context.tools.call(
                "prometheus",
                query="service_health",
            )


        prompt = build_rca_prompt(
            context.event,
            metrics,
        )


        response = await self.llm_client.chat(
            ChatRequest(
                system_prompt=(
                    "You are an SRE RCA assistant."
                ),
                user_prompt=prompt,
            )
        )


        result = parse_rca_result(
            response.content,
        )


        # 保存观测数据
        context.variables[
            "rca_metrics"
        ] = metrics


        return result