from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from services.agent_runtime.app.investigation.engine_benchmark_matrix import (
    InvestigationEngineBenchmarkArmObservation,
    InvestigationEngineBenchmarkMatrixEvaluator,
    InvestigationEngineBenchmarkScenario,
)
from services.agent_runtime.app.investigation.engine_shadow_gate import (
    INVESTIGATION_LANGGRAPH_SHADOW_ACKNOWLEDGEMENT,
    InvestigationEngineShadowConfigurationError,
    InvestigationEngineShadowGate,
    InvestigationEngineShadowGateCode,
    InvestigationEngineShadowSettings,
    build_investigation_engine_shadow_evidence,
    investigation_engine_benchmark_matrix_digest,
)
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
from services.agent_runtime.app.investigation.session_runtime_settings import (
    InvestigationEngineBackend,
)

NOW = datetime(
    2026,
    8,
    17,
    0,
    0,
    tzinfo=UTC,
)
RELEASE_DIGEST = "a" * 64


def arm(
    scenario: InvestigationEngineBenchmarkScenario,
    *,
    backend: str,
    elapsed_ms: float,
) -> InvestigationEngineBenchmarkArmObservation:
    session_status = InvestigationSessionStatus.COMPLETED
    investigation_status = InvestigationStatus.CONCLUDED
    stop_reason = InvestigationStopReason.INSUFFICIENT_EVIDENCE
    outcome = InvestigationSessionLoopOutcome.COMPLETED
    loop_stop = InvestigationSessionLoopStopReason.SESSION_COMPLETED
    durable_steps = 3
    reasoner_calls = 2
    probe_calls = 1
    external_calls = 3
    recovery_required = False
    failed_probe_observed = False
    concurrent_grants = 0
    concurrent_blocked = False
    restart_performed = False

    if scenario == InvestigationEngineBenchmarkScenario.SUFFICIENT_EVIDENCE:
        stop_reason = InvestigationStopReason.SUFFICIENT_EVIDENCE
    elif scenario == InvestigationEngineBenchmarkScenario.PROBE_FAILURE:
        failed_probe_observed = True
    elif scenario in {
        InvestigationEngineBenchmarkScenario.REASONER_FAILURE,
        InvestigationEngineBenchmarkScenario.BUDGET_EXHAUSTION,
    }:
        session_status = InvestigationSessionStatus.FAILED
        investigation_status = InvestigationStatus.FAILED
        stop_reason = InvestigationStopReason.REASONER_ERROR
        outcome = InvestigationSessionLoopOutcome.FAILED
        loop_stop = InvestigationSessionLoopStopReason.SESSION_FAILED
        durable_steps = 1
        reasoner_calls = 1
        probe_calls = 0
        external_calls = 1
    elif scenario == InvestigationEngineBenchmarkScenario.REASONER_TIMEOUT:
        session_status = InvestigationSessionStatus.INDETERMINATE
        investigation_status = InvestigationStatus.RUNNING
        stop_reason = None
        outcome = InvestigationSessionLoopOutcome.BLOCKED
        loop_stop = InvestigationSessionLoopStopReason.RECOVERY_REQUIRED
        durable_steps = 1
        reasoner_calls = 1
        probe_calls = 0
        external_calls = 1
        recovery_required = True
    elif scenario == InvestigationEngineBenchmarkScenario.CONCURRENT_CLAIM:
        session_status = InvestigationSessionStatus.PAUSED
        investigation_status = InvestigationStatus.RUNNING
        stop_reason = None
        outcome = InvestigationSessionLoopOutcome.PAUSED
        loop_stop = InvestigationSessionLoopStopReason.STEP_LIMIT
        durable_steps = 1
        reasoner_calls = 1
        probe_calls = 0
        external_calls = 1
        concurrent_grants = 1
        concurrent_blocked = True
    elif scenario == InvestigationEngineBenchmarkScenario.RESTART_RECOVERY:
        restart_performed = True

    semantic_digest = hashlib.sha256(
        f"semantic:{scenario.value}".encode()
    ).hexdigest()
    protocol_digest = hashlib.sha256(
        f"protocol:{scenario.value}".encode()
    ).hexdigest()
    return InvestigationEngineBenchmarkArmObservation(
        backend=backend,
        session_status=session_status,
        investigation_status=investigation_status,
        investigation_stop_reason=stop_reason,
        outcome=outcome,
        loop_stop_reason=loop_stop,
        durable_steps=durable_steps,
        reasoner_calls=reasoner_calls,
        probe_calls=probe_calls,
        external_calls_made=external_calls,
        replay_external_calls_made=0,
        recovery_required=recovery_required,
        failed_probe_observed=failed_probe_observed,
        concurrent_call_grants=concurrent_grants,
        concurrent_replay_blocked=concurrent_blocked,
        restart_performed=restart_performed,
        semantic_digest=semantic_digest,
        protocol_digest=protocol_digest,
        elapsed_ms=elapsed_ms,
    )


