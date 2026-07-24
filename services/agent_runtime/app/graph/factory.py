from services.agent_runtime.app.graph.node import (
    AgentNode,
)

from services.agent_runtime.app.graph.graph import (
    AgentGraph,
)


def create_agent_graph(
    registry,
):

    noise = AgentNode(
        registry.get(
            "noise"
        )
    )


    rca = AgentNode(
        registry.get(
            "rca"
        )
    )


    healing = AgentNode(
        registry.get(
            "healing"
        )
    )


    def need_rca(
        result,
    ):

        return (
            result.message
            == "Real Alert"
        )


    noise.connect(
        rca,
        condition=need_rca,
    )


    rca.connect(
        healing,
    )


    return AgentGraph(
        noise,
    )