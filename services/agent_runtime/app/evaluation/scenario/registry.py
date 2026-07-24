from services.agent_runtime.app.evaluation.scenario.models import (
    ScenarioDefinition,
)



class ScenarioRegistry:
    """
    Scenario registry.

    Manage evaluation scenarios.
    """


    def __init__(self):

        self._scenarios: dict[str, ScenarioDefinition] = {}



    def register(
        self,
        scenario: ScenarioDefinition,
    ) -> None:

        self._scenarios[
            scenario.name
        ] = scenario



    def get(
        self,
        name: str,
    ) -> ScenarioDefinition:

        return self._scenarios[name]



    def list(
        self,
    ) -> list[ScenarioDefinition]:

        return list(
            self._scenarios.values()
        )