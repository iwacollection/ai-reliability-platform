from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "change-intelligence-benchmark-v3"

AFTER_NAME = (
    "change_intelligence_benchmark_v3_after.txt"
)

ERROR_NAME = (
    "change_intelligence_benchmark_v3_error.txt"
)

EXPECTED_HASHES = {'services/agent_runtime/app/evaluation/intelligence_benchmark/scenarios.py': '7df3551eb34dcc76d8b4f063c23135e344a9f3211dd740ed185a4fc60a2c2fc3', 'services/agent_runtime/app/tools/kubernetes/change_tool.py': 'a921fb69806ed2625c796c4a601a1debf23960ab53dcee61a9b76a806d5fc95d', 'services/agent_runtime/app/investigation/probes.py': 'ac965097763e1676228ebd6a89e47987f18166ea1ae34e10391f75c45dfa8406', 'services/agent_runtime/app/investigation/reasoner.py': '92a27784ad7f20eca5b7604c2ea02df3f81669937a83243f04614c267e868656'}

SCENARIOS_SOURCE = 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\n\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (\n    BenchmarkScenario,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationProbe,\n    InvestigationStopReason,\n)\n\n\ndef _all_probes(\n    *,\n    pod_state,\n    working_set,\n    memory_limit,\n    restart_count,\n):\n    return {\n        InvestigationProbe.KUBERNETES_POD_STATE: (\n            pod_state\n        ),\n        InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET: {\n            "value_sum": float(\n                working_set\n            ),\n        },\n        InvestigationProbe.PROMETHEUS_MEMORY_LIMIT: {\n            "value_sum": float(\n                memory_limit\n            ),\n        },\n        InvestigationProbe.PROMETHEUS_RESTART_COUNT: {\n            "value_sum": float(\n                restart_count\n            ),\n        },\n    }\n\n\nSCENARIOS = [\n    BenchmarkScenario(\n        key="oom_limit_pressure",\n        title=(\n            "Clear OOM with memory pressure near container limit"\n        ),\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "payment-api restarted unexpectedly"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": False,\n                "scheduled": True,\n                "oom_killed": True,\n                "max_restart_count": 7,\n                "state_reasons": (\n                    "CrashLoopBackOff"\n                ),\n                "last_termination_reasons": (\n                    "OOMKilled"\n                ),\n            },\n            working_set=530_000_000,\n            memory_limit=536_870_912,\n            restart_count=7,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_root_cause_keyword_groups=[\n            [\n                "memory",\n                "内存",\n            ],\n            [\n                "limit",\n                "限制",\n                "oom",\n            ],\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="crashloop_not_memory",\n        title=(\n            "CrashLoop with normal memory should not be mislabeled as OOM"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api restart count is increasing"\n        ),\n        evidence_by_probe={\n            **_all_probes(\n                pod_state={\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": False,\n                    "max_restart_count": 9,\n                    "state_reasons": (\n                        "CrashLoopBackOff"\n                    ),\n                    "last_termination_reasons": (\n                        "Error"\n                    ),\n                },\n                working_set=120_000_000,\n                memory_limit=536_870_912,\n                restart_count=9,\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ): "unavailable",\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n        ],\n        hidden_missing_capability_keywords=[\n            "log",\n            "日志",\n            "stderr",\n            "stdout",\n            "container output",\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="conflicting_oom_signal",\n        title=(\n            "Alert suggests OOM while bounded evidence does not confirm it"\n        ),\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "OOM-related alert fired for payment-api"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": True,\n                "scheduled": True,\n                "oom_killed": False,\n                "max_restart_count": 1,\n                "state_reasons": "",\n                "last_termination_reasons": (\n                    "Completed"\n                ),\n            },\n            working_set=470_000_000,\n            memory_limit=536_870_912,\n            restart_count=1,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="crashloop_previous_log_rca",\n        title=(\n            "CrashLoop previous-container log provides causal startup evidence"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api restart count is increasing"\n        ),\n        evidence_by_probe={\n            **_all_probes(\n                pod_state={\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": False,\n                    "max_restart_count": 9,\n                    "state_reasons": (\n                        "CrashLoopBackOff"\n                    ),\n                    "last_termination_reasons": (\n                        "Error"\n                    ),\n                },\n                working_set=120_000_000,\n                memory_limit=536_870_912,\n                restart_count=9,\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ): {\n                "temporal_basis": (\n                    "previous_container"\n                ),\n                "container_name": (\n                    "payment-api"\n                ),\n                "previous": True,\n                "log_line_count": 2,\n                "tool_truncated": False,\n                "evidence_truncated": False,\n                "redaction_count": 1,\n                "log_excerpt": (\n                    "panic: invalid configuration: "\n                    "MAX_CONNECTIONS must be >= 1\\n"\n                    "password=[REDACTED]"\n                ),\n            },\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n        ],\n        hidden_preferred_first_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n        ],\n        hidden_root_cause_keyword_groups=[\n            [\n                "panic",\n            ],\n            [\n                "config",\n                "configuration",\n            ],\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="memory_false_alarm",\n        title=(\n            "Healthy memory state should drive safe abstention"\n        ),\n        alert_name="PodMemoryHigh",\n        alert_message=(\n            "payment-api memory alert fired"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": True,\n                "scheduled": True,\n                "oom_killed": False,\n                "max_restart_count": 0,\n                "state_reasons": "",\n                "last_termination_reasons": "",\n            },\n            working_set=220_000_000,\n            memory_limit=536_870_912,\n            restart_count=0,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_max_reasonable_tool_calls=3,\n    ),\n    BenchmarkScenario(\n        key="probe_backend_failure",\n        title=(\n            "Unavailable pod evidence must not produce fabricated RCA"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api restarts are elevated"\n        ),\n        evidence_by_probe={\n            InvestigationProbe.KUBERNETES_POD_STATE: (\n                "unavailable"\n            ),\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET: {\n                "value_sum": 150_000_000.0,\n            },\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT: {\n                "value_sum": 536_870_912.0,\n            },\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT: {\n                "value_sum": 6.0,\n            },\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_acceptable_stop_reasons=[\n            InvestigationStopReason.NO_SAFE_PROBE,\n        ],\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_preferred_first_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n        ],\n        hidden_missing_capability_keywords=[\n            "log",\n            "日志",\n            "pod state",\n            "termination",\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="oom_without_explanatory_metrics",\n        title=(\n            "OOM termination with non-explanatory sampled metrics should remain cautious"\n        ),\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "payment-api was terminated and restarted"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": True,\n                "scheduled": True,\n                "oom_killed": True,\n                "max_restart_count": 3,\n                "state_reasons": "",\n                "last_termination_reasons": (\n                    "OOMKilled"\n                ),\n            },\n            working_set=300_000_000,\n            memory_limit=1_073_741_824,\n            restart_count=3,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_missing_capability_keywords=[\n            "histor",\n            "历史",\n            "range",\n            "peak",\n            "time",\n            "日志",\n            "log",\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n]\n\n\n\nCHANGE_EVENT_TIME = datetime(\n    2026,\n    8,\n    11,\n    0,\n    20,\n    tzinfo=UTC,\n)\n\n\ndef _change_facts(\n    *,\n    revision_before,\n    revision_after,\n    image_before,\n    image_after,\n    rollout_started_at,\n    rollout_offset_seconds,\n    recent,\n    generation=9,\n    observed_generation=9,\n    replicas_desired=4,\n    replicas_updated=4,\n    replicas_ready=4,\n    replicas_available=4,\n    replicas_unavailable=0,\n    history_complete=True,\n):\n    return {\n        "temporal_basis": (\n            "workload_change_history"\n        ),\n        "owner_chain_verified": True,\n        "deployment_name": "payment-api",\n        "revision_before": revision_before,\n        "revision_after": revision_after,\n        "revision_changed": (\n            (\n                revision_before\n                != revision_after\n            )\n            if (\n                revision_before is not None\n                and revision_after is not None\n            )\n            else None\n        ),\n        "image_before": image_before,\n        "image_after": image_after,\n        "image_changed": (\n            (\n                image_before\n                != image_after\n            )\n            if (\n                image_before is not None\n                and image_after is not None\n            )\n            else None\n        ),\n        "rollout_started_at": (\n            rollout_started_at\n        ),\n        "rollout_offset_seconds": (\n            float(\n                rollout_offset_seconds\n            )\n        ),\n        "recent_rollout_before_incident": (\n            recent\n        ),\n        "generation": generation,\n        "observed_generation": (\n            observed_generation\n        ),\n        "replicas_desired": replicas_desired,\n        "replicas_updated": replicas_updated,\n        "replicas_ready": replicas_ready,\n        "replicas_available": (\n            replicas_available\n        ),\n        "replicas_unavailable": (\n            replicas_unavailable\n        ),\n        "history_complete": (\n            history_complete\n        ),\n    }\n\n\nCHANGE_SCENARIOS = [\n    BenchmarkScenario(\n        key="change_image_rollout_log_rca",\n        title=(\n            "Recent image rollout plus causal previous logs supports change RCA"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api started restarting shortly after a rollout"\n        ),\n        event_occurred_at=(\n            CHANGE_EVENT_TIME\n        ),\n        evidence_by_probe={\n            **_all_probes(\n                pod_state={\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": False,\n                    "max_restart_count": 8,\n                    "state_reasons": (\n                        "CrashLoopBackOff"\n                    ),\n                    "last_termination_reasons": (\n                        "Error"\n                    ),\n                },\n                working_set=125_000_000,\n                memory_limit=536_870_912,\n                restart_count=8,\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ): {\n                "temporal_basis": (\n                    "previous_container"\n                ),\n                "container_name": (\n                    "payment-api"\n                ),\n                "previous": True,\n                "log_line_count": 2,\n                "tool_truncated": False,\n                "evidence_truncated": False,\n                "redaction_count": 0,\n                "log_excerpt": (\n                    "panic: incompatible schema version after startup; "\n                    "payment-api:v7 expects schema v7 but runtime has v6"\n                ),\n            },\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ): _change_facts(\n                revision_before=6,\n                revision_after=7,\n                image_before=(\n                    "app=payment-api:v6"\n                ),\n                image_after=(\n                    "app=payment-api:v7"\n                ),\n                rollout_started_at=(\n                    "2026-08-11T00:15:00+00:00"\n                ),\n                rollout_offset_seconds=300,\n                recent=True,\n                replicas_ready=2,\n                replicas_available=2,\n                replicas_unavailable=2,\n            ),\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n        ],\n        hidden_preferred_first_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_root_cause_keyword_groups=[\n            [\n                "image",\n                "rollout",\n                "deploy",\n                "revision",\n                "版本",\n                "发布",\n            ],\n            [\n                "schema",\n                "incompatible",\n                "panic",\n                "兼容",\n            ],\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="recent_change_but_memory_pressure",\n        title=(\n            "Recent rollout is correlated but memory limit pressure is causal"\n        ),\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "payment-api restarted after a recent deployment"\n        ),\n        event_occurred_at=(\n            CHANGE_EVENT_TIME\n        ),\n        evidence_by_probe={\n            **_all_probes(\n                pod_state={\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": True,\n                    "max_restart_count": 5,\n                    "state_reasons": (\n                        "CrashLoopBackOff"\n                    ),\n                    "last_termination_reasons": (\n                        "OOMKilled"\n                    ),\n                },\n                working_set=530_000_000,\n                memory_limit=536_870_912,\n                restart_count=5,\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ): "unavailable",\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ): _change_facts(\n                revision_before=10,\n                revision_after=11,\n                image_before=(\n                    "app=payment-api:v10"\n                ),\n                image_after=(\n                    "app=payment-api:v11"\n                ),\n                rollout_started_at=(\n                    "2026-08-11T00:17:00+00:00"\n                ),\n                rollout_offset_seconds=180,\n                recent=True,\n            ),\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n        ],\n        hidden_root_cause_keyword_groups=[\n            [\n                "memory",\n                "内存",\n            ],\n            [\n                "limit",\n                "oom",\n                "限制",\n            ],\n        ],\n        hidden_max_reasonable_tool_calls=5,\n    ),\n    BenchmarkScenario(\n        key="stale_rollout_crashloop_unknown",\n        title=(\n            "Old rollout must not explain a new CrashLoop without mechanism evidence"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api began restarting today"\n        ),\n        event_occurred_at=(\n            CHANGE_EVENT_TIME\n        ),\n        evidence_by_probe={\n            **_all_probes(\n                pod_state={\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": False,\n                    "max_restart_count": 6,\n                    "state_reasons": (\n                        "CrashLoopBackOff"\n                    ),\n                    "last_termination_reasons": (\n                        "Error"\n                    ),\n                },\n                working_set=130_000_000,\n                memory_limit=536_870_912,\n                restart_count=6,\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ): "unavailable",\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ): _change_facts(\n                revision_before=4,\n                revision_after=5,\n                image_before=(\n                    "app=payment-api:v4"\n                ),\n                image_after=(\n                    "app=payment-api:v5"\n                ),\n                rollout_started_at=(\n                    "2026-08-08T00:20:00+00:00"\n                ),\n                rollout_offset_seconds=259200,\n                recent=False,\n            ),\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n        ],\n        hidden_missing_capability_keywords=[\n            "log",\n            "日志",\n            "mechanism",\n            "cause",\n            "原因",\n        ],\n        hidden_max_reasonable_tool_calls=5,\n    ),\n    BenchmarkScenario(\n        key="change_probe_backend_failure",\n        title=(\n            "Unavailable change backend must not produce invented rollout RCA"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api restart spike may have followed a deployment"\n        ),\n        event_occurred_at=(\n            CHANGE_EVENT_TIME\n        ),\n        evidence_by_probe={\n            **_all_probes(\n                pod_state={\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": False,\n                    "max_restart_count": 4,\n                    "state_reasons": (\n                        "CrashLoopBackOff"\n                    ),\n                    "last_termination_reasons": (\n                        "Error"\n                    ),\n                },\n                working_set=140_000_000,\n                memory_limit=536_870_912,\n                restart_count=4,\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ): "unavailable",\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ): "unavailable",\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_acceptable_stop_reasons=[\n            InvestigationStopReason.NO_SAFE_PROBE,\n        ],\n        hidden_required_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n        ],\n        hidden_preferred_first_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_missing_capability_keywords=[\n            "change",\n            "deployment",\n            "rollout",\n            "revision",\n            "变更",\n            "发布",\n        ],\n        hidden_max_reasonable_tool_calls=5,\n    ),\n    BenchmarkScenario(\n        key="recent_change_without_causal_mechanism",\n        title=(\n            "Recent image change plus failure symptom is not sufficient causal proof"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api is CrashLooping shortly after a rollout"\n        ),\n        event_occurred_at=(\n            CHANGE_EVENT_TIME\n        ),\n        evidence_by_probe={\n            **_all_probes(\n                pod_state={\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": False,\n                    "max_restart_count": 3,\n                    "state_reasons": (\n                        "CrashLoopBackOff"\n                    ),\n                    "last_termination_reasons": (\n                        "Error"\n                    ),\n                },\n                working_set=145_000_000,\n                memory_limit=536_870_912,\n                restart_count=3,\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ): "unavailable",\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ): _change_facts(\n                revision_before=20,\n                revision_after=21,\n                image_before=(\n                    "app=payment-api:v20"\n                ),\n                image_after=(\n                    "app=payment-api:v21"\n                ),\n                rollout_started_at=(\n                    "2026-08-11T00:16:00+00:00"\n                ),\n                rollout_offset_seconds=240,\n                recent=True,\n                replicas_ready=3,\n                replicas_available=3,\n                replicas_unavailable=1,\n            ),\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n        ],\n        hidden_preferred_first_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_missing_capability_keywords=[\n            "log",\n            "日志",\n            "mechanism",\n            "cause",\n            "causal",\n            "原因",\n        ],\n        hidden_max_reasonable_tool_calls=5,\n    ),\n]\n\n\nCHANGE_SCENARIO_KEYS = tuple(\n    item.key\n    for item in CHANGE_SCENARIOS\n)\n\n\ndef _change_rollout_event_facts(\n    *,\n    revision_before,\n    revision_after,\n    image_before,\n    image_after,\n    rollout_started_at,\n    rollout_offset_seconds,\n    recent,\n    rollout_condition_summary,\n    generation_observed,\n    rollout_complete,\n    rollout_failure_signal,\n    rollout_failure_reason,\n    events_status,\n    events_error_code,\n    recent_event_count,\n    recent_warning_count,\n    recent_event_reasons,\n    recent_event_summary,\n    replicas_ready=4,\n    replicas_available=4,\n    replicas_unavailable=0,\n):\n    """\n    Change #002 v2.1 synthetic evidence.\n\n    Keep the retained scalar fact count within the production EvidenceItem\n    max_length=32 contract.\n    """\n\n    return {\n        **_change_facts(\n            revision_before=revision_before,\n            revision_after=revision_after,\n            image_before=image_before,\n            image_after=image_after,\n            rollout_started_at=(\n                rollout_started_at\n            ),\n            rollout_offset_seconds=(\n                rollout_offset_seconds\n            ),\n            recent=recent,\n            replicas_ready=replicas_ready,\n            replicas_available=(\n                replicas_available\n            ),\n            replicas_unavailable=(\n                replicas_unavailable\n            ),\n        ),\n        "rollout_condition_summary": (\n            rollout_condition_summary\n        ),\n        "generation_observed": (\n            generation_observed\n        ),\n        "rollout_complete": (\n            rollout_complete\n        ),\n        "rollout_failure_signal": (\n            rollout_failure_signal\n        ),\n        "rollout_failure_reason": (\n            rollout_failure_reason\n        ),\n        "events_status": (\n            events_status\n        ),\n        "events_error_code": (\n            events_error_code\n        ),\n        "recent_event_count": (\n            recent_event_count\n        ),\n        "recent_warning_count": (\n            recent_warning_count\n        ),\n        "recent_event_reasons": (\n            recent_event_reasons\n        ),\n        "recent_event_summary": (\n            recent_event_summary\n        ),\n    }\n\n\nCHANGE_ROLLOUT_EVENT_SCENARIOS = [\n    BenchmarkScenario(\n        key="rollout_failure_events_log_rca",\n        title=(\n            "Failed rollout signals plus causal logs support change RCA"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api began failing during the latest rollout"\n        ),\n        event_occurred_at=(\n            CHANGE_EVENT_TIME\n        ),\n        evidence_by_probe={\n            **_all_probes(\n                pod_state={\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": False,\n                    "max_restart_count": 7,\n                    "state_reasons": (\n                        "CrashLoopBackOff"\n                    ),\n                    "last_termination_reasons": (\n                        "Error"\n                    ),\n                },\n                working_set=118_000_000,\n                memory_limit=536_870_912,\n                restart_count=7,\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ): {\n                "temporal_basis": (\n                    "previous_container"\n                ),\n                "container_name": (\n                    "payment-api"\n                ),\n                "previous": True,\n                "log_line_count": 2,\n                "tool_truncated": False,\n                "evidence_truncated": False,\n                "redaction_count": 0,\n                "log_excerpt": (\n                    "panic: incompatible schema version; "\n                    "payment-api:v31 cannot start against schema v30"\n                ),\n            },\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ): _change_rollout_event_facts(\n                revision_before=30,\n                revision_after=31,\n                image_before=(\n                    "app=payment-api:v30"\n                ),\n                image_after=(\n                    "app=payment-api:v31"\n                ),\n                rollout_started_at=(\n                    "2026-08-11T00:15:00+00:00"\n                ),\n                rollout_offset_seconds=300,\n                recent=True,\n                rollout_condition_summary=(\n                    "Progressing=False:ProgressDeadlineExceeded;"\n                    "Available=False:MinimumReplicasUnavailable;"\n                    "ReplicaFailure=True:FailedCreate"\n                ),\n                generation_observed=True,\n                rollout_complete=False,\n                rollout_failure_signal=True,\n                rollout_failure_reason=(\n                    "ProgressDeadlineExceeded;FailedCreate"\n                ),\n                events_status="complete",\n                events_error_code=None,\n                recent_event_count=3,\n                recent_warning_count=2,\n                recent_event_reasons=(\n                    "BackOff;FailedCreate;ScalingReplicaSet"\n                ),\n                recent_event_summary=(\n                    "2026-08-11T00:19:00+00:00 "\n                    "Pod/payment-api Warning BackOff: "\n                    "Back-off restarting failed container | "\n                    "2026-08-11T00:18:00+00:00 "\n                    "ReplicaSet/payment-api-31 Warning FailedCreate"\n                ),\n                replicas_ready=2,\n                replicas_available=2,\n                replicas_unavailable=2,\n            ),\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n        ],\n        hidden_preferred_first_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_root_cause_keyword_groups=[\n            [\n                "rollout",\n                "image",\n                "deploy",\n                "revision",\n                "发布",\n                "版本",\n            ],\n            [\n                "schema",\n                "incompatible",\n                "panic",\n                "兼容",\n            ],\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="normal_rollout_events_dependency_rca",\n        title=(\n            "Healthy rollout events must not distract from dependency failure"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api is restarting after a deployment"\n        ),\n        event_occurred_at=(\n            CHANGE_EVENT_TIME\n        ),\n        evidence_by_probe={\n            **_all_probes(\n                pod_state={\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": False,\n                    "max_restart_count": 5,\n                    "state_reasons": (\n                        "CrashLoopBackOff"\n                    ),\n                    "last_termination_reasons": (\n                        "Error"\n                    ),\n                },\n                working_set=132_000_000,\n                memory_limit=536_870_912,\n                restart_count=5,\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ): {\n                "temporal_basis": (\n                    "previous_container"\n                ),\n                "container_name": (\n                    "payment-api"\n                ),\n                "previous": True,\n                "log_line_count": 2,\n                "tool_truncated": False,\n                "evidence_truncated": False,\n                "redaction_count": 0,\n                "log_excerpt": (\n                    "startup failed: connection refused to orders-db:5432; "\n                    "dependency unavailable"\n                ),\n            },\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ): _change_rollout_event_facts(\n                revision_before=40,\n                revision_after=41,\n                image_before=(\n                    "app=payment-api:v40"\n                ),\n                image_after=(\n                    "app=payment-api:v41"\n                ),\n                rollout_started_at=(\n                    "2026-08-11T00:14:00+00:00"\n                ),\n                rollout_offset_seconds=360,\n                recent=True,\n                rollout_condition_summary=(\n                    "Progressing=True:NewReplicaSetAvailable;"\n                    "Available=True:MinimumReplicasAvailable;"\n                    "ReplicaFailure=False:-"\n                ),\n                generation_observed=True,\n                rollout_complete=True,\n                rollout_failure_signal=False,\n                rollout_failure_reason=None,\n                events_status="complete",\n                events_error_code=None,\n                recent_event_count=2,\n                recent_warning_count=0,\n                recent_event_reasons=(\n                    "ScalingReplicaSet;NewReplicaSetAvailable"\n                ),\n                recent_event_summary=(\n                    "2026-08-11T00:14:30+00:00 "\n                    "Deployment/payment-api Normal ScalingReplicaSet | "\n                    "2026-08-11T00:16:00+00:00 "\n                    "Deployment/payment-api Normal NewReplicaSetAvailable"\n                ),\n            ),\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n        ],\n        hidden_preferred_first_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_root_cause_keyword_groups=[\n            [\n                "dependency",\n                "orders-db",\n                "database",\n                "db",\n                "依赖",\n                "数据库",\n            ],\n            [\n                "connection refused",\n                "unavailable",\n                "连接",\n                "不可用",\n            ],\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="event_rbac_unavailable_core_change_rca",\n        title=(\n            "Event RBAC loss must not erase core rollout and log evidence"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api failed during a recent image rollout"\n        ),\n        event_occurred_at=(\n            CHANGE_EVENT_TIME\n        ),\n        evidence_by_probe={\n            **_all_probes(\n                pod_state={\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": False,\n                    "max_restart_count": 6,\n                    "state_reasons": (\n                        "CrashLoopBackOff"\n                    ),\n                    "last_termination_reasons": (\n                        "Error"\n                    ),\n                },\n                working_set=121_000_000,\n                memory_limit=536_870_912,\n                restart_count=6,\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ): {\n                "temporal_basis": (\n                    "previous_container"\n                ),\n                "container_name": (\n                    "payment-api"\n                ),\n                "previous": True,\n                "log_line_count": 2,\n                "tool_truncated": False,\n                "evidence_truncated": False,\n                "redaction_count": 0,\n                "log_excerpt": (\n                    "panic: required configuration key FEATURE_SCHEMA_V42 "\n                    "is missing after image v42 startup"\n                ),\n            },\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ): _change_rollout_event_facts(\n                revision_before=41,\n                revision_after=42,\n                image_before=(\n                    "app=payment-api:v41"\n                ),\n                image_after=(\n                    "app=payment-api:v42"\n                ),\n                rollout_started_at=(\n                    "2026-08-11T00:16:00+00:00"\n                ),\n                rollout_offset_seconds=240,\n                recent=True,\n                rollout_condition_summary=(\n                    "Progressing=False:ProgressDeadlineExceeded;"\n                    "Available=False:MinimumReplicasUnavailable;"\n                    "ReplicaFailure=Unknown:-"\n                ),\n                generation_observed=True,\n                rollout_complete=False,\n                rollout_failure_signal=True,\n                rollout_failure_reason=(\n                    "ProgressDeadlineExceeded"\n                ),\n                events_status="unavailable",\n                events_error_code=(\n                    "authorization_denied"\n                ),\n                recent_event_count=0,\n                recent_warning_count=0,\n                recent_event_reasons=None,\n                recent_event_summary=None,\n                replicas_ready=2,\n                replicas_available=2,\n                replicas_unavailable=2,\n            ),\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n        ],\n        hidden_preferred_first_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_root_cause_keyword_groups=[\n            [\n                "image",\n                "rollout",\n                "deploy",\n                "revision",\n                "发布",\n                "版本",\n            ],\n            [\n                "configuration",\n                "config",\n                "missing",\n                "panic",\n                "配置",\n                "缺失",\n            ],\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n]\n\n\nCHANGE_ROLLOUT_EVENT_SCENARIO_KEYS = tuple(\n    item.key\n    for item in CHANGE_ROLLOUT_EVENT_SCENARIOS\n)\n\n\nSMOKE_SCENARIO_KEYS = (\n    "oom_limit_pressure",\n    "crashloop_not_memory",\n    "conflicting_oom_signal",\n)\n\n\ndef scenarios_for_mode(\n    mode: str,\n) -> list[\n    BenchmarkScenario\n]:\n    if mode == "smoke":\n        keys = set(\n            SMOKE_SCENARIO_KEYS\n        )\n\n        return [\n            item\n            for item in SCENARIOS\n            if item.key in keys\n        ]\n\n    if mode == "full":\n        return list(\n            SCENARIOS\n        )\n\n    raise ValueError(\n        "Benchmark mode must be smoke or full"\n    )\n\n\ndef scenario_by_key(\n    key: str,\n) -> BenchmarkScenario:\n    for item in (\n        *SCENARIOS,\n        *CHANGE_SCENARIOS,\n        *CHANGE_ROLLOUT_EVENT_SCENARIOS,\n    ):\n        if item.key == key:\n            return item\n\n    raise KeyError(\n        key\n    )\n\n\n__all__ = [\n    "CHANGE_ROLLOUT_EVENT_SCENARIOS",\n    "CHANGE_ROLLOUT_EVENT_SCENARIO_KEYS",\n    "CHANGE_SCENARIOS",\n    "CHANGE_SCENARIO_KEYS",\n    "SCENARIOS",\n    "SMOKE_SCENARIO_KEYS",\n    "scenario_by_key",\n    "scenarios_for_mode",\n]\n'
TEST_SOURCE = 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\n\nimport pytest\n\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (\n    BenchmarkProbeExecutor,\n    benchmark_evidence_id,\n    run_scenario,\n)\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.scenarios import (\n    CHANGE_ROLLOUT_EVENT_SCENARIOS,\n    CHANGE_ROLLOUT_EVENT_SCENARIO_KEYS,\n    CHANGE_SCENARIOS,\n    SCENARIOS,\n    scenario_by_key,\n    scenarios_for_mode,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    IncidentHypothesis,\n    InvestigationConclusion,\n    InvestigationDecision,\n    InvestigationLimits,\n    InvestigationProbe,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    11,\n    0,\n    30,\n    tzinfo=UTC,\n)\n\n\nclass ScriptedReasoner(\n    BaseInvestigationReasoner\n):\n    def __init__(\n        self,\n        decisions,\n    ) -> None:\n        self.decisions = list(\n            decisions\n        )\n\n    async def decide(\n        self,\n        scope,\n        state,\n    ):\n        return self.decisions.pop(\n            0\n        )\n\n\ndef hypothesis(\n    *,\n    cause: str,\n    supporting=None,\n    missing=None,\n    confidence=0.8,\n):\n    return IncidentHypothesis(\n        hypothesis_id="h1",\n        cause=cause,\n        confidence=confidence,\n        supporting_evidence_ids=(\n            supporting\n            or []\n        ),\n        conflicting_evidence_ids=[],\n        missing_evidence=(\n            missing\n            or []\n        ),\n        optional_evidence=[],\n    )\n\n\ndef continue_with(\n    probe: InvestigationProbe,\n    *,\n    supporting=None,\n):\n    return InvestigationDecision(\n        hypotheses=[\n            hypothesis(\n                cause=(\n                    "candidate cause"\n                ),\n                supporting=(\n                    supporting\n                    or []\n                ),\n                missing=[\n                    "one discriminative evidence source"\n                ],\n                confidence=0.5,\n            )\n        ],\n        rationale_summary=(\n            "collect discriminative evidence"\n        ),\n        stop=False,\n        stop_reason=None,\n        next_probe=probe,\n        conclusion=None,\n    )\n\n\ndef sufficient(\n    *,\n    cause: str,\n    evidence_ids,\n):\n    return InvestigationDecision(\n        hypotheses=[\n            hypothesis(\n                cause=cause,\n                supporting=list(\n                    evidence_ids\n                ),\n                missing=[],\n                confidence=0.9,\n            )\n        ],\n        rationale_summary=(\n            "trusted independent evidence supports the conclusion"\n        ),\n        stop=True,\n        stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        next_probe=None,\n        conclusion=InvestigationConclusion(\n            root_cause=cause,\n            confidence=0.9,\n            evidence_ids=list(\n                evidence_ids\n            ),\n            remaining_uncertainties=[],\n        ),\n    )\n\n\ndef test_historical_benchmark_sets_remain_unchanged():\n    assert len(\n        SCENARIOS\n    ) == 7\n\n    assert len(\n        scenarios_for_mode(\n            "full"\n        )\n    ) == 7\n\n    assert len(\n        scenarios_for_mode(\n            "smoke"\n        )\n    ) == 3\n\n    assert len(\n        CHANGE_SCENARIOS\n    ) == 5\n\n\ndef test_rollout_event_suite_is_separate_and_addressable():\n    assert len(\n        CHANGE_ROLLOUT_EVENT_SCENARIOS\n    ) == 3\n\n    assert len(\n        CHANGE_ROLLOUT_EVENT_SCENARIO_KEYS\n    ) == 3\n\n    assert {\n        item.key\n        for item\n        in CHANGE_ROLLOUT_EVENT_SCENARIOS\n    } == {\n        "rollout_failure_events_log_rca",\n        "normal_rollout_events_dependency_rca",\n        "event_rbac_unavailable_core_change_rca",\n    }\n\n    for key in (\n        CHANGE_ROLLOUT_EVENT_SCENARIO_KEYS\n    ):\n        assert (\n            scenario_by_key(\n                key\n            ).key\n            == key\n        )\n\n\n@pytest.mark.asyncio\nasync def test_rollout_event_change_facts_respect_production_contract():\n    for scenario in (\n        CHANGE_ROLLOUT_EVENT_SCENARIOS\n    ):\n        executor = BenchmarkProbeExecutor(\n            scenario,\n            observed_at=NOW,\n        )\n\n        evidence = await executor.collect(\n            None,\n            None,\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n        )\n\n        assert evidence.source == (\n            "kubernetes_change"\n        )\n\n        assert evidence.trusted is True\n\n        assert len(\n            evidence.facts\n        ) <= 32\n\n        assert (\n            "rollout_condition_summary"\n            in evidence.facts\n        )\n\n        assert (\n            "events_status"\n            in evidence.facts\n        )\n\n\ndef test_failed_rollout_scenario_contains_discriminative_warning_signals():\n    scenario = scenario_by_key(\n        "rollout_failure_events_log_rca"\n    )\n\n    facts = scenario.evidence_by_probe[\n        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE\n    ]\n\n    assert facts[\n        "rollout_failure_signal"\n    ] is True\n\n    assert (\n        "ProgressDeadlineExceeded"\n        in facts[\n            "rollout_failure_reason"\n        ]\n    )\n\n    assert facts[\n        "recent_warning_count"\n    ] == 2\n\n    assert (\n        "FailedCreate"\n        in facts[\n            "recent_event_reasons"\n        ]\n    )\n\n\ndef test_normal_event_scenario_explicitly_marks_rollout_healthy():\n    scenario = scenario_by_key(\n        "normal_rollout_events_dependency_rca"\n    )\n\n    facts = scenario.evidence_by_probe[\n        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE\n    ]\n\n    assert facts[\n        "rollout_failure_signal"\n    ] is False\n\n    assert facts[\n        "rollout_complete"\n    ] is True\n\n    assert facts[\n        "recent_warning_count"\n    ] == 0\n\n    assert (\n        "ScalingReplicaSet"\n        in facts[\n            "recent_event_reasons"\n        ]\n    )\n\n\ndef test_event_rbac_scenario_retains_core_change_facts():\n    scenario = scenario_by_key(\n        "event_rbac_unavailable_core_change_rca"\n    )\n\n    facts = scenario.evidence_by_probe[\n        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE\n    ]\n\n    assert facts[\n        "revision_before"\n    ] == 41\n\n    assert facts[\n        "revision_after"\n    ] == 42\n\n    assert facts[\n        "events_status"\n    ] == "unavailable"\n\n    assert facts[\n        "events_error_code"\n    ] == "authorization_denied"\n\n    assert facts[\n        "rollout_failure_signal"\n    ] is True\n\n\n@pytest.mark.asyncio\nasync def test_failed_rollout_plus_logs_scores_as_grounded_change_rca():\n    scenario = scenario_by_key(\n        "rollout_failure_events_log_rca"\n    )\n\n    change_id = benchmark_evidence_id(\n        scenario.key,\n        (\n            InvestigationProbe\n            .KUBERNETES_WORKLOAD_CHANGE\n        ),\n    )\n\n    logs_id = benchmark_evidence_id(\n        scenario.key,\n        (\n            InvestigationProbe\n            .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ),\n    )\n\n    reasoner = ScriptedReasoner(\n        [\n            continue_with(\n                (\n                    InvestigationProbe\n                    .KUBERNETES_WORKLOAD_CHANGE\n                ),\n            ),\n            continue_with(\n                (\n                    InvestigationProbe\n                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n                ),\n                supporting=[\n                    change_id\n                ],\n            ),\n            sufficient(\n                cause=(\n                    "image rollout introduced an incompatible schema panic"\n                ),\n                evidence_ids=[\n                    change_id,\n                    logs_id,\n                ],\n            ),\n        ]\n    )\n\n    result = await run_scenario(\n        reasoner=reasoner,\n        scenario=scenario,\n        limits=InvestigationLimits(),\n        observed_at=NOW,\n    )\n\n    assert result.outcome_correct is True\n    assert result.grounding_correct is True\n\n    assert (\n        result.root_cause_or_abstention_correct\n        is True\n    )\n\n\n@pytest.mark.asyncio\nasync def test_healthy_rollout_plus_dependency_logs_scores_dependency_rca():\n    scenario = scenario_by_key(\n        "normal_rollout_events_dependency_rca"\n    )\n\n    change_id = benchmark_evidence_id(\n        scenario.key,\n        (\n            InvestigationProbe\n            .KUBERNETES_WORKLOAD_CHANGE\n        ),\n    )\n\n    logs_id = benchmark_evidence_id(\n        scenario.key,\n        (\n            InvestigationProbe\n            .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ),\n    )\n\n    reasoner = ScriptedReasoner(\n        [\n            continue_with(\n                (\n                    InvestigationProbe\n                    .KUBERNETES_WORKLOAD_CHANGE\n                ),\n            ),\n            continue_with(\n                (\n                    InvestigationProbe\n                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n                ),\n                supporting=[\n                    change_id\n                ],\n            ),\n            sufficient(\n                cause=(\n                    "orders-db dependency is unavailable and connection is refused"\n                ),\n                evidence_ids=[\n                    logs_id\n                ],\n            ),\n        ]\n    )\n\n    result = await run_scenario(\n        reasoner=reasoner,\n        scenario=scenario,\n        limits=InvestigationLimits(),\n        observed_at=NOW,\n    )\n\n    assert result.outcome_correct is True\n\n    assert (\n        result.root_cause_or_abstention_correct\n        is True\n    )\n\n    assert result.guard_rescued is False\n\n\n@pytest.mark.asyncio\nasync def test_event_rbac_loss_does_not_block_change_plus_logs_rca():\n    scenario = scenario_by_key(\n        "event_rbac_unavailable_core_change_rca"\n    )\n\n    change_id = benchmark_evidence_id(\n        scenario.key,\n        (\n            InvestigationProbe\n            .KUBERNETES_WORKLOAD_CHANGE\n        ),\n    )\n\n    logs_id = benchmark_evidence_id(\n        scenario.key,\n        (\n            InvestigationProbe\n            .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ),\n    )\n\n    reasoner = ScriptedReasoner(\n        [\n            continue_with(\n                (\n                    InvestigationProbe\n                    .KUBERNETES_WORKLOAD_CHANGE\n                ),\n            ),\n            continue_with(\n                (\n                    InvestigationProbe\n                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n                ),\n                supporting=[\n                    change_id\n                ],\n            ),\n            sufficient(\n                cause=(\n                    "image rollout requires a missing configuration key and panics"\n                ),\n                evidence_ids=[\n                    change_id,\n                    logs_id,\n                ],\n            ),\n        ]\n    )\n\n    result = await run_scenario(\n        reasoner=reasoner,\n        scenario=scenario,\n        limits=InvestigationLimits(),\n        observed_at=NOW,\n    )\n\n    assert result.outcome_correct is True\n    assert result.grounding_correct is True\n\n    assert (\n        result.root_cause_or_abstention_correct\n        is True\n    )\n'


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def find_repo_root(
    start: Path,
) -> Path:
    for candidate in (
        start,
        *start.parents,
    ):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate

    raise RuntimeError(
        "Repository root not found."
    )


