import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import services.agent_runtime.app.evaluation.real_incident.llm_run as run_module

from services.agent_runtime.app.evaluation.real_incident.llm_run import (
    HistoricalLLMRunConfigurationError,
    create_historical_llm_runtime,
    run_real_llm_historical_incident,
    safe_result_payload,
)
from services.agent_runtime.app.investigation.llm_gateway_adapter import (
    InvestigationLLMGatewayAdapter,
)
from services.agent_runtime.app.investigation.reasoner import (
    LLMInvestigationReasoner,
)
from services.agent_runtime.app.llm.gateway.models import (
    LLMGatewayResponse,
)


INCIDENT_TIME = datetime(
    2026,
    7,
    18,
    6,
    1,
    0,
    tzinfo=UTC,
)


HUMAN_SECRET = (
    "HUMAN_GROUND_TRUTH_"
    "MUST_NEVER_REACH_LLM"
)


class ScriptedGateway:
    """
    Offline Gateway transport for focused tests.

    LLMInvestigationReasoner remains real.
    """

    def __init__(
        self,
    ):
        self.requests = []

    async def chat(
        self,
        request,
    ):
        self.requests.append(
            request
        )

        marker = "State:\n"

        assert marker in (
            request.prompt
        )

        state = json.loads(
            request.prompt.split(
                marker,
                1,
            )[1]
        )

        evidence = state[
            "evidence"
        ]

        evidence_ids = [
            item[
                "evidence_id"
            ]
            for item
            in evidence
        ]

        if len(
            evidence
        ) == 0:

            payload = {
                "hypotheses": [
                    {
                        "hypothesis_id": (
                            "memory-pressure"
                        ),
                        "cause": (
                            "Pod termination may be "
                            "memory related"
                        ),
                        "confidence": 0.45,
                        "supporting_evidence_ids": [],
                        "conflicting_evidence_ids": [],
                        "missing_evidence": [
                            "pod termination state"
                        ],
                    }
                ],
                "rationale_summary": (
                    "Inspect Pod state first"
                ),
                "stop": False,
                "stop_reason": None,
                "next_probe": (
                    "kubernetes_pod_state"
                ),
                "conclusion": None,
            }

        elif len(
            evidence
        ) == 1:

            payload = {
                "hypotheses": [
                    {
                        "hypothesis_id": (
                            "memory-pressure"
                        ),
                        "cause": (
                            "OOMKilled is supported "
                            "but memory usage is needed"
                        ),
                        "confidence": 0.72,
                        "supporting_evidence_ids": (
                            evidence_ids
                        ),
                        "conflicting_evidence_ids": [],
                        "missing_evidence": [
                            "memory working set"
                        ],
                    }
                ],
                "rationale_summary": (
                    "Inspect memory working set"
                ),
                "stop": False,
                "stop_reason": None,
                "next_probe": (
                    "prometheus_memory_working_set"
                ),
                "conclusion": None,
            }

        else:

            payload = {
                "hypotheses": [
                    {
                        "hypothesis_id": (
                            "memory-pressure"
                        ),
                        "cause": (
                            "OOMKilled occurred with "
                            "high memory usage"
                        ),
                        "confidence": 0.94,
                        "supporting_evidence_ids": (
                            evidence_ids
                        ),
                        "conflicting_evidence_ids": [],
                        "missing_evidence": [],
                    }
                ],
                "rationale_summary": (
                    "Trusted evidence is sufficient"
                ),
                "stop": True,
                "stop_reason": (
                    "sufficient_evidence"
                ),
                "next_probe": None,
                "conclusion": {
                    "root_cause": (
                        "Container memory pressure "
                        "caused OOMKilled"
                    ),
                    "confidence": 0.94,
                    "evidence_ids": (
                        evidence_ids
                    ),
                    "remaining_uncertainties": [],
                },
            }

        return LLMGatewayResponse(
            content=json.dumps(
                payload
            ),
            provider=(
                "unit-real-provider"
            ),
            model=(
                "unit-test-model"
            ),
            fallback_used=False,
        )


def app_settings(
    provider,
):
    return SimpleNamespace(
        llm=SimpleNamespace(
            provider=provider
        )
    )


