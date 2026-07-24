import asyncio

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

from datetime import UTC, datetime

from services.agent_runtime.app.agents.noise.agent import (
    NoiseAgent,
)

from services.agent_runtime.app.llm.client import (
    LLMClient,
)

from services.agent_runtime.app.llm.provider_factory import (
    create_llm_provider,
)

from services.agent_runtime.app.model.context import (
    AgentContext,
)


def test_noise_agent():

    async def run():

        provider = create_llm_provider()

        client = LLMClient(
            provider,
        )

        agent = NoiseAgent(
            client,
        )

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

        context = AgentContext(
            event=event,
        )

        result = await agent.run(
            context,
        )

        print()

        print(
            result.model_dump()
        )

        assert result.success is True

        assert result.agent == "noise"


    asyncio.run(run())