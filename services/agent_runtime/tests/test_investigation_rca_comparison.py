from copy import deepcopy
from datetime import UTC, datetime

import pytest

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

from services.agent_runtime.app.investigation.comparison import (
    build_rca_investigation_comparison,
)
from services.agent_runtime.app.model.context import (
    AgentContext,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)
from services.agent_runtime.app.tools.manager import (
    ToolManager,
)
from services.agent_runtime.app.tools.registry import (
    ToolRegistry,
)


NOW = datetime(
    2026,
    8,
    10,
    5,
    10,
    tzinfo=UTC,
)


def investigation_snapshot(
    *,
    root_cause=(
        "Container memory pressure caused OOM"
    ),
    confidence=0.95,
):
    return {
        "shadow_mode": True,
        "read_only": True,
        "status": "concluded",
        "stop_reason": (
            "sufficient_evidence"
        ),
        "evidence": [
            {
                "evidence_id": "evidence-1",
                "trusted": True,
            },
            {
                "evidence_id": "evidence-2",
                "trusted": True,
            },
        ],
        "conclusion": {
            "root_cause": root_cause,
            "confidence": confidence,
            "evidence_ids": [
                "evidence-1",
                "evidence-2",
            ],
        },
    }


def test_normalized_match_is_deterministic_and_not_semantic():
    comparison = (
        build_rca_investigation_comparison(
            rca={
                "root_cause": (
                    "Container memory-pressure "
                    "caused OOM."
                ),
                "confidence": 0.80,
                "evidence": [
                    "pod evidence",
                ],
            },
            investigation_snapshot=(
                investigation_snapshot()
            ),
        )
    )

    assert comparison[
        "available"
    ] is True

    assert comparison[
        "comparison_status"
    ] == "matched"

    assert comparison[
        "decision_influence"
    ] is False

    assert comparison[
        "comparison"
    ][
        "exact_match"
    ] is False

    assert comparison[
        "comparison"
    ][
        "normalized_text_match"
    ] is True

    assert comparison[
        "comparison"
    ][
        "confidence_delta"
    ] == 0.15

    assert comparison[
        "rca"
    ][
        "evidence_count"
    ] == 1

    assert comparison[
        "investigation"
    ][
        "evidence_count"
    ] == 2

    assert comparison[
        "investigation"
    ][
        "trusted_evidence_count"
    ] == 2


def test_different_root_causes_are_reported_without_decision_influence():
    comparison = (
        build_rca_investigation_comparison(
            rca={
                "root_cause": (
                    "Recent deployment regression"
                ),
                "confidence": 0.70,
                "evidence": [],
            },
            investigation_snapshot=(
                investigation_snapshot()
            ),
        )
    )

    assert comparison[
        "comparison_status"
    ] == "mismatched"

    assert comparison[
        "comparison"
    ][
        "exact_match"
    ] is False

    assert comparison[
        "comparison"
    ][
        "normalized_text_match"
    ] is False

    assert comparison[
        "decision_influence"
    ] is False


def test_investigation_without_sufficient_conclusion_is_not_comparable():
    comparison = (
        build_rca_investigation_comparison(
            rca={
                "root_cause": (
                    "Memory pressure"
                ),
                "confidence": 0.75,
                "evidence": [],
            },
            investigation_snapshot={
                "shadow_mode": True,
                "read_only": True,
                "status": "exhausted",
                "stop_reason": (
                    "max_iterations"
                ),
                "evidence": [
                    {
                        "trusted": True,
                    }
                ],
                "conclusion": None,
            },
        )
    )

    assert comparison[
        "available"
    ] is False

    assert comparison[
        "comparison_status"
    ] == "investigation_no_conclusion"

    assert comparison[
        "comparison"
    ][
        "exact_match"
    ] is None


def test_orchestration_failure_is_not_comparable():
    comparison = (
        build_rca_investigation_comparison(
            rca={
                "root_cause": (
                    "Memory pressure"
                ),
                "confidence": 0.75,
            },
            investigation_snapshot=None,
            orchestration_snapshot={
                "status": "failed",
                "failure_code": (
                    "RuntimeError"
                ),
            },
        )
    )

    assert comparison[
        "available"
    ] is False

    assert comparison[
        "comparison_status"
    ] == (
        "investigation_orchestration_failed"
    )