def historical_observation(
    *,
    observation_id,
    source,
    kind,
    offset_seconds,
    data,
):
    return {
        "observation_id": (
            observation_id
        ),
        "source": source,
        "kind": kind,
        "observed_at": (
            INCIDENT_TIME
            + timedelta(
                seconds=(
                    offset_seconds
                )
            )
        ).isoformat(),
        "production_signal": True,
        "data": data,
        "metadata": {
            "resource": (
                "payment-api"
            ),
            "namespace": (
                "payment"
            ),
            "cluster": (
                "production-a"
            ),
        },
    }


def dataset_payload():
    return {
        "schema_version": "v1",
        "incident_id": (
            "real-llm-unit-001"
        ),
        "event": {
            "header": {
                "event_id": (
                    "11111111-1111-4111-8111-111111111111"
                ),
                "trace_id": (
                    "22222222-2222-4222-8222-222222222222"
                ),
                "source": (
                    "alertmanager"
                ),
                "occurred_at": (
                    INCIDENT_TIME.isoformat()
                ),
            },
            "signal": {
                "type": "alert",
                "name": (
                    "PodOOMKilled"
                ),
                "severity": (
                    "critical"
                ),
                "message": (
                    "payment-api restarted"
                ),
                "labels": {},
            },
            "resources": [
                {
                    "kind": "pod",
                    "name": (
                        "payment-api"
                    ),
                    "namespace": (
                        "payment"
                    ),
                    "cluster": (
                        "production-a"
                    ),
                }
            ],
        },
        "observations": [
            historical_observation(
                observation_id=(
                    "obs-kubernetes"
                ),
                source=(
                    "kubernetes"
                ),
                kind=(
                    "pod_state"
                ),
                offset_seconds=-10,
                data={
                    "phase": "Running",
                    "ready": False,
                    "scheduled": True,
                    "oom_killed": True,
                    "containers": [
                        {
                            "restart_count": 7,
                            "state_reason": (
                                "CrashLoopBackOff"
                            ),
                            "last_termination_reason": (
                                "OOMKilled"
                            ),
                        }
                    ],
                },
            ),
            historical_observation(
                observation_id=(
                    "obs-memory"
                ),
                source=(
                    "prometheus"
                ),
                kind=(
                    "memory_working_set"
                ),
                offset_seconds=-5,
                data={
                    "value": (
                        520000000.0
                    ),
                },
            ),
        ],
        "timeline": [
            {
                "timeline_id": (
                    "human-timeline"
                ),
                "occurred_at": (
                    INCIDENT_TIME
                    + timedelta(
                        minutes=30
                    )
                ).isoformat(),
                "source": "human",
                "event_type": (
                    "postmortem_note"
                ),
                "summary": (
                    "Human knows the answer"
                ),
                "evidence_refs": [],
            }
        ],
        "ground_truth": {
            "root_cause": (
                HUMAN_SECRET
            ),
            "contributing_factors": [],
            "evidence_refs": [
                "obs-kubernetes",
                "obs-memory",
            ],
            "source": (
                "human-postmortem"
            ),
            "quality": "verified",
            "reviewed_at": (
                INCIDENT_TIME
                + timedelta(
                    hours=2
                )
            ).isoformat(),
            "resolution_summary": (
                "Human-only resolution"
            ),
        },
        "metadata": {
            "unit_test_only": True,
        },
    }


def install_non_mock(
    monkeypatch,
    gateway,
):
    monkeypatch.setattr(
        run_module,
        "get_settings",
        lambda: app_settings(
            "unit-real-provider"
        ),
    )

    monkeypatch.setattr(
        run_module,
        "create_llm_gateway",
        lambda: gateway,
    )


def test_real_llm_runtime_refuses_mock_provider_before_gateway_creation(
    monkeypatch,
):
    gateway_calls = {
        "count": 0
    }

    monkeypatch.setattr(
        run_module,
        "get_settings",
        lambda: app_settings(
            "mock"
        ),
    )

    def forbidden_gateway():
        gateway_calls[
            "count"
        ] += 1

        raise AssertionError(
            "Mock provider guard must run "
            "before Gateway construction"
        )

    monkeypatch.setattr(
        run_module,
        "create_llm_gateway",
        forbidden_gateway,
    )

    with pytest.raises(
        HistoricalLLMRunConfigurationError,
        match="refuses the mock provider",
    ):
        create_historical_llm_runtime()

    assert (
        gateway_calls[
            "count"
        ]
        == 0
    )


