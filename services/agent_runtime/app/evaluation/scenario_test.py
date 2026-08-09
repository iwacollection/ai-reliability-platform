import asyncio

from services.agent_runtime.app.evaluation.assertion.engine import (
    AssertionEngine,
)
from services.agent_runtime.app.evaluation.replay.engine import (
    ScenarioReplayEngine,
)
from services.agent_runtime.app.evaluation.report.generator import (
    EvaluationReportGenerator,
)
from services.agent_runtime.app.evaluation.runner import (
    ScenarioRunner,
)
from services.agent_runtime.app.evaluation.scenario.factory import (
    create_scenario_registry,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)


def assert_action_incident_link(
    item: dict,
) -> dict:
    """
    Verify that the remediation action, approval, and Incident belong to the
    same scenario execution.
    """

    action_result = item.get(
        "action"
    )

    if action_result is None:
        return {
            "checked": False,
            "passed": True,
            "reason": (
                "Scenario did not produce "
                "a remediation action"
            ),
        }

    context = item["context"]
    plan = action_result["plan"]
    execution = action_result["execution"]
    incident_snapshot = action_result[
        "incident"
    ]

    incident_id = str(
        context.incident.id
    )

    assert incident_snapshot["id"] == (
        incident_id
    ), (
        "Replay Incident snapshot does not "
        "match AgentContext Incident"
    )

    assert incident_snapshot["status"] == (
        context.incident.status.value
    ), (
        "Replay Incident status does not "
        "match AgentContext status"
    )

    assert execution.get("incident_id") == (
        incident_id
    ), (
        "Action execution is not linked "
        "to the replay Incident"
    )

    execution_status = execution.get(
        "status"
    )

    if execution_status == "pending_approval":
        approval = execution.get(
            "approval",
            {},
        )

        assert approval.get(
            "incident_id"
        ) == incident_id, (
            "Approval request is not linked "
            "to the replay Incident"
        )

        assert plan["type"] != "none", (
            "Pending approval contains "
            "ActionType.NONE"
        )

        assert plan["target"] != "unknown", (
            "Pending approval contains "
            "an unknown target"
        )

        assert incident_snapshot["status"] == (
            "confirmed"
        ), (
            "Incident waiting for approval "
            "must remain CONFIRMED"
        )

    return {
        "checked": True,
        "passed": True,
        "incident_id": incident_id,
        "incident_status": (
            incident_snapshot["status"]
        ),
        "action": plan["type"],
        "target": plan["target"],
        "execution_status": execution_status,
    }


async def main() -> None:
    runtime = AgentRuntime()

    scenario_registry = (
        create_scenario_registry()
    )

    replay_engine = ScenarioReplayEngine(
        runtime
    )

    runner = ScenarioRunner(
        scenario_registry,
        replay_engine,
    )

    results = await runner.run_all()

    assertion_engine = AssertionEngine()

    print()
    print("=" * 80)
    print("Scenario Replay Finished")
    print("=" * 80)

    for item in results:
        print()
        print(
            "Scenario:",
            item["scenario"],
        )

        context = item["context"]

        report_generator = (
            EvaluationReportGenerator()
        )

        report = report_generator.generate(
            context.evaluations
        )

        print()
        print("Evaluation Report")
        print(
            report.model_dump()
        )

        print()
        print("Scenario Assertion")

        assertion = (
            assertion_engine.assert_scenario(
                scenario_registry.get(
                    item["scenario"]
                ),
                context,
            )
        )

        print(
            assertion.model_dump()
        )

        action_result = item.get(
            "action"
        )

        if action_result:
            print()
            print("Action Plan")
            print(
                action_result["plan"]
            )

            print()
            print("Execution Result")
            print(
                action_result["execution"]
            )

        action_incident_assertion = (
            assert_action_incident_link(
                item
            )
        )

        print()
        print("Action Incident Assertion")
        print(
            action_incident_assertion
        )


if __name__ == "__main__":
    asyncio.run(main())