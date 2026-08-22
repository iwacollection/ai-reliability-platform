from services.agent_runtime.app.evaluation.scenario.registry import (
    ScenarioRegistry,
)

from services.agent_runtime.app.evaluation.replay.engine import (
    ScenarioReplayEngine,
)

from services.agent_runtime.app.investigation.scenario_evaluation_report import (
    build_investigation_scenario_evaluation_report,
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

    async def run_all(
        self,
    ):
        """
        Preserve the existing Scenario Runner contract.

        Returns the original list of replay results.
        """

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

    async def run_all_with_investigation_report(
        self,
    ) -> dict:
        """
        Execute every registered Scenario and attach one aggregate,
        read-only Investigation Shadow evaluation report.

        The underlying replay results are not modified.

        Existing callers of run_all() remain completely unchanged.
        """

        results = await self.run_all()

        report = (
            build_investigation_scenario_evaluation_report(
                results
            )
        )

        return {
            "results": results,
            "investigation_report": report,
        }

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
