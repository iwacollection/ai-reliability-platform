import json

import pytest

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

from services.agent_runtime.app.agents.noise.agent import (
    NoiseAgent,
)
from services.agent_runtime.app.llm.gateway.models import (
    LLMGatewayRequest,
    LLMGatewayResponse,
)
from services.agent_runtime.app.model.context import (
    AgentContext,
)


class MockNoiseGateway:
    """
    Lightweight LLM Gateway used by the NoiseAgent unit test.

    This mock keeps the same chat contract as LLMGateway without invoking
    provider routing, fallback, rate limiting, or an external model.
    """

    def __init__(self) -> None:
        self.last_request: (
            LLMGatewayRequest | None
        ) = None

    async def chat(
        self,
        request: LLMGatewayRequest,
    ) -> LLMGatewayResponse:
        self.last_request = request

        content = json.dumps(
            {
                "noise": False,
                "confidence": 0.95,
                "reason": (
                    "Critical production alert."
                ),
            }
        )

        return LLMGatewayResponse(
            content=content,
            provider="mock",
            model="mock-model",
            fallback_used=False,
        )


@pytest.mark.asyncio
async def test_noise_agent():
    gateway = MockNoiseGateway()

    agent = NoiseAgent(
        gateway,
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

    assert result.success is True
    assert result.agent == "noise"

    assert gateway.last_request is not None

    assert gateway.last_request.context.agent == (
        "noise"
    )

    assert "PodHighCPU" in (
        gateway.last_request.prompt
    )

    assert gateway.last_request.context.require_json is (
        True
    )