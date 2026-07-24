from services.agent_runtime.app.graph.node import (
    AgentNode,
)

from services.agent_runtime.app.model.context import (
    AgentContext,
)

from services.agent_runtime.app.model.result import (
    AgentResult,
)

from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)


class AgentGraph:
    """
    Execute agents by graph.
    """


    def __init__(
        self,
        root: AgentNode,
    ) -> None:

        self.root = root


    async def execute(
        self,
        context: AgentContext,
    ) -> list[AgentResult]:

        results: list[AgentResult] = []

        await self._execute_node(
            self.root,
            context,
            results,
        )

        return results


    async def _execute_node(
        self,
        node: AgentNode,
        context: AgentContext,
        results: list[AgentResult],
    ):

        # Agent开始执行
        context.incident.update(
            IncidentStatus.ANALYZING,
            f"Running {node.agent.name}",
        )


        result = await node.agent.run(
            context,
        )


        results.append(
            result
        )


        # 保存Agent结果
        context.results[
            node.agent.name
        ] = result.model_dump()


        # 根据Agent结果更新Incident状态
        if result.success:

            if node.agent.name == "healing":

                context.incident.update(
                    IncidentStatus.RESOLVED,
                    result.message,
                )

            else:

                context.incident.update(
                    IncidentStatus.CONFIRMED,
                    result.message,
                )

        else:

            context.incident.update(
                IncidentStatus.FAILED,
                result.message,
            )


        # 执行后续节点
        for (
            next_node,
            condition,
        ) in node.next_nodes:


            if condition is None:

                await self._execute_node(
                    next_node,
                    context,
                    results,
                )


            elif condition(
                result
            ):

                await self._execute_node(
                    next_node,
                    context,
                    results,
                )