from services.agent_runtime.app.registry.agent_registry import (
    AgentRegistry,
)

from services.agent_runtime.app.planner.models import (
    AgentDependency,
)


class AgentPlanner:
    """
    Build execution order from agent dependencies.
    """


    def analyze(
        self,
        registry: AgentRegistry,
    ) -> list[AgentDependency]:

        results = []

        for agent in registry.list_agents():

            metadata = agent.metadata()

            results.append(
                AgentDependency(
                    agent=agent.name,
                    missing=[],
                    satisfied=list(
                        metadata.get(
                            "depends_on",
                            [],
                        )
                    ),
                )
            )

        return results



    def build_execution_order(
        self,
        registry: AgentRegistry,
    ) -> list[str]:

        agents = {
            agent.name: agent
            for agent in registry.list_agents()
        }


        metadata = {
            name: agent.metadata()
            for name, agent in agents.items()
        }


        executed = set()

        order = []


        while len(order) < len(agents):

            progress = False


            for name, meta in metadata.items():

                if name in executed:
                    continue


                dependencies = (
                    meta.get(
                        "depends_on",
                        [],
                    )
                )


                # 当前 agent 依赖已经满足
                if all(
                    dep in executed
                    or dep in self._provided_by(
                        executed,
                        metadata,
                    )
                    for dep in dependencies
                ):

                    order.append(name)

                    executed.add(name)

                    progress = True


            if not progress:
                raise RuntimeError(
                    "Circular dependency detected"
                )


        return order



    def _provided_by(
        self,
        executed,
        metadata,
    ):

        provided = set()


        for name in executed:

            provided.update(
                metadata[name]
                .get(
                    "provides",
                    [],
                )
            )


        return provided