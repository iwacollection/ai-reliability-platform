import asyncio

from datetime import UTC, datetime

from common.domain.event import (
    Header,
    Resource,
    Signal,
    StandardEvent,
)

from common.domain.event.enums import (
    EventSource,
    ResourceKind,
    Severity,
    SignalType,
)

from services.agent_runtime.app.evaluation.report.generator import (
    EvaluationReportGenerator,
)

from services.agent_runtime.app.model.context import (
    AgentContext,
)

from services.agent_runtime.app.runtime.decision_runtime import (
    DecisionRuntime,
)

from services.agent_runtime.app.runtime.inspector import (
    RuntimeInspector,
)

from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)


async def run_action_and_verification_demo(
    runtime: AgentRuntime,
    context: AgentContext,
    healing_result: dict,
) -> None:
    """
    Demonstrate the complete remediation lifecycle.

    This function explicitly simulates human approval, then delegates
    post-remediation checks to the shared evidence-backed Coordinator.

    Mock or dry-run evidence remains untrusted and therefore cannot resolve
    the Incident. This is a local demonstration flow, not a production
    automatic-approval implementation.
    """

    event_resource = (
        context.event.resources[0]
        if context.event.resources
        else None
    )

    plan, execution = (
        await runtime.action_runtime.execute(
            healing_result,
            incident=context.incident,
            namespace=(
                event_resource.namespace
                if event_resource is not None
                else None
            ),
            cluster=(
                event_resource.cluster
                if event_resource is not None
                else None
            ),
        )
    )

    print()

    print(
        "Action Plan"
    )

    print(
        plan.model_dump(
            mode="json"
        )
    )

    print()

    print(
        "Initial Execution Result"
    )

    print(
        execution
    )

    print()

    print(
        "Incident State Before Approval"
    )

    print(
        context.incident.model_dump(
            mode="json"
        )
    )

    if (
        execution.get("status")
        == "pending_approval"
    ):
        approval_id = execution[
            "approval_id"
        ]

        print()

        print(
            "DEMO ONLY: Simulate Human Approval"
        )

        approval = await (
            runtime.approval.approve(
                approval_id
            )
        )

        print(
            approval.model_dump(
                mode="json"
            )
        )

        execution = await (
            runtime.action_runtime.resume(
                approval_id,
                incident=context.incident,
                operator_id="main_demo_operator",
                idempotency_key=(
                    "main-demo-action:"
                    f"{approval_id}"
                ),
            )
        )

        print()

        print(
            "Resumed Execution Result"
        )

        print(
            execution
        )

        print()

        print(
            "Incident State After Action"
        )

        print(
            context.incident.model_dump(
                mode="json"
            )
        )

    if (
        execution.get("success")
        is not True
    ):
        print()

        print(
            "Verification Skipped"
        )

        print(
            {
                "reason": (
                    "Action execution did "
                    "not succeed"
                ),
                "execution": execution,
            }
        )

        return

    print()

    print(
        "Run Evidence-backed Verification"
    )

    verification, incident = await (
        runtime.verification_coordinator.run(
            incident_id=(
                context.incident.id
            ),
            plan=plan,
            namespace=plan.namespace,
            cluster=plan.cluster,
            context=context,
            metadata={
                "source": "main_demo",
                "trigger": (
                    "post_action_execution"
                ),
                "action_execution_id": (
                    execution.get(
                        "execution_id"
                    )
                ),
            },
        )
    )

    context.incident = incident

    print()

    print(
        "Verification Result"
    )

    print(
        verification.model_dump(
            mode="json"
        )
    )

    print()

    print(
        "Final Incident State"
    )

    print(
        context.incident.model_dump(
            mode="json"
        )
    )


async def main():
    event = StandardEvent(
        header=Header(
            source=(
                EventSource.ALERTMANAGER
            ),
            occurred_at=datetime.now(
                UTC
            ),
        ),
        signal=Signal(
            type=SignalType.ALERT,
            name="PodHighCPU",
            severity=Severity.CRITICAL,
            message="CPU > 90%",
        ),
        resources=[
            Resource(
                kind=ResourceKind.POD,
                name="payment-api",
                namespace="payment",
                cluster="production-a",
            )
        ],
    )

    runtime = AgentRuntime()

    context = AgentContext(
        event=event,
        memory=runtime.memory,
        tools=runtime.tools,
        skills=runtime.skills,
    )

    registry = runtime.registry

    inspector = RuntimeInspector(
        registry
    )

    inspector.print_report()

    print()

    print(
        "Build execution pipeline"
    )


    results = await runtime.execute(
        context
    )

    print()

    print(
        "Run Pipeline Again"
    )

    await runtime.execute(
        context
    )

    print()

    print(
        "=" * 80
    )

    print(
        "Pipeline Finished"
    )

    print(
        "=" * 80
    )

    for result in results:
        print(
            result.model_dump()
        )

    healing_result = (
        context.results.get(
            "healing"
        )
    )

    if healing_result:
        await (
            run_action_and_verification_demo(
                runtime=runtime,
                context=context,
                healing_result=healing_result,
            )
        )

    decision_runtime = (
        DecisionRuntime()
    )

    decision = (
        decision_runtime.evaluate(
            results
        )
    )

    print()

    print(
        "Final Decision"
    )

    print(
        decision
    )

    print()

    print(
        "Memory Check"
    )

    memory_data = (
        await context.memory.get(
            "incident:payment-api:"
            "PodHighCPU"
        )
    )

    print(
        memory_data
    )

    print()

    print(
        "Trace Check"
    )

    for trace in runtime.tracer.list():
        print(
            trace.model_dump()
        )

    print()

    print(
        "Execution Record Check"
    )

    for execution_record in (
        context.executions
    ):
        print(
            execution_record.model_dump()
        )

    print()

    print(
        "Evaluation Check"
    )

    for evaluation in (
        context.evaluations
    ):
        print(
            evaluation.model_dump()
        )

    print()

    print(
        "Evaluation Report"
    )

    report_generator = (
        EvaluationReportGenerator()
    )

    report = (
        report_generator.generate(
            context.evaluations
        )
    )

    print(
        report.model_dump()
    )


if __name__ == "__main__":
    asyncio.run(
        main()
    )