def passing_report():
    observations = {
        scenario: (
            arm(
                scenario,
                backend="custom",
                elapsed_ms=10.0,
            ),
            arm(
                scenario,
                backend="langgraph",
                elapsed_ms=12.0,
            ),
        )
        for scenario in InvestigationEngineBenchmarkScenario
    }
    report = InvestigationEngineBenchmarkMatrixEvaluator().evaluate(
        observations
    )
    assert report.passed is True
    return report


def fresh_evidence(
    *,
    expires_at: datetime | None = None,
):
    return build_investigation_engine_shadow_evidence(
        report=passing_report(),
        release_digest=RELEASE_DIGEST,
        generated_at=NOW,
        expires_at=(
            expires_at
            or NOW + timedelta(minutes=30)
        ),
    )


def enabled_settings(
    tmp_path,
    *,
    evidence=None,
    kill_switch_engaged: bool = False,
):
    active_evidence = evidence or fresh_evidence()
    return InvestigationEngineShadowSettings(
        enabled=True,
        acknowledgement=(
            INVESTIGATION_LANGGRAPH_SHADOW_ACKNOWLEDGEMENT
        ),
        kill_switch_engaged=kill_switch_engaged,
        shadow_db_path=str(
            tmp_path / "langgraph-shadow.db"
        ),
        expected_matrix_digest=(
            active_evidence.matrix_digest
        ),
        expected_release_digest=(
            active_evidence.release_digest
        ),
    )


def test_default_environment_is_disabled_and_kill_switch_engaged():
    settings = InvestigationEngineShadowSettings.from_environment(
        {}
    )

    assert settings.enabled is False
    assert settings.kill_switch_engaged is True
    assert settings.sample_rate == 0.01
    assert settings.max_concurrent_sessions == 1
    assert settings.max_external_steps_per_invocation == 1
    assert settings.expected_matrix_digest is None
    assert settings.expected_release_digest is None


def test_enabled_environment_requires_exact_acknowledgement_and_digests():
    base = {
        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_ENABLED": "true",
    }
    with pytest.raises(
        InvestigationEngineShadowConfigurationError,
        match="configuration is invalid",
    ):
        InvestigationEngineShadowSettings.from_environment(
            base
        )

    values = {
        **base,
        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_ACKNOWLEDGEMENT": (
            INVESTIGATION_LANGGRAPH_SHADOW_ACKNOWLEDGEMENT
        ),
        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_KILL_SWITCH_ENGAGED": "false",
        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_MATRIX_DIGEST": "b" * 64,
        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_RELEASE_DIGEST": "c" * 64,
        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_SAMPLE_RATE": "0.02",
        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_MAX_CONCURRENT": "2",
    }
    settings = InvestigationEngineShadowSettings.from_environment(
        values
    )

    assert settings.enabled is True
    assert settings.kill_switch_engaged is False
    assert settings.sample_rate == 0.02
    assert settings.max_concurrent_sessions == 2


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (
            "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_SAMPLE_RATE",
            "0.5",
        ),
        (
            "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_MAX_CONCURRENT",
            "5",
        ),
        (
            "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_KILL_SWITCH_ENGAGED",
            "maybe",
        ),
        (
            "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_DB_PATH",
            "shadow.sqlite",
        ),
    ],
)
def test_invalid_environment_fails_closed(name, value):
    with pytest.raises(
        InvestigationEngineShadowConfigurationError,
        match="configuration is invalid",
    ):
        InvestigationEngineShadowSettings.from_environment(
            {name: value}
        )