def test_raw_rca_evidence_is_never_copied_to_comparison():
    secret = (
        "https://user:secret-token@"
        "private.example.invalid"
    )

    comparison = (
        build_rca_investigation_comparison(
            rca={
                "root_cause": (
                    "Memory pressure"
                ),
                "confidence": 0.80,
                "evidence": [
                    {
                        "secret": secret,
                    }
                ],
            },
            investigation_snapshot=(
                investigation_snapshot(
                    root_cause=(
                        "Memory pressure"
                    )
                )
            ),
        )
    )

    assert secret not in str(
        comparison
    )

    assert comparison[
        "rca"
    ][
        "evidence_count"
    ] == 1


def event():
    return StandardEvent(
        header=Header(
            source=EventSource.ALERTMANAGER,
            occurred_at=NOW,
        ),
        signal=Signal(
            type=SignalType.ALERT,
            name="PodOOMKilled",
            severity=Severity.CRITICAL,
            message="payment-api restarted",
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


def tools():
    return ToolManager(
        ToolRegistry()
    )


class RCAPipeline:
    def __init__(
        self,
    ):
        self.rca = {
            "root_cause": (
                "Container memory-pressure "
                "caused OOM."
            ),
            "confidence": 0.80,
            "evidence": [
                "legacy evidence",
            ],
        }

    async def execute(
        self,
        context,
    ):
        context.variables[
            "rca"
        ] = deepcopy(
            self.rca
        )

        context.variables[
            "healing_input_marker"
        ] = "authoritative"

        return [
            "pipeline-result"
        ]


class ComparisonShadowCoordinator:
    async def investigate(
        self,
        context,
    ):
        context.metadata[
            "investigation_shadow"
        ] = investigation_snapshot()

        return object()


def lightweight_runtime(
    *,
    coordinator,
):
    runtime = object.__new__(
        AgentRuntime
    )

    runtime.pipeline = RCAPipeline()

    runtime.investigation_coordinator = (
        coordinator
    )

    runtime.tools = tools()

    return runtime


@pytest.mark.asyncio
async def test_runtime_publishes_comparison_without_mutating_authoritative_rca():
    runtime = lightweight_runtime(
        coordinator=(
            ComparisonShadowCoordinator()
        )
    )

    context = AgentContext(
        request_id="comparison-request",
        event=event(),
        tools=runtime.tools,
    )

    results = await runtime.execute(
        context
    )

    assert results == [
        "pipeline-result"
    ]

    authoritative_rca = (
        context.variables[
            "rca"
        ]
    )

    assert authoritative_rca == {
        "root_cause": (
            "Container memory-pressure "
            "caused OOM."
        ),
        "confidence": 0.80,
        "evidence": [
            "legacy evidence",
        ],
    }

    assert context.variables[
        "healing_input_marker"
    ] == "authoritative"

    comparison = context.metadata[
        "investigation_rca_comparison"
    ]

    assert comparison[
        "comparison_status"
    ] == "matched"

    assert comparison[
        "decision_influence"
    ] is False

    assert (
        "investigation_rca_comparison"
        not in context.variables
    )

    assert (
        "investigation_rca_comparison"
        not in context.results
    )


@pytest.mark.asyncio
async def test_disabled_runtime_clears_stale_shadow_comparison_metadata():
    runtime = lightweight_runtime(
        coordinator=None
    )

    context = AgentContext(
        request_id="stale-request",
        event=event(),
        tools=runtime.tools,
        metadata={
            "investigation_shadow": {
                "stale": True,
            },
            "investigation_shadow_orchestration": {
                "stale": True,
            },
            "investigation_rca_comparison": {
                "stale": True,
            },
        },
    )

    await runtime.execute(
        context
    )

    assert (
        "investigation_shadow"
        not in context.metadata
    )

    assert (
        "investigation_shadow_orchestration"
        not in context.metadata
    )

    assert (
        "investigation_rca_comparison"
        not in context.metadata
    )
