from collections.abc import Mapping

from services.agent_runtime.app.evaluation.replay.engine import (
    ScenarioReplayEngine,
)
from services.agent_runtime.app.evaluation.scenario.registry import (
    ScenarioRegistry,
)
from services.agent_runtime.app.investigation.scenario_evaluation_report import (
    build_investigation_scenario_evaluation_report,
)
from services.agent_runtime.app.model.context import (
    AgentContext,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)


class InvestigationEvaluationMatrixRunner:
    """
    Run an Investigation evaluation matrix without ActionRuntime.

    The primary Pipeline still executes through AgentRuntime.execute(), so
    RCA vs Investigation comparison is produced exactly as in Runtime.

    Deliberately excluded:

    - ActionRuntime.execute
    - Approval decisions
    - VerificationRuntime
    - Kubernetes production execution
    """

    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        registry: ScenarioRegistry,
    ) -> None:
        self.runtime = runtime
        self.registry = registry

        self.event_builder = (
            ScenarioReplayEngine(
                runtime
            )
        )

    async def run(
        self,
    ) -> dict:
        results = []

        expectations = []

        for scenario in self.registry.list():

            event = (
                self.event_builder.build_event(
                    scenario
                )
            )

            context = AgentContext(
                event=event,
                memory=self.runtime.memory,
                tools=self.runtime.tools,
                skills=self.runtime.skills,
            )

            agent_results = await (
                self.runtime.execute(
                    context
                )
            )

            item = {
                "scenario": scenario.name,
                "results": agent_results,
                "context": context,

                # Matrix is explicitly evaluation-only.
                "action": None,
            }

            results.append(
                item
            )

            expected_status = (
                scenario.metadata.get(
                    "expected_comparison_status"
                )
            )

            comparison = (
                context.metadata.get(
                    "investigation_rca_comparison"
                )
            )

            actual_status = (
                comparison.get(
                    "comparison_status"
                )
                if isinstance(
                    comparison,
                    Mapping,
                )
                else None
            )

            expectations.append(
                {
                    "scenario": (
                        scenario.name
                    ),
                    "expected_status": (
                        expected_status
                    ),
                    "actual_status": (
                        actual_status
                    ),
                    "passed": (
                        actual_status
                        == expected_status
                    ),
                }
            )

        report = (
            build_investigation_scenario_evaluation_report(
                results
            )
        )

        return {
            "schema_version": "v1",
            "fixture_mode": True,
            "read_only": True,
            "decision_influence": False,
            "scenario_count": len(
                results
            ),
            "expectations_passed": all(
                item[
                    "passed"
                ]
                for item
                in expectations
            ),
            "expectations": (
                expectations
            ),
            "results": results,
            "investigation_report": (
                report
            ),
        }


__all__ = [
    "InvestigationEvaluationMatrixRunner",
]