def test_matrix_digest_ignores_elapsed_time_but_detects_semantics():
    report = passing_report()
    baseline = investigation_engine_benchmark_matrix_digest(
        report
    )
    first = report.scenarios[0]
    timing_only = report.model_copy(
        update={
            "scenarios": (
                first.model_copy(
                    update={
                        "custom": first.custom.model_copy(
                            update={"elapsed_ms": 9999.0}
                        )
                    }
                ),
                *report.scenarios[1:],
            )
        }
    )
    semantic_change = report.model_copy(
        update={
            "scenarios": (
                first.model_copy(
                    update={
                        "custom": first.custom.model_copy(
                            update={
                                "semantic_digest": "f" * 64
                            }
                        )
                    }
                ),
                *report.scenarios[1:],
            )
        }
    )

    assert (
        investigation_engine_benchmark_matrix_digest(
            timing_only
        )
        == baseline
    )
    assert (
        investigation_engine_benchmark_matrix_digest(
            semantic_change
        )
        != baseline
    )


def test_shadow_evidence_requires_a_completely_passing_matrix():
    report = passing_report()
    evidence = fresh_evidence()

    assert evidence.report_passed is True
    assert evidence.scenario_count == 8
    assert evidence.passed_count == 8
    assert evidence.release_digest == RELEASE_DIGEST

    with pytest.raises(
        ValueError,
        match="completely passing Matrix",
    ):
        build_investigation_engine_shadow_evidence(
            report=report.model_copy(
                update={"passed": False}
            ),
            release_digest=RELEASE_DIGEST,
            generated_at=NOW,
            expires_at=NOW + timedelta(minutes=30),
        )

    with pytest.raises(ValidationError):
        build_investigation_engine_shadow_evidence(
            report=report,
            release_digest=RELEASE_DIGEST,
            generated_at=NOW,
            expires_at=NOW,
        )


def test_gate_allows_only_fresh_release_bound_isolated_custom_primary(
    tmp_path,
):
    evidence = fresh_evidence()
    settings = enabled_settings(
        tmp_path,
        evidence=evidence,
    )
    primary_db = tmp_path / "custom-primary.db"

    decision = InvestigationEngineShadowGate().evaluate(
        settings=settings,
        evidence=evidence,
        primary_backend=InvestigationEngineBackend.CUSTOM,
        primary_db_path=str(primary_db),
        now=NOW + timedelta(minutes=5),
    )

    assert decision.allowed is True
    assert decision.code == InvestigationEngineShadowGateCode.ALLOWED
    assert decision.primary_engine == "custom"
    assert decision.shadow_engine == "langgraph"
    assert decision.read_only is True
    assert decision.writes_allowed is False
    assert not primary_db.exists()
    assert not Path(settings.shadow_db_path).exists()


