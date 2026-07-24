from services.agent_runtime.app.registry.agent_registry import (
    AgentRegistry,
)


class AgentScheduler:
    """
    Dynamic dependency based agent scheduler.

    Decide execution order according to
    agent metadata.
    """


    def __init__(
        self,
        registry: AgentRegistry,
    ):

        self.registry = registry



    def build_plan(self) -> list[str]:
        """
        Build execution order.

        Example:

        noise
          |
        diagnosis
          |
        rca
          |
        healing

        """

        agents = (
            self.registry
            .list_agents()
        )


        execution_order = []

        provided_capabilities = set()


        while len(execution_order) < len(agents):

            progress = False


            for agent in agents:

                if agent.name in execution_order:
                    continue


                metadata = (
                    agent.metadata
                )


                dependencies = (
                    metadata
                    .get(
                        "depends_on",
                        []
                    )
                )


                if set(dependencies).issubset(
                    provided_capabilities
                ):

                    execution_order.append(
                        agent.name
                    )


                    provided_capabilities.update(
                        metadata
                        .get(
                            "provides",
                            []
                        )
                    )


                    progress = True



            if not progress:

                # dependency cannot be resolved
                break



        return execution_order