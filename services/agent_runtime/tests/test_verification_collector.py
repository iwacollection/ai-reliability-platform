from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from services.agent_runtime.app.verification.collector import (
    VerificationEvaluation,
    VerificationEvidenceCollector,
    VerificationProbe,
)
from services.agent_runtime.app.verification.models import (
    VerificationSource,
)


NOW = datetime(
    2026,
    8,
    1,
    8,
    0,
    tzinfo=UTC,
)


class FakeToolManager:
    def __init__(
        self,
        responses: list[Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.responses = list(
            responses or []
        )
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def call(
        self,
        name: str,
        context=None,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(
            {
                "name": name,
                "context": context,
                "kwargs": kwargs,
            }
        )

        if self.error is not None:
            raise self.error

        return self.responses[
            len(self.calls) - 1
        ]


def pod_ready_evaluator(
    evidence,
) -> VerificationEvaluation:
    ready = evidence[
        "data"
    ]["ready"]

    return VerificationEvaluation(
        passed=ready is True,
        observed_value=ready,
        expected_value=True,
        message=(
            "Pod is ready"
            if ready
            else "Pod is not ready"
        ),
        metadata={
            "evaluation": "pod_ready"
        },
    )


def build_probe(
    *,
    name: str = "pod_ready",
    source: VerificationSource = (
        VerificationSource.WORKLOAD
    ),
    tool: str = "kubernetes",
    provider: str = "kubernetes",
    required: bool = True,
    evaluator=pod_ready_evaluator,
) -> VerificationProbe:
    return VerificationProbe(
        name=name,
        source=source,
        tool=tool,
        provider=provider,
        arguments={
            "action": "describe",
            "resource": "pod",
            "target": "payment-api",
        },
        evaluator=evaluator,
        required=required,
    )


def trusted_evidence(
    **updates: Any,
) -> dict[str, Any]:
    evidence = {
        "success": True,
        "source": "kubernetes",
        "mode": "read_only",
        "production_signal": True,
        "observed_at": NOW.isoformat(),
        "data": {
            "ready": True,
        },
    }
    evidence.update(
        updates
    )
    return evidence


def create_collector(
    tools: FakeToolManager,
) -> VerificationEvidenceCollector:
    return VerificationEvidenceCollector(
        tools=tools,
        clock=lambda: NOW,
    )


def test_collector_rejects_invalid_time_limits():
    tools = FakeToolManager()

    with pytest.raises(
        ValueError,
        match="max_evidence_age",
    ):
        VerificationEvidenceCollector(
            tools=tools,
            max_evidence_age=timedelta(0),
        )

    with pytest.raises(
        ValueError,
        match="max_future_skew",
    ):
        VerificationEvidenceCollector(
            tools=tools,
            max_future_skew=timedelta(
                seconds=-1
            ),
        )


@pytest.mark.asyncio
async def test_trusted_fresh_evidence_can_pass():
    tools = FakeToolManager(
        responses=[
            trusted_evidence()
        ]
    )
    collector = create_collector(
        tools
    )
    context = object()

    check = await collector.collect_one(
        build_probe(),
        context=context,
    )

    assert check.passed is True
    assert check.required is True
    assert check.observed_value is True
    assert check.expected_value is True
    assert check.checked_at == NOW
    assert check.metadata["trusted"] is True
    assert check.metadata["provider"] == (
        "kubernetes"
    )
    assert check.metadata["evaluation"] == (
        "pod_ready"
    )
    assert tools.calls[0]["context"] is context
    assert tools.calls[0]["kwargs"]["target"] == (
        "payment-api"
    )


@pytest.mark.asyncio
async def test_current_prometheus_mock_is_rejected():
    evaluator_called = False

    def evaluator(
        evidence,
    ) -> VerificationEvaluation:
        nonlocal evaluator_called
        evaluator_called = True
        return VerificationEvaluation(
            passed=True
        )

    tools = FakeToolManager(
        responses=[
            {
                "query": "pod_cpu_usage",
                "metrics": {
                    "cpu_usage": 92,
                },
                "source": "mock_prometheus",
            }
        ]
    )
    collector = create_collector(
        tools
    )
    probe = build_probe(
        source=VerificationSource.METRIC,
        tool="prometheus",
        provider="prometheus",
        evaluator=evaluator,
    )

    check = await collector.collect_one(
        probe
    )

    assert check.passed is None
    assert check.metadata["trusted"] is False
    assert evaluator_called is False
    assert (
        "mock, test, or simulated evidence "
        "is not allowed"
        in check.metadata["rejection_reasons"]
    )


@pytest.mark.asyncio
async def test_current_kubernetes_dry_run_is_rejected():
    tools = FakeToolManager(
        responses=[
            {
                "success": True,
                "mode": "dry_run",
                "action": "describe",
                "resource": "pod",
                "target": "payment-api",
                "message": (
                    "Kubernetes action simulated"
                ),
            }
        ]
    )
    collector = create_collector(
        tools
    )

    check = await collector.collect_one(
        build_probe()
    )

    assert check.passed is None
    assert check.metadata["trusted"] is False
    assert "mode is not trusted" in (
        check.metadata["rejection_reasons"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("observed_at", "expected_reason"),
    [
        (
            NOW - timedelta(minutes=6),
            "evidence is stale",
        ),
        (
            NOW + timedelta(seconds=31),
            "observed_at is too far in the future",
        ),
        (
            "not-a-datetime",
            "observed_at is missing or invalid",
        ),
    ],
)
async def test_invalid_evidence_time_is_rejected(
    observed_at,
    expected_reason,
):
    tools = FakeToolManager(
        responses=[
            trusted_evidence(
                observed_at=observed_at
            )
        ]
    )
    collector = create_collector(
        tools
    )

    check = await collector.collect_one(
        build_probe()
    )

    assert check.passed is None
    assert expected_reason in (
        check.metadata["rejection_reasons"]
    )


@pytest.mark.asyncio
async def test_provider_mismatch_is_rejected():
    tools = FakeToolManager(
        responses=[
            trusted_evidence(
                source="prometheus"
            )
        ]
    )
    collector = create_collector(
        tools
    )

    check = await collector.collect_one(
        build_probe()
    )

    assert check.passed is None
    assert (
        "source does not match expected provider"
        in check.metadata["rejection_reasons"]
    )


@pytest.mark.asyncio
async def test_tool_failure_is_inconclusive():
    tools = FakeToolManager(
        error=TimeoutError(
            "query timed out"
        )
    )
    collector = create_collector(
        tools
    )

    check = await collector.collect_one(
        build_probe()
    )

    assert check.passed is None
    assert check.metadata["trusted"] is False
    assert check.metadata["error_type"] == (
        "TimeoutError"
    )
    assert "collection failed" in (
        check.message.lower()
    )


@pytest.mark.asyncio
async def test_evaluator_failure_is_inconclusive():
    def broken_evaluator(
        evidence,
    ):
        raise KeyError(
            "missing metric"
        )

    tools = FakeToolManager(
        responses=[
            trusted_evidence()
        ]
    )
    collector = create_collector(
        tools
    )

    check = await collector.collect_one(
        build_probe(
            evaluator=broken_evaluator
        )
    )

    assert check.passed is None
    assert check.metadata["trusted"] is False
    assert check.metadata["error_type"] == (
        "KeyError"
    )
    assert "evaluation failed" in (
        check.message.lower()
    )


@pytest.mark.asyncio
async def test_invalid_evaluator_result_is_inconclusive():
    tools = FakeToolManager(
        responses=[
            trusted_evidence()
        ]
    )
    collector = create_collector(
        tools
    )

    check = await collector.collect_one(
        build_probe(
            evaluator=lambda evidence: True
        )
    )

    assert check.passed is None
    assert check.metadata["trusted"] is False
    assert "invalid result" in (
        check.message.lower()
    )


@pytest.mark.asyncio
async def test_collect_preserves_probe_order():
    tools = FakeToolManager(
        responses=[
            trusted_evidence(),
            trusted_evidence(),
        ]
    )
    collector = create_collector(
        tools
    )
    probes = [
        build_probe(
            name="required_check"
        ),
        build_probe(
            name="optional_check",
            required=False,
        ),
    ]

    checks = await collector.collect(
        probes
    )

    assert [
        check.name
        for check in checks
    ] == [
        "required_check",
        "optional_check",
    ]
    assert checks[0].required is True
    assert checks[1].required is False
    assert len(
        tools.calls
    ) == 2