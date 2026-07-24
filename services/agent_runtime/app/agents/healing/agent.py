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

from services.agent_runtime.app.agents.healing.prompt import (
    build_healing_prompt,
)

from services.agent_runtime.app.agents.healing.parser import (
    parse_healing_result,
)


class HealingAgent(BaseAgent):
    """
    Auto healing suggestion agent.
    """


    def __init__(
        self,
        llm_client: LLMClient,
    ) -> None:

        self.llm_client = llm_client


    @property
    def name(self) -> str:

        return "healing"


    async def run(
        self,
        context: AgentContext,
    ) -> AgentResult:


        prompt = build_healing_prompt(
            context.event,
        )


        response = await self.llm_client.chat(
            ChatRequest(
                system_prompt=(
                    "You are an SRE healing assistant."
                ),
                user_prompt=prompt,
            )
        )


        return parse_healing_result(
            response.content,
        )