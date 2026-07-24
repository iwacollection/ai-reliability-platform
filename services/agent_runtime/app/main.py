from services.agent_runtime.app.model.result import (
    AgentResult,
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

from services.agent_runtime.app.agents.noise.agent import NoiseAgent
from services.agent_runtime.app.model.context import AgentContext
from services.agent_runtime.app.graph.factory import (
    create_agent_graph,
)
from services.agent_runtime.app.registry.factory import (
    create_agent_registry,
)

from services.agent_runtime.app.aggregator.simple import (
    SimpleAggregator,
)

from services.agent_runtime.app.memory.store import (
    MemoryStore,
)

from services.agent_runtime.app.action.planner import (
    ActionPlanner,
)

from services.agent_runtime.app.action.mock_executor import (
    MockExecutor,
)

from services.agent_runtime.app.tools.factory import (
    create_tool_manager,
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

    memory = MemoryStore()


    tool_manager = create_tool_manager()


    context = AgentContext(
        event=event,
        memory=memory,
        tools=tool_manager,
    )

    registry = create_agent_registry()

    print(registry.names())

    pipeline = create_agent_graph(
        registry,
    )

    results = await pipeline.execute(
        context,
    )

    print()
    print("=" * 80)
    print("Pipeline Finished")
    print("=" * 80)

    for result in results:
        print(result.model_dump())

    healing_result = context.results.get(
        "healing"
    )


    if healing_result:

        planner = ActionPlanner()

        plan = planner.create_plan(
            AgentResult(
                **healing_result
            )
        )


        print()

        print("Action Plan")

        print(
            plan.model_dump()
        )


        executor = MockExecutor(
            tool_manager
        )


        execution = await executor.execute(
            plan
        )


        print()

        print("Execution Result")

        print(
            execution
        )

    aggregator = SimpleAggregator()

    decision = aggregator.aggregate(
        results,
    )

    print()

    print("Final Decision")

    print(decision)

    print()
    print("Context Results")
    print(context.results)

    print()

    print("Tool Test")


    tool_result = await context.tools.call(
        "echo",
        message="tool connected",
    )


    print(tool_result)


if __name__ == "__main__":
    asyncio.run(main())