def normalize_text(
    value: str,
) -> str:
    return (
        value
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def read_text(
    path: Path,
) -> str:
    return normalize_text(
        path.read_text(
            encoding="utf-8-sig",
            errors="strict",
        )
    )


def write_text(
    path: Path,
    value: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        normalize_text(
            value
        ),
        encoding="utf-8",
        newline="\n",
    )


def sha256_text(
    value: str,
) -> str:
    return hashlib.sha256(
        normalize_text(
            value
        ).encode(
            "utf-8"
        )
    ).hexdigest()


def backup_file(
    path: Path,
) -> Path:
    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup = path.with_name(
        f"{path.name}.before_{VERSION}_{stamp}.bak"
    )

    shutil.copy2(
        path,
        backup,
    )

    return backup


def run_command(
    *,
    root: Path,
    name: str,
    command: list[str],
) -> CommandResult:
    process = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    return CommandResult(
        name=name,
        command=command,
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )


def section(
    lines: list[str],
    title: str,
) -> None:
    lines.extend(
        [
            "",
            "=" * 120,
            title,
            "=" * 120,
            "",
        ]
    )


def add_command(
    lines: list[str],
    result: CommandResult,
) -> None:
    section(
        lines,
        f"COMMAND: {result.name}",
    )

    lines.extend(
        [
            " ".join(
                result.command
            ),
            "",
            f"ExitCode: {result.returncode}",
            "",
            "STDOUT",
            "-" * 120,
            result.stdout.rstrip()
            or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            result.stderr.rstrip()
            or "<EMPTY>",
        ]
    )


def verify_hash(
    *,
    root: Path,
    relative: str,
) -> None:
    path = root / relative

    if not path.exists():
        raise RuntimeError(
            f"Required current file is missing: {relative}"
        )

    actual = sha256_text(
        read_text(
            path
        )
    )

    expected = EXPECTED_HASHES[
        relative
    ]

    if actual != expected:
        raise RuntimeError(
            (
                f"{relative} changed after the reviewed current version. "
                f"expected_sha256={expected} actual_sha256={actual}. "
                "Refusing stale Change Benchmark v3 installation."
            )
        )


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after = root / AFTER_NAME
    error = root / ERROR_NAME

    for output in (
        after,
        error,
    ):
        try:
            output.unlink()
        except FileNotFoundError:
            pass

    scenarios_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "evaluation"
        / "intelligence_benchmark"
        / "scenarios.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_change_rollout_event_benchmark.py"
    )

    sources = {
        scenarios_file: SCENARIOS_SOURCE,
        test_file: TEST_SOURCE,
    }

    targets = list(
        sources.keys()
    )

    preexisting = {
        path: path.exists()
        for path in targets
    }

    backups = []

    report = [
        "Change Intelligence Benchmark v3",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Purpose:",
        "- preserve historical Full 7 and Change 5 benchmark baselines",
        "- add a separate 3-scenario rollout/event evidence suite for Change #002 v2.1",
        "- validate failed rollout signals, unrelated healthy rollout events, and Event RBAC degradation",
        "",
        "New deterministic scenarios:",
        "- rollout_failure_events_log_rca",
        "- normal_rollout_events_dependency_rca",
        "- event_rbac_unavailable_core_change_rca",
        "",
        "Evidence contract:",
        "- synthetic Change v2.1 facts must construct a real EvidenceItem",
        "- retained facts must remain <= 32",
        "- all facts remain scalar/bounded",
        "",
        "Safety:",
        "- benchmark-only scenario/test files are modified",
        "- no Runtime / Tool / Reasoner / Guard authority is changed",
        "- no real LLM/Kubernetes/Prometheus request is sent",
    ]

    try:
        section(
            report,
            "CURRENT HASH PREFLIGHT",
        )

        for relative in EXPECTED_HASHES:
            verify_hash(
                root=root,
                relative=relative,
            )

            report.append(
                relative
                + "="
                + EXPECTED_HASHES[
                    relative
                ]
            )

        section(
            report,
            "BACKUP",
        )

        for path in targets:
            if path.exists():
                backup = backup_file(
                    path
                )

                backups.append(
                    (
                        path,
                        backup,
                    )
                )

                report.append(
                    "backup="
                    + str(
                        backup.relative_to(
                            root
                        )
                    )
                )

        for path, source in sources.items():
            write_text(
                path,
                source,
            )

        syntax = run_command(
            root=root,
            name="Python syntax",
            command=[
                "uv",
                "run",
                "python",
                "-m",
                "py_compile",
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
                    for path in targets
                ],
            ],
        )

        add_command(
            report,
            syntax,
        )

        if syntax.returncode != 0:
            raise RuntimeError(
                "Change Benchmark v3 syntax failed"
            )

        focused = run_command(
            root=root,
            name="Change rollout/event benchmark focused suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_change_rollout_event_benchmark.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_change_intelligence_benchmark.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_intelligence_benchmark.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_logs_benchmark.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            focused,
        )

        if focused.returncode != 0:
            raise RuntimeError(
                "Change rollout/event benchmark focused tests failed"
            )

        compatibility = run_command(
            root=root,
            name="Change #002 v2.1 compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_change_rollout_evidence.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_change_capability.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_final_synthesis_budget_discipline.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_evidence_consistency.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            compatibility,
        )

        if compatibility.returncode != 0:
            raise RuntimeError(
                "Change Benchmark v3 compatibility tests failed"
            )

        preflight = run_command(
            root=root,
            name="Benchmark isolation / evidence contract preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from services.agent_runtime.app.evaluation."
                    "intelligence_benchmark.scenarios import "
                    "SCENARIOS,CHANGE_SCENARIOS,CHANGE_ROLLOUT_EVENT_SCENARIOS,"
                    "CHANGE_ROLLOUT_EVENT_SCENARIO_KEYS,scenarios_for_mode,scenario_by_key; "
                    "from services.agent_runtime.app.evaluation."
                    "intelligence_benchmark.engine import BenchmarkProbeExecutor; "
                    "from services.agent_runtime.app.investigation.models import InvestigationProbe; "
                    "from datetime import UTC,datetime; import asyncio; "
                    "assert len(SCENARIOS)==7; "
                    "assert len(scenarios_for_mode('full'))==7; "
                    "assert len(scenarios_for_mode('smoke'))==3; "
                    "assert len(CHANGE_SCENARIOS)==5; "
                    "assert len(CHANGE_ROLLOUT_EVENT_SCENARIOS)==3; "
                    "assert all(scenario_by_key(k).key==k for k in CHANGE_ROLLOUT_EVENT_SCENARIO_KEYS); "
                    "async def check(): "
                    "\n    "
                    "[(lambda e: (_ for _ in ()).throw(AssertionError('facts > 32')) if len(e.facts)>32 else None)"
                    "(await BenchmarkProbeExecutor(s,observed_at=datetime(2026,8,11,tzinfo=UTC)).collect(None,None,InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE)) "
                    "for s in CHANGE_ROLLOUT_EVENT_SCENARIOS]; "
                    "\n"
                    "asyncio.run(check()); "
                    "print('full=7 smoke=3 change=5 rollout_event=3 facts_contract=ok')"
                ),
            ],
        )

        add_command(
            report,
            preflight,
        )

        if preflight.returncode != 0:
            raise RuntimeError(
                "Change Benchmark v3 preflight failed"
            )

        authority = run_command(
            root=root,
            name="Benchmark-only authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "s=Path(r'services/agent_runtime/app/evaluation/"
                    "intelligence_benchmark/scenarios.py').read_text(encoding='utf-8'); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService','VerificationRuntime',"
                    "'.post(','.patch(','.put(','.delete('] if x in s]; "
                    "print('forbidden_matches='+str(bad)); "
                    "raise SystemExit(1 if bad else 0)"
                ),
            ],
        )

        add_command(
            report,
            authority,
        )

        if authority.returncode != 0:
            raise RuntimeError(
                "Change Benchmark v3 authority boundary failed"
            )

        status = run_command(
            root=root,
            name="Git status",
            command=[
                "git",
                "status",
                "--short",
                "--",
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
                    for path in targets
                ],
            ],
        )

        add_command(
            report,
            status,
        )

        section(
            report,
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "Change Intelligence Benchmark v3 is installed.",
                "",
                "Historical benchmark sets remain:",
                "- Full = 7",
                "- Smoke = 3",
                "- Change = 5",
                "",
                "New rollout/event suite:",
                "- 3 scenarios",
                "- separately addressable through scenario_by_key",
                "- no real model tokens required for installation/validation",
                "",
                "Future real-model command can run only these 3 scenarios x N through batch v2.",
            ]
        )

        write_text(
            after,
            "\n".join(
                report
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "CHANGE INTELLIGENCE BENCHMARK V3 PASSED"
        )
        print("=" * 72)
        print("")
        print(
            "No real LLM/Kubernetes/Prometheus request was sent."
        )
        print("")
        print("Upload only:")
        print(after)

        return 0

    except Exception as exc:
        rollback = []

        for original, backup in reversed(
            backups
        ):
            try:
                shutil.copy2(
                    backup,
                    original,
                )

                rollback.append(
                    "RESTORED "
                    + str(
                        original.relative_to(
                            root
                        )
                    )
                )

            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK FAILED "
                    + str(
                        original.relative_to(
                            root
                        )
                    )
                    + ": "
                    + f"{type(rollback_exc).__name__}: {rollback_exc}"
                )

        for path in targets:
            if (
                not preexisting[
                    path
                ]
                and path.exists()
            ):
                try:
                    path.unlink()

                    rollback.append(
                        "REMOVED newly-created "
                        + str(
                            path.relative_to(
                                root
                            )
                        )
                    )

                except Exception as rollback_exc:
                    rollback.append(
                        "ROLLBACK REMOVE FAILED "
                        + str(
                            path.relative_to(
                                root
                            )
                        )
                        + ": "
                        + f"{type(rollback_exc).__name__}: {rollback_exc}"
                    )

        write_text(
            error,
            "\n".join(
                [
                    "Change Intelligence Benchmark v3 FAILED",
                    f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
                    "",
                    f"{type(exc).__name__}: {exc}",
                    "",
                    traceback.format_exc(),
                    "",
                    "ROLLBACK",
                    "=" * 120,
                    *rollback,
                    "",
                    "PARTIAL REPORT",
                    "=" * 120,
                    *report,
                ]
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "CHANGE INTELLIGENCE BENCHMARK V3 FAILED"
        )
        print("=" * 72)
        print("")
        print(
            "Modified files were rolled back where possible."
        )
        print("")
        print("Upload only:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
