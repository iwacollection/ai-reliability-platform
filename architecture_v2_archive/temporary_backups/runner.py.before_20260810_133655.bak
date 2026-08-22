from services.agent_runtime.app.evaluation.scenario.registry import (
    ScenarioRegistry,
)


from services.agent_runtime.app.evaluation.replay.engine import (
    ScenarioReplayEngine,
)



class ScenarioRunner:
    """
    Execute evaluation scenarios.

    Used for:
    - Harness testing
    - Regression testing
    - Agent validation
    """



    def __init__(
        self,
        registry: ScenarioRegistry,
        replay_engine: ScenarioReplayEngine,
    ):

        self.registry = registry

        self.replay_engine = replay_engine



    async def run_all(self):

        results = []


        for scenario in self.registry.list():


            print(
                "RUN SCENARIO:",
                scenario.name,
            )


            result = await (
                self.replay_engine.replay(
                    scenario
                )
            )


            results.append(
                result
            )


        return results



    async def run(
        self,
        name: str,
    ):


        scenario = self.registry.get(
            name
        )


        return await (
            self.replay_engine.replay(
                scenario
            )
        )