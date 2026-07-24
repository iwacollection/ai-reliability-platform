from __future__ import annotations

from services.agent_runtime.app.agent.base import BaseAgent


class AgentRegistry:
    """
    Registry for all agents.
    """

    def __init__(self) -> None:

        self._agents: dict[str, BaseAgent] = {}


    def register(
        self,
        agent: BaseAgent,
    ) -> None:

        self._agents[
            agent.name
        ] = agent


    def get(
        self,
        name: str,
    ) -> BaseAgent:

        if name not in self._agents:
            raise KeyError(
                f"Agent '{name}' not found."
            )

        return self._agents[name]


    def list_agents(
        self,
    ) -> list[BaseAgent]:

        return list(
            self._agents.values()
        )


    def names(
        self,
    ) -> list[str]:

        return list(
            self._agents.keys()
        )