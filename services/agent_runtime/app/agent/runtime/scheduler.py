from services.agent_runtime.app.registry.agent_registry import AgentRegistry


class AgentScheduler:
    """Dynamic dependency-based agent scheduler."""

    def __init__(self, registry: AgentRegistry):
        self.registry = registry

    def build_plan(self) -> list[str]:
        """Build execution order from declared capability dependencies."""

        agents = self.registry.list_agents()
        execution_order: list[str] = []
        provided_capabilities: set[str] = set()

        while len(execution_order) < len(agents):
            progress = False

            for agent in agents:
                if agent.name in execution_order:
                    continue

                metadata = agent.metadata()
                dependencies = metadata.get("depends_on", [])

                if set(dependencies).issubset(provided_capabilities):
                    execution_order.append(agent.name)
                    provided_capabilities.update(metadata.get("provides", []))
                    progress = True

            if not progress:
                # Dependency graph contains an unresolved capability or cycle.
                break

        return execution_order
