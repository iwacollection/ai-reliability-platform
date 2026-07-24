from services.agent_runtime.app.agent.base import BaseAgent
from services.agent_runtime.app.llm.client import LLMClient
from services.agent_runtime.app.llm.models import ChatRequest
from services.agent_runtime.app.model.context import AgentContext
from services.agent_runtime.app.model.result import AgentResult

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
        llm_client: LLMClient,
    ) -> None:

        self.llm_client = llm_client


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

        response = await self.llm_client.chat(
            ChatRequest(
                system_prompt=(
                    "You are an SRE assistant."
                ),
                user_prompt=prompt,
            )
        )

        result = parse_noise_result(
            response.content,
        )

        return result