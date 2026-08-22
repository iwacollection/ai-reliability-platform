from services.agent_runtime.app.registry.agent_registry import (
    AgentRegistry,
)

from services.agent_runtime.app.planner.agent_planner import (
    AgentPlanner,
)


class RuntimeInspector:
    """
    Inspect agent runtime.

    Used for:
    - agent metadata
    - dependency analysis
    """


    def __init__(
        self,
        registry: AgentRegistry,
    ):

        self.registry = registry

        self.planner = AgentPlanner()



    def metadata(self) -> dict:

        return self.registry.all_metadata()



    def dependencies(self):

        return self.planner.analyze(
            self.registry
        )



    def print_report(self):

        print()

        print(
            "Agent Metadata"
        )

        print(
            self.metadata()
        )


        print()

        print(
            "Dependency Analysis"
        )


        for dependency in self.dependencies():

            print(
                dependency
            )


        print(
            self.registry.names()
        )