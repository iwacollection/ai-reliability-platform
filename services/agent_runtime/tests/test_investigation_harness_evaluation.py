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

from services.agent_runtime.app.investigation.harness_evaluation import (
    build_investigation_harness_evaluation,
)
from services.harness.runner.runner import (
    HarnessRunner,
)


NOW = datetime(
    2026,
    8,
    10,
    5,
    40,
    tzinfo=UTC,
)


def valid_comparison(
    *,
    status: str = "matched",
    rca_confidence: float = 0.8,
    investigation_confidence: float = 0.9,
):
    comparable = status in {
        "matched",
        "mismatched",
    }

    rca_available = (
        status != "rca_unavailable"
    )

    investigation_available = (
        status
        not in {
            "investigation_no_conclusion",
            "investigation_orchestration_failed",
        }
    )

    confidence_delta = (
        round(
            (
                investigation_confidence
                - rca_confidence
            ),
            6,
        )
        if comparable
        else None
    )

    return {
        "schema_version": "v1",
        "shadow_mode": True,
        "read_only": True,
        "decision_influence": False,
        "available": comparable,
        "comparison_status": status,
        "rca": {
            "available": rca_available,
            "root_cause": (
                "legacy RCA"
                if rca_available
                else None
            ),
            "confidence": (
                rca_confidence
                if rca_available
                else None
            ),
            "evidence_count": 2,
        },
        "investigation": {
            "available": (
                investigation_available
            ),
            "status": (
                "concluded"
                if investigation_available
                else "exhausted"
            ),
            "stop_reason": (
                "sufficient_evidence"
                if investigation_available
                else "max_iterations"
            ),
            "root_cause": (
                "shadow RCA"
                if investigation_available
                else None
            ),
            "confidence": (
                investigation_confidence
                if investigation_available
                else None
            ),
            "evidence_count": (
                3
                if investigation_available
                else 0
            ),
            "trusted_evidence_count": (
                3
                if investigation_available
                else 0
            ),
            "conclusion_evidence_count": (
                2
                if investigation_available
                else 0
            ),
        },
        "comparison": {
            "exact_match": (
                True
                if status == "matched"
                else (
                    False
                    if status == "mismatched"
                    else None
                )
            ),
            "normalized_text_match": (
                True
                if status == "matched"
                else (
                    False
                    if status == "mismatched"
                    else None
                )
            ),
            "confidence_delta": (
                confidence_delta
            ),
        },
    }


def test_matched_comparison_becomes_bounded_harness_evaluation():
    evaluation = (
        build_investigation_harness_evaluation(
            valid_comparison(
                status="matched",
                rca_confidence=0.8,
                investigation_confidence=0.9,
            )
        )
    )

    assert evaluation.schema_version == "v1"

    assert evaluation.shadow_mode is True
    assert evaluation.read_only is True

    assert (
        evaluation.decision_influence
        is False
    )

    assert (
        evaluation.comparison_present
        is True
    )

    assert (
        evaluation.comparison_valid
        is True
    )

    assert (
        evaluation.comparison_status
        == "matched"
    )

    assert evaluation.comparable is True
    assert evaluation.matched is True

    assert (
        evaluation.investigation_concluded
        is True
    )

    assert evaluation.confidence_delta == 0.1

    assert (
        evaluation.investigation_evidence_count
        == 3
    )

    assert (
        evaluation.trusted_evidence_count
        == 3
    )

    assert (
        evaluation.conclusion_evidence_count
        == 2
    )

    assert (
        evaluation.trusted_evidence_ratio
        == 1.0
    )


def test_missing_comparison_is_exposed_without_fake_metrics():
    evaluation = (
        build_investigation_harness_evaluation(
            None
        )
    )

    assert (
        evaluation.comparison_present
        is False
    )

    assert (
        evaluation.comparison_valid
        is False
    )

    assert evaluation.comparison_status is None

    assert evaluation.comparable is False
    assert evaluation.matched is None

    assert (
        evaluation.confidence_delta
        is None
    )

    assert (
        evaluation.investigation_evidence_count
        is None
    )


