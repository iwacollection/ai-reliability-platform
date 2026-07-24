from typing import Callable

from services.agent_runtime.app.agent.base import BaseAgent


class AgentNode:
    """
    Agent execution node.
    """

    def __init__(
        self,
        agent: BaseAgent,
    ) -> None:

        self.agent = agent

        self.next_nodes: list[
            tuple[
                "AgentNode",
                Callable | None,
            ]
        ] = []


    def connect(
        self,
        node: "AgentNode",
        condition: Callable | None = None,
    ) -> None:

        self.next_nodes.append(
            (
                node,
                condition,
            )
        )