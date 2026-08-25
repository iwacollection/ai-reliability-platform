from services.agent_runtime.app.agent.runtime.scheduler import AgentScheduler
from services.agent_runtime.app.registry.agent_registry import AgentRegistry


class _FakeAgent:
    def __init__(self, name: str, depends_on: list[str], provides: list[str]):
        self.name = name
        self._depends_on = depends_on
        self._provides = provides

    def metadata(self) -> dict:
        return {
            "type": "test",
            "depends_on": self._depends_on,
            "provides": self._provides,
        }


def test_scheduler_calls_metadata_and_orders_capability_dependencies() -> None:
    registry = AgentRegistry()
    registry.register(_FakeAgent("investigation", ["finding"], ["evidence"]))
    registry.register(_FakeAgent("discovery", [], ["finding"]))
    registry.register(_FakeAgent("rca", ["evidence"], ["root_cause"]))

    plan = AgentScheduler(registry).build_plan()

    assert plan == ["discovery", "investigation", "rca"]