def test_malformed_comparison_is_not_trusted_or_leaked():
    secret = (
        "https://user:secret-token@"
        "provider.example.invalid"
    )

    value = valid_comparison(
        status="matched"
    )

    value[
        "decision_influence"
    ] = True

    value["rca"][
        "root_cause"
    ] = secret

    value["investigation"][
        "root_cause"
    ] = secret

    evaluation = (
        build_investigation_harness_evaluation(
            value
        )
    )

    assert (
        evaluation.comparison_present
        is True
    )

    assert (
        evaluation.comparison_valid
        is False
    )

    assert (
        evaluation.comparison_status
        is None
    )

    serialized = str(
        evaluation.model_dump(
            mode="json"
        )
    )

    assert secret not in serialized


def event():
    return StandardEvent(
        header=Header(
            source=(
                EventSource.ALERTMANAGER
            ),
            occurred_at=NOW,
        ),
        signal=Signal(
            type=SignalType.ALERT,
            name="PodOOMKilled",
            severity=Severity.CRITICAL,
            message=(
                "payment-api restarted"
            ),
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


class FakeLoader:
    def load_event(
        self,
        case_name,
    ):
        return event()


class FakeTraceStore:
    def list(
        self,
    ):
        return []


class FakeResult:
    def model_dump(
        self,
    ):
        return {
            "agent": "rca",
            "success": True,
        }


class FakeRuntime:
    def __init__(
        self,
        comparison_snapshot,
    ):
        self.comparison_snapshot = (
            deepcopy(
                comparison_snapshot
            )
        )

        self.memory = None
        self.tools = None
        self.skills = None
        self.mcp = None
        self.sandbox = None
        self.sandbox_policy = None
        self.approval = None

        self.tracer = (
            FakeTraceStore()
        )

        self.execute_calls = 0

    async def execute(
        self,
        context,
    ):
        self.execute_calls += 1

        if (
            self.comparison_snapshot
            is not None
        ):
            context.metadata[
                "investigation_rca_comparison"
            ] = deepcopy(
                self.comparison_snapshot
            )

        return [
            FakeResult()
        ]


@pytest.mark.asyncio
async def test_harness_runner_exposes_evaluation_without_changing_primary_result():
    runtime = FakeRuntime(
        valid_comparison(
            status="matched"
        )
    )

    runner = HarnessRunner(
        runtime=runtime
    )

    runner.loader = FakeLoader()

    result = await runner.run(
        "pod_oom_case"
    )

    assert runtime.execute_calls == 1

    assert result[
        "case"
    ] == "pod_oom_case"

    assert result[
        "success"
    ] is True

    assert result[
        "results"
    ] == [
        {
            "agent": "rca",
            "success": True,
        }
    ]

    assert result[
        "executions"
    ] == []

    assert result[
        "evaluations"
    ] == []

    assert result[
        "traces"
    ] == []

    investigation = result[
        "investigation_evaluation"
    ]

    assert investigation[
        "comparison_present"
    ] is True

    assert investigation[
        "comparison_valid"
    ] is True

    assert investigation[
        "comparison_status"
    ] == "matched"

    assert investigation[
        "decision_influence"
    ] is False

    assert (
        "root_cause"
        not in investigation
    )

    assert (
        "evidence"
        not in investigation
    )


@pytest.mark.asyncio
async def test_harness_runner_handles_disabled_or_missing_shadow():
    runtime = FakeRuntime(
        None
    )

    runner = HarnessRunner(
        runtime=runtime
    )

    runner.loader = FakeLoader()

    result = await runner.run(
        "disabled-shadow-case"
    )

    investigation = result[
        "investigation_evaluation"
    ]

    assert investigation[
        "comparison_present"
    ] is False

    assert investigation[
        "comparison_valid"
    ] is False

    assert investigation[
        "comparison_status"
    ] is None

    assert investigation[
        "decision_influence"
    ] is False