def test_historical_runtime_uses_real_llm_reasoner_and_same_gateway(
    monkeypatch,
):
    gateway = (
        ScriptedGateway()
    )

    install_non_mock(
        monkeypatch,
        gateway,
    )

    runtime = (
        create_historical_llm_runtime()
    )

    coordinator = (
        runtime.investigation_coordinator
    )

    reasoner = (
        coordinator.reasoner
    )

    assert isinstance(
        reasoner,
        LLMInvestigationReasoner,
    )

    adapter = (
        reasoner.investigation_llm
    )

    assert isinstance(
        adapter,
        InvestigationLLMGatewayAdapter,
    )

    assert (
        adapter.llm_gateway
        is gateway
    )

    assert (
        runtime.llm_gateway
        is gateway
    )

    assert (
        runtime.investigation_settings.enabled
        is True
    )

    assert not hasattr(
        runtime,
        "pipeline",
    )

    assert not hasattr(
        runtime,
        "action_runtime",
    )

    assert not hasattr(
        runtime,
        "verification_runtime",
    )

    assert not hasattr(
        runtime,
        "tools",
    )


@pytest.mark.asyncio
async def test_real_llm_reasoner_drives_historical_investigation(
    monkeypatch,
    tmp_path,
):
    gateway = (
        ScriptedGateway()
    )

    install_non_mock(
        monkeypatch,
        gateway,
    )

    incident_path = (
        tmp_path
        / "incident.json"
    )

    incident_path.write_text(
        json.dumps(
            dataset_payload()
        ),
        encoding="utf-8",
    )

    result = await (
        run_real_llm_historical_incident(
            incident_path
        )
    )

    assert (
        result.investigation.status.value
        == "concluded"
    )

    assert (
        result.investigation.tool_call_count
        == 2
    )

    assert [
        probe.value
        for probe
        in result
        .investigation
        .attempted_probes
    ] == [
        "kubernetes_pod_state",
        "prometheus_memory_working_set",
    ]

    assert (
        result.investigation.conclusion
        is not None
    )

    assert (
        result
        .investigation
        .conclusion
        .root_cause
        == (
            "Container memory pressure "
            "caused OOMKilled"
        )
    )

    assert len(
        gateway.requests
    ) == 3

    for request in (
        gateway.requests
    ):
        assert (
            request.context.agent
            == "investigation"
        )

        assert (
            request.context.require_json
            is True
        )

        assert (
            HUMAN_SECRET
            not in request.prompt
        )

        assert (
            "Human knows the answer"
            not in request.prompt
        )


@pytest.mark.asyncio
async def test_safe_result_contains_agent_work_but_no_human_answer(
    monkeypatch,
    tmp_path,
):
    gateway = (
        ScriptedGateway()
    )

    install_non_mock(
        monkeypatch,
        gateway,
    )

    incident_path = (
        tmp_path
        / "incident.json"
    )

    incident_path.write_text(
        json.dumps(
            dataset_payload()
        ),
        encoding="utf-8",
    )

    result = await (
        run_real_llm_historical_incident(
            incident_path
        )
    )

    payload = safe_result_payload(
        result
    )

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    )

    assert (
        HUMAN_SECRET
        not in serialized
    )

    assert (
        "Human knows the answer"
        not in serialized
    )

    assert (
        "Human-only resolution"
        not in serialized
    )

    assert (
        payload[
            "run_mode"
        ]
        == (
            "real_llm_historical_"
            "investigation"
        )
    )

    assert (
        payload[
            "shadow_mode"
        ]
        is True
    )

    assert (
        payload[
            "read_only"
        ]
        is True
    )

    assert (
        payload[
            "decision_influence"
        ]
        is False
    )

    assert (
        payload[
            "agent"
        ][
            "attempted_probes"
        ]
        == [
            "kubernetes_pod_state",
            (
                "prometheus_"
                "memory_working_set"
            ),
        ]
    )

    assert (
        payload[
            "agent"
        ][
            "conclusion"
        ][
            "root_cause"
        ]
        == (
            "Container memory pressure "
            "caused OOMKilled"
        )
    )
