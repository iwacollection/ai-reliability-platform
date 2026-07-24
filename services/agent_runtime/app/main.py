from services.agent_runtime.app.runtime.inspector import (
    RuntimeInspector,
)

from services.agent_runtime.app.runtime.action_runtime import (
    ActionRuntime,
)

from services.agent_runtime.app.runtime.decision_runtime import (
    DecisionRuntime,
)


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

from services.agent_runtime.app.model.context import (
    AgentContext,
)

from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)

from services.agent_runtime.app.evaluation.report.generator import (
    EvaluationReportGenerator,
)



async def main():

    event = StandardEvent(

        header=Header(
            source=EventSource.ALERTMANAGER,
            occurred_at=datetime.now(UTC),
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
        registry,
    )


    inspector.print_report()



    print()

    print(
        "Build execution pipeline"
    )



    pipeline = runtime.pipeline



    results = await pipeline.execute(
        context,
    )



    print()

    print(
        "Run Pipeline Again"
    )



    await pipeline.execute(
        context,
    )



    print()

    print("=" * 80)

    print(
        "Pipeline Finished"
    )

    print("=" * 80)



    for result in results:

        print(
            result.model_dump()
        )



    healing_result = context.results.get(
        "healing"
    )



    if healing_result:


        action_runtime = ActionRuntime()



        plan, execution = await action_runtime.execute(
            healing_result
        )



        print()

        print(
            "Action Plan"
        )


        print(
            plan.model_dump()
        )



        print()

        print(
            "Execution Result"
        )


        print(
            execution
        )



    decision_runtime = DecisionRuntime()



    decision = decision_runtime.evaluate(
        results,
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



    memory_data = await context.memory.get(
        "incident:payment-api:PodHighCPU"
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



    for execution_record in context.executions:

        print(
            execution_record.model_dump()
        )



    print()

    print(
        "Evaluation Check"
    )


    for evaluation in context.evaluations:

        print(
            evaluation.model_dump()
        )



    print()

    print(
        "Evaluation Report"
    )


    report_generator = EvaluationReportGenerator()


    report = report_generator.generate(
        context.evaluations
    )


    print(
        report.model_dump()
    )



if __name__ == "__main__":

    asyncio.run(main())