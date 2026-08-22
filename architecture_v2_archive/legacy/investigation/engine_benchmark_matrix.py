from __future__ import annotations

from enum import Enum
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from services.agent_runtime.app.investigation.models import (
    InvestigationStatus,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.session_loop import (
    InvestigationSessionLoopOutcome,
    InvestigationSessionLoopStopReason,
)
from services.agent_runtime.app.investigation.session_models import (
    InvestigationSessionStatus,
)


class InvestigationEngineBenchmarkScenario(str, Enum):
    SUFFICIENT_EVIDENCE = "sufficient_evidence"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PROBE_FAILURE = "probe_failure"
    REASONER_FAILURE = "reasoner_failure"
    REASONER_TIMEOUT = "reasoner_timeout"
    BUDGET_EXHAUSTION = "budget_exhaustion"
    CONCURRENT_CLAIM = "concurrent_claim"
    RESTART_RECOVERY = "restart_recovery"


REQUIRED_BENCHMARK_SCENARIOS = frozenset(
    InvestigationEngineBenchmarkScenario
)


class InvestigationEngineBenchmarkArmObservation(BaseModel):
    """Bounded observation for one backend in one controlled scenario."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    backend: Literal["custom", "langgraph"]
    session_status: InvestigationSessionStatus
    investigation_status: InvestigationStatus
    investigation_stop_reason: InvestigationStopReason | None
    outcome: InvestigationSessionLoopOutcome
    loop_stop_reason: InvestigationSessionLoopStopReason
    durable_steps: int = Field(ge=0, le=32)
    reasoner_calls: int = Field(ge=0, le=32)
    probe_calls: int = Field(ge=0, le=32)
    external_calls_made: int = Field(ge=0, le=32)
    replay_external_calls_made: int = Field(ge=0, le=32)
    recovery_required: bool = False
    failed_probe_observed: bool = False
    concurrent_call_grants: int = Field(default=0, ge=0, le=2)
    concurrent_replay_blocked: bool = False
    restart_performed: bool = False
    semantic_digest: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    protocol_digest: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    sensitive_output_absent: bool = True
    elapsed_ms: float = Field(ge=0.0)


class InvestigationEngineBenchmarkScenarioResult(BaseModel):
    """Evaluated parity and safety result for one Matrix row."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    scenario: InvestigationEngineBenchmarkScenario
    custom: InvestigationEngineBenchmarkArmObservation
    langgraph: InvestigationEngineBenchmarkArmObservation
    semantic_equivalent: bool
    protocol_equivalent: bool
    call_budget_equivalent: bool
    replay_safe: bool
    expected_behavior_met: bool
    sensitive_output_absent: bool
    passed: bool


class InvestigationEngineBenchmarkMatrixReport(BaseModel):
    """Complete eight-scenario Custom-versus-LangGraph safety report."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: Literal["v1"] = "v1"
    controlled_replay: Literal[True] = True
    read_only: Literal[True] = True
    scenario_count: int = Field(ge=8, le=8)
    passed_count: int = Field(ge=0, le=8)
    scenarios: tuple[
        InvestigationEngineBenchmarkScenarioResult,
        ...,
    ] = Field(min_length=8, max_length=8)
    all_semantically_equivalent: bool
    all_protocol_equivalent: bool
    all_call_budgets_equivalent: bool
    all_replay_safe: bool
    sensitive_output_absent: bool
    passed: bool


class InvestigationEngineBenchmarkMatrixEvaluator:
    """
    Evaluate a complete controlled failure and recovery scenario matrix.

    The Evaluator cannot run Engines or external dependencies. It consumes
    bounded observations produced by an isolated test harness and fails closed
    when a required scenario, backend, parity property, or expected safety
    outcome is absent.
    """

    def evaluate(
        self,
        observations: Mapping[
            InvestigationEngineBenchmarkScenario,
            tuple[
                InvestigationEngineBenchmarkArmObservation,
                InvestigationEngineBenchmarkArmObservation,
            ],
        ],
    ) -> InvestigationEngineBenchmarkMatrixReport:
        if set(observations) != REQUIRED_BENCHMARK_SCENARIOS:
            raise ValueError(
                "Investigation Engine Benchmark Matrix is incomplete"
            )

        results = tuple(
            self._evaluate_scenario(
                scenario,
                observations[scenario],
            )
            for scenario in InvestigationEngineBenchmarkScenario
        )
        passed_count = sum(
            result.passed
            for result in results
        )
        all_semantically_equivalent = all(
            result.semantic_equivalent
            for result in results
        )
        all_protocol_equivalent = all(
            result.protocol_equivalent
            for result in results
        )
        all_call_budgets_equivalent = all(
            result.call_budget_equivalent
            for result in results
        )
        all_replay_safe = all(
            result.replay_safe
            for result in results
        )
        sensitive_output_absent = all(
            result.sensitive_output_absent
            for result in results
        )

        return InvestigationEngineBenchmarkMatrixReport(
            scenario_count=len(results),
            passed_count=passed_count,
            scenarios=results,
            all_semantically_equivalent=(
                all_semantically_equivalent
            ),
            all_protocol_equivalent=(
                all_protocol_equivalent
            ),
            all_call_budgets_equivalent=(
                all_call_budgets_equivalent
            ),
            all_replay_safe=all_replay_safe,
            sensitive_output_absent=(
                sensitive_output_absent
            ),
            passed=(
                passed_count == len(results)
                and all_semantically_equivalent
                and all_protocol_equivalent
                and all_call_budgets_equivalent
                and all_replay_safe
                and sensitive_output_absent
            ),
        )

    def _evaluate_scenario(
        self,
        scenario: InvestigationEngineBenchmarkScenario,
        arms: tuple[
            InvestigationEngineBenchmarkArmObservation,
            InvestigationEngineBenchmarkArmObservation,
        ],
    ) -> InvestigationEngineBenchmarkScenarioResult:
        if (
            not isinstance(arms, tuple)
            or len(arms) != 2
        ):
            raise TypeError(
                "Investigation Engine Benchmark Matrix arms are invalid"
            )
        by_backend = {
            arm.backend: arm
            for arm in arms
        }
        if set(by_backend) != {
            "custom",
            "langgraph",
        }:
            raise ValueError(
                "Investigation Engine Benchmark Matrix backends are invalid"
            )

        custom = by_backend["custom"]
        langgraph = by_backend["langgraph"]
        semantic_equivalent = (
            custom.semantic_digest
            == langgraph.semantic_digest
        )
        protocol_equivalent = (
            custom.protocol_digest
            == langgraph.protocol_digest
            and custom.session_status
            == langgraph.session_status
            and custom.investigation_status
            == langgraph.investigation_status
            and custom.investigation_stop_reason
            == langgraph.investigation_stop_reason
            and custom.outcome == langgraph.outcome
            and custom.loop_stop_reason
            == langgraph.loop_stop_reason
        )
        call_budget_equivalent = (
            custom.reasoner_calls
            == langgraph.reasoner_calls
            and custom.probe_calls
            == langgraph.probe_calls
            and custom.external_calls_made
            == langgraph.external_calls_made
            and custom.concurrent_call_grants
            == langgraph.concurrent_call_grants
        )
        replay_safe = (
            custom.replay_external_calls_made == 0
            and langgraph.replay_external_calls_made == 0
        )
        expected_behavior_met = (
            self._matches_expectation(
                scenario,
                custom,
            )
            and self._matches_expectation(
                scenario,
                langgraph,
            )
        )
        sensitive_output_absent = (
            custom.sensitive_output_absent
            and langgraph.sensitive_output_absent
        )

        return InvestigationEngineBenchmarkScenarioResult(
            scenario=scenario,
            custom=custom,
            langgraph=langgraph,
            semantic_equivalent=semantic_equivalent,
            protocol_equivalent=protocol_equivalent,
            call_budget_equivalent=call_budget_equivalent,
            replay_safe=replay_safe,
            expected_behavior_met=expected_behavior_met,
            sensitive_output_absent=(
                sensitive_output_absent
            ),
            passed=(
                semantic_equivalent
                and protocol_equivalent
                and call_budget_equivalent
                and replay_safe
                and expected_behavior_met
                and sensitive_output_absent
            ),
        )

    @staticmethod
    def _matches_expectation(
        scenario: InvestigationEngineBenchmarkScenario,
        arm: InvestigationEngineBenchmarkArmObservation,
    ) -> bool:
        if scenario == InvestigationEngineBenchmarkScenario.SUFFICIENT_EVIDENCE:
            return (
                arm.session_status == InvestigationSessionStatus.COMPLETED
                and arm.investigation_status
                == InvestigationStatus.CONCLUDED
                and arm.investigation_stop_reason
                == InvestigationStopReason.SUFFICIENT_EVIDENCE
                and arm.outcome
                == InvestigationSessionLoopOutcome.COMPLETED
                and arm.durable_steps == 3
                and arm.reasoner_calls == 2
                and arm.probe_calls == 1
                and arm.external_calls_made == 3
            )

        if scenario in {
            InvestigationEngineBenchmarkScenario.INSUFFICIENT_EVIDENCE,
            InvestigationEngineBenchmarkScenario.PROBE_FAILURE,
            InvestigationEngineBenchmarkScenario.RESTART_RECOVERY,
        }:
            base = (
                arm.session_status == InvestigationSessionStatus.COMPLETED
                and arm.investigation_status
                == InvestigationStatus.CONCLUDED
                and arm.investigation_stop_reason
                == InvestigationStopReason.INSUFFICIENT_EVIDENCE
                and arm.outcome
                == InvestigationSessionLoopOutcome.COMPLETED
                and arm.durable_steps == 3
                and arm.reasoner_calls == 2
                and arm.probe_calls == 1
                and arm.external_calls_made == 3
            )
            if scenario == InvestigationEngineBenchmarkScenario.PROBE_FAILURE:
                return base and arm.failed_probe_observed
            if scenario == InvestigationEngineBenchmarkScenario.RESTART_RECOVERY:
                return base and arm.restart_performed
            return base

        if scenario in {
            InvestigationEngineBenchmarkScenario.REASONER_FAILURE,
            InvestigationEngineBenchmarkScenario.BUDGET_EXHAUSTION,
        }:
            return (
                arm.session_status == InvestigationSessionStatus.FAILED
                and arm.investigation_status
                == InvestigationStatus.FAILED
                and arm.outcome
                == InvestigationSessionLoopOutcome.FAILED
                and arm.durable_steps == 1
                and arm.reasoner_calls == 1
                and arm.probe_calls == 0
                and arm.external_calls_made == 1
            )

        if scenario == InvestigationEngineBenchmarkScenario.REASONER_TIMEOUT:
            return (
                arm.session_status
                == InvestigationSessionStatus.INDETERMINATE
                and arm.investigation_status
                == InvestigationStatus.RUNNING
                and arm.outcome
                == InvestigationSessionLoopOutcome.BLOCKED
                and arm.loop_stop_reason
                == InvestigationSessionLoopStopReason.RECOVERY_REQUIRED
                and arm.durable_steps == 1
                and arm.reasoner_calls == 1
                and arm.probe_calls == 0
                and arm.external_calls_made == 1
                and arm.recovery_required
            )

        if scenario == InvestigationEngineBenchmarkScenario.CONCURRENT_CLAIM:
            return (
                arm.session_status == InvestigationSessionStatus.PAUSED
                and arm.investigation_status
                == InvestigationStatus.RUNNING
                and arm.durable_steps == 1
                and arm.reasoner_calls == 1
                and arm.probe_calls == 0
                and arm.external_calls_made == 1
                and arm.concurrent_call_grants == 1
                and arm.concurrent_replay_blocked
            )

        return False


__all__ = [
    "InvestigationEngineBenchmarkArmObservation",
    "InvestigationEngineBenchmarkMatrixEvaluator",
    "InvestigationEngineBenchmarkMatrixReport",
    "InvestigationEngineBenchmarkScenario",
    "InvestigationEngineBenchmarkScenarioResult",
    "REQUIRED_BENCHMARK_SCENARIOS",
]