def test_gate_operational_failures_deny_without_side_effects(tmp_path):
    gate = InvestigationEngineShadowGate()
    evidence = fresh_evidence()
    settings = enabled_settings(
        tmp_path,
        evidence=evidence,
    )
    primary_db = tmp_path / "custom-primary.db"

    cases = (
        (
            settings.model_copy(
                update={"kill_switch_engaged": True}
            ),
            evidence,
            InvestigationEngineBackend.CUSTOM,
            str(primary_db),
            NOW,
            InvestigationEngineShadowGateCode.KILL_SWITCH_ENGAGED,
        ),
        (
            settings,
            None,
            InvestigationEngineBackend.CUSTOM,
            str(primary_db),
            NOW,
            InvestigationEngineShadowGateCode.EVIDENCE_MISSING,
        ),
        (
            settings,
            evidence,
            InvestigationEngineBackend.LANGGRAPH,
            str(primary_db),
            NOW,
            InvestigationEngineShadowGateCode.PRIMARY_ENGINE_NOT_CUSTOM,
        ),
        (
            settings,
            evidence,
            InvestigationEngineBackend.CUSTOM,
            settings.shadow_db_path,
            NOW,
            InvestigationEngineShadowGateCode.STORE_NOT_ISOLATED,
        ),
        (
            settings,
            evidence,
            InvestigationEngineBackend.CUSTOM,
            str(primary_db),
            NOW - timedelta(seconds=1),
            InvestigationEngineShadowGateCode.CLOCK_ROLLBACK,
        ),
        (
            settings,
            evidence,
            InvestigationEngineBackend.CUSTOM,
            str(primary_db),
            evidence.expires_at,
            InvestigationEngineShadowGateCode.EVIDENCE_EXPIRED,
        ),
        (
            settings.model_copy(
                update={"expected_matrix_digest": "d" * 64}
            ),
            evidence,
            InvestigationEngineBackend.CUSTOM,
            str(primary_db),
            NOW,
            InvestigationEngineShadowGateCode.MATRIX_DIGEST_MISMATCH,
        ),
        (
            settings.model_copy(
                update={"expected_release_digest": "e" * 64}
            ),
            evidence,
            InvestigationEngineBackend.CUSTOM,
            str(primary_db),
            NOW,
            InvestigationEngineShadowGateCode.RELEASE_DIGEST_MISMATCH,
        ),
    )

    for (
        active_settings,
        active_evidence,
        backend,
        db_path,
        current,
        expected_code,
    ) in cases:
        decision = gate.evaluate(
            settings=active_settings,
            evidence=active_evidence,
            primary_backend=backend,
            primary_db_path=db_path,
            now=current,
        )
        assert decision.allowed is False
        assert decision.code == expected_code
        assert decision.sample_rate == 0.0
        assert decision.max_concurrent_sessions == 0

    assert not primary_db.exists()
    assert not Path(settings.shadow_db_path).exists()


def test_gate_rejects_evidence_window_larger_than_policy(tmp_path):
    evidence = fresh_evidence(
        expires_at=NOW + timedelta(hours=2)
    )
    settings = enabled_settings(
        tmp_path,
        evidence=evidence,
    )

    decision = InvestigationEngineShadowGate().evaluate(
        settings=settings,
        evidence=evidence,
        primary_backend=InvestigationEngineBackend.CUSTOM,
        primary_db_path=str(
            tmp_path / "custom-primary.db"
        ),
        now=NOW,
    )

    assert decision.allowed is False
    assert decision.code == (
        InvestigationEngineShadowGateCode.EVIDENCE_TOO_OLD
    )


def test_deterministic_sampling_is_restart_stable_and_bounded(tmp_path):
    evidence = fresh_evidence()
    settings = enabled_settings(
        tmp_path,
        evidence=evidence,
    )
    gate = InvestigationEngineShadowGate()
    decision = gate.evaluate(
        settings=settings,
        evidence=evidence,
        primary_backend=InvestigationEngineBackend.CUSTOM,
        primary_db_path=str(
            tmp_path / "custom-primary.db"
        ),
        now=NOW,
    )
    identities = [
        UUID(int=index + 1)
        for index in range(5000)
    ]
    first = [
        gate.selected_for_shadow(
            decision=decision,
            incident_id=incident_id,
            run_key="oomkilled-shadow-v1",
        )
        for incident_id in identities
    ]
    second = [
        gate.selected_for_shadow(
            decision=decision,
            incident_id=incident_id,
            run_key="oomkilled-shadow-v1",
        )
        for incident_id in identities
    ]

    assert first == second
    assert 25 <= sum(first) <= 75
    denied = decision.model_copy(
        update={
            "allowed": False,
            "code": InvestigationEngineShadowGateCode.DISABLED,
        }
    )
    assert gate.selected_for_shadow(
        decision=denied,
        incident_id="not-a-uuid",
        run_key="",
    ) is False


def test_decision_is_frozen_and_contains_no_request_identity(tmp_path):
    evidence = fresh_evidence()
    settings = enabled_settings(
        tmp_path,
        evidence=evidence,
    )
    decision = InvestigationEngineShadowGate().evaluate(
        settings=settings,
        evidence=evidence,
        primary_backend=InvestigationEngineBackend.CUSTOM,
        primary_db_path=str(
            tmp_path / "custom-primary.db"
        ),
        now=NOW,
    )

    serialized = decision.model_dump_json()
    assert "credential" not in serialized
    assert "incident_id" not in serialized
    assert "run_key" not in serialized
    assert "db_path" not in serialized
    with pytest.raises(ValidationError):
        decision.allowed = False
