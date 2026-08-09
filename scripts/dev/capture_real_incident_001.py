from __future__ import annotations

import json
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


AFTER_NAME = "real_incident_001_capture_v1_after.txt"
ERROR_NAME = "real_incident_001_capture_v1_error.txt"

DATASET_RELATIVE_PATH = (
    "evaluation_data/real_incidents/incident_001.json"
)


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
        "Repository root not found. "
        "Run this script from inside ai-reliability-platform."
    )


def install_import_paths(
    root: Path,
) -> None:
    candidates = [
        root,
        root / "packages" / "common" / "src",
    ]

    for candidate in reversed(
        candidates
    ):
        value = str(
            candidate
        )

        if value not in sys.path:
            sys.path.insert(
                0,
                value,
            )


def write_text(
    path: Path,
    text: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        (
            text
            .replace(
                "\r\n",
                "\n",
            )
            .replace(
                "\r",
                "\n",
            )
        ),
        encoding="utf-8",
        newline="\n",
    )


def prompt_required(
    label: str,
) -> str:
    while True:
        value = input(
            f"{label}: "
        ).strip()

        if value:
            return value

        print(
            "  Required. Please enter a value."
        )


def prompt_optional(
    label: str,
) -> str | None:
    value = input(
        f"{label} [optional]: "
    ).strip()

    return (
        value
        if value
        else None
    )


def prompt_bool(
    label: str,
    *,
    default: bool | None = None,
) -> bool | None:
    default_text = ""

    if default is True:
        default_text = " [Y/n]"

    elif default is False:
        default_text = " [y/N]"

    else:
        default_text = " [y/n, blank=unknown]"

    while True:
        raw = input(
            f"{label}{default_text}: "
        ).strip().lower()

        if not raw:
            return default

        if raw in {
            "y",
            "yes",
            "true",
            "1",
        }:
            return True

        if raw in {
            "n",
            "no",
            "false",
            "0",
        }:
            return False

        print(
            "  Enter y / n, or blank when unknown."
        )


def prompt_int_optional(
    label: str,
) -> int | None:
    while True:
        raw = input(
            f"{label} [optional integer]: "
        ).strip()

        if not raw:
            return None

        try:
            return int(
                raw
            )

        except ValueError:
            print(
                "  Enter an integer or leave blank."
            )


def prompt_float_optional(
    label: str,
) -> float | None:
    while True:
        raw = input(
            f"{label} [optional number]: "
        ).strip()

        if not raw:
            return None

        try:
            return float(
                raw
            )

        except ValueError:
            print(
                "  Enter a number or leave blank."
            )


def prompt_choice(
    label: str,
    choices: tuple[str, ...],
    *,
    default: str | None = None,
) -> str:
    choice_text = "/".join(
        choices
    )

    suffix = (
        f" [{choice_text}, default={default}]"
        if default is not None
        else f" [{choice_text}]"
    )

    while True:
        raw = input(
            f"{label}{suffix}: "
        ).strip().lower()

        if not raw and default is not None:
            return default

        if raw in choices:
            return raw

        print(
            f"  Choose one of: {choice_text}"
        )


def prompt_timestamp_required(
    label: str,
) -> str:
    while True:
        value = prompt_required(
            (
                f"{label} "
                "(ISO-8601 with timezone, "
                "example 2026-07-18T06:01:00+08:00)"
            )
        )

        try:
            parsed = datetime.fromisoformat(
                value.replace(
                    "Z",
                    "+00:00",
                )
            )

        except ValueError:
            print(
                "  Invalid ISO-8601 timestamp."
            )
            continue

        if parsed.tzinfo is None:
            print(
                "  Timestamp must include timezone."
            )
            continue

        return value


def prompt_timestamp_optional(
    label: str,
) -> str | None:
    while True:
        raw = input(
            (
                f"{label} "
                "[optional ISO-8601 with timezone]: "
            )
        ).strip()

        if not raw:
            return None

        try:
            parsed = datetime.fromisoformat(
                raw.replace(
                    "Z",
                    "+00:00",
                )
            )

        except ValueError:
            print(
                "  Invalid ISO-8601 timestamp."
            )
            continue

        if parsed.tzinfo is None:
            print(
                "  Timestamp must include timezone."
            )
            continue

        return raw


def bytes_from_mib(
    mib: float,
) -> float:
    return (
        mib
        * 1024.0
        * 1024.0
    )


def scope_metadata(
    *,
    resource: str,
    namespace: str,
    cluster: str | None,
) -> dict[str, Any]:
    return {
        "resource": resource,
        "namespace": namespace,
        "cluster": cluster,
    }


def collect_pod_state(
    *,
    resource: str,
    namespace: str,
    cluster: str | None,
) -> dict[str, Any] | None:
    observed_at = prompt_timestamp_optional(
        "Pod state observed_at"
    )

    if observed_at is None:
        return None

    print("")
    print(
        "Pod state facts. Blank is allowed when genuinely unknown."
    )

    phase = (
        prompt_optional(
            "Pod phase"
        )
        or "Unknown"
    )

    ready = prompt_bool(
        "Pod ready"
    )

    scheduled = prompt_bool(
        "Pod scheduled"
    )

    oom_killed = prompt_bool(
        "OOMKilled observed",
        default=False,
    )

    restart_count = prompt_int_optional(
        "Container restart_count"
    )

    state_reason = prompt_optional(
        "Container current state reason"
    )

    termination_reason = prompt_optional(
        "Container last termination reason"
    )

    container: dict[str, Any] = {}

    if restart_count is not None:
        container[
            "restart_count"
        ] = restart_count

    if state_reason is not None:
        container[
            "state_reason"
        ] = state_reason

    if termination_reason is not None:
        container[
            "last_termination_reason"
        ] = termination_reason

    return {
        "observation_id": (
            "obs-pod-state-001"
        ),
        "source": "kubernetes",
        "kind": "pod_state",
        "observed_at": observed_at,
        "production_signal": True,
        "data": {
            "phase": phase,
            "ready": ready,
            "scheduled": scheduled,
            "oom_killed": oom_killed,
            "containers": [
                container
            ],
        },
        "metadata": scope_metadata(
            resource=resource,
            namespace=namespace,
            cluster=cluster,
        ),
    }


def collect_metric(
    *,
    observation_id: str,
    kind: str,
    label: str,
    unit_hint: str,
    resource: str,
    namespace: str,
    cluster: str | None,
    convert_mib: bool,
) -> dict[str, Any] | None:
    observed_at = prompt_timestamp_optional(
        f"{label} observed_at"
    )

    if observed_at is None:
        return None

    value = prompt_float_optional(
        f"{label} value ({unit_hint})"
    )

    if value is None:
        print(
            "  Observation skipped because no numeric value was supplied."
        )
        return None

    normalized_value = (
        bytes_from_mib(
            value
        )
        if convert_mib
        else value
    )

    return {
        "observation_id": observation_id,
        "source": "prometheus",
        "kind": kind,
        "observed_at": observed_at,
        "production_signal": True,
        "data": {
            "value": normalized_value,
        },
        "metadata": scope_metadata(
            resource=resource,
            namespace=namespace,
            cluster=cluster,
        ),
    }


def collect_timeline(
) -> list[dict[str, Any]]:
    timeline: list[
        dict[str, Any]
    ] = []

    print("")
    print(
        "Optional human/system timeline."
    )
    print(
        "Add only facts that actually happened. "
        "Leave the first timestamp blank to skip the timeline."
    )

    index = 1

    while True:
        occurred_at = prompt_timestamp_optional(
            (
                f"Timeline #{index} occurred_at"
            )
        )

        if occurred_at is None:
            break

        source = prompt_required(
            f"Timeline #{index} source"
        )

        event_type = prompt_required(
            (
                f"Timeline #{index} event type "
                "(example alert_fired / operator_action / recovered)"
            )
        )

        summary = (
            prompt_optional(
                f"Timeline #{index} summary"
            )
            or ""
        )

        timeline.append(
            {
                "timeline_id": (
                    f"tl-{index:03d}"
                ),
                "occurred_at": occurred_at,
                "source": source,
                "event_type": event_type,
                "summary": summary,
                "evidence_refs": [],
            }
        )

        index += 1

        add_more = prompt_bool(
            "Add another timeline entry",
            default=False,
        )

        if add_more is not True:
            break

    return timeline


def collect_dataset(
) -> dict[str, Any]:
    print("")
    print(
        "=" * 72
    )
    print(
        "REAL INCIDENT #001 CAPTURE"
    )
    print(
        "=" * 72
    )
    print("")
    print(
        "Enter only facts from a real incident."
    )
    print(
        "You may anonymize service/cluster names, "
        "but do not invent observations or root cause."
    )
    print(
        "For an unknown observation timestamp, leave it blank; "
        "that observation will not be included."
    )
    print("")

    incident_id = prompt_required(
        (
            "Incident ID "
            "(anonymized name is fine, example real-oom-001)"
        )
    )

    occurred_at = prompt_timestamp_required(
        "Alert occurred_at"
    )

    alert_name = prompt_required(
        "Alert name"
    )

    severity = prompt_choice(
        "Severity",
        (
            "info",
            "warning",
            "critical",
        ),
        default="critical",
    )

    alert_message = (
        prompt_optional(
            "Alert message"
        )
        or ""
    )

    resource = prompt_required(
        "Pod/resource name"
    )

    namespace = (
        prompt_optional(
            "Namespace"
        )
        or "default"
    )

    cluster = prompt_optional(
        "Cluster"
    )

    event_id = str(
        uuid4()
    )

    trace_id = str(
        uuid4()
    )

    print("")
    print(
        "--- Historical production observations ---"
    )
    print(
        "Current Agent supports Pod state + three memory/restart metrics."
    )

    observations: list[
        dict[str, Any]
    ] = []

    pod_state = collect_pod_state(
        resource=resource,
        namespace=namespace,
        cluster=cluster,
    )

    if pod_state is not None:
        observations.append(
            pod_state
        )

    memory_working_set = collect_metric(
        observation_id=(
            "obs-memory-working-set-001"
        ),
        kind="memory_working_set",
        label="Memory working set",
        unit_hint="MiB",
        resource=resource,
        namespace=namespace,
        cluster=cluster,
        convert_mib=True,
    )

    if memory_working_set is not None:
        observations.append(
            memory_working_set
        )

    memory_limit = collect_metric(
        observation_id=(
            "obs-memory-limit-001"
        ),
        kind="memory_limit",
        label="Memory limit",
        unit_hint="MiB",
        resource=resource,
        namespace=namespace,
        cluster=cluster,
        convert_mib=True,
    )

    if memory_limit is not None:
        observations.append(
            memory_limit
        )

    restart_count = collect_metric(
        observation_id=(
            "obs-restart-count-001"
        ),
        kind="restart_count",
        label="Restart count",
        unit_hint="count",
        resource=resource,
        namespace=namespace,
        cluster=cluster,
        convert_mib=False,
    )

    if restart_count is not None:
        observations.append(
            restart_count
        )

    timeline = collect_timeline()

    print("")
    print(
        "--- Human-verified ground truth ---"
    )
    print(
        "This section will NOT be included in the Agent replay source."
    )

    root_cause = prompt_required(
        "Verified root cause"
    )

    contributing_raw = (
        prompt_optional(
            (
                "Contributing factors "
                "(semicolon-separated)"
            )
        )
        or ""
    )

    contributing_factors = [
        item.strip()
        for item
        in contributing_raw.split(
            ";"
        )
        if item.strip()
    ]

    ground_truth_source = prompt_required(
        (
            "Ground truth source "
            "(example postmortem / ticket / operator_review)"
        )
    )

    quality = prompt_choice(
        "Ground truth quality",
        (
            "verified",
            "reviewed",
            "provisional",
        ),
        default="verified",
    )

    reviewed_at = prompt_timestamp_optional(
        "Ground truth reviewed_at"
    )

    resolution_summary = prompt_optional(
        "Resolution summary"
    )

    evidence_refs = [
        item[
            "observation_id"
        ]
        for item
        in observations
    ]

    return {
        "schema_version": "v1",
        "incident_id": incident_id,
        "event": {
            "header": {
                "event_id": event_id,
                "trace_id": trace_id,
                "source": "alertmanager",
                "occurred_at": occurred_at,
            },
            "signal": {
                "type": "alert",
                "name": alert_name,
                "severity": severity,
                "message": alert_message,
                "labels": {},
            },
            "resources": [
                {
                    "kind": "pod",
                    "name": resource,
                    "namespace": namespace,
                    "cluster": cluster,
                }
            ],
        },
        "observations": observations,
        "timeline": timeline,
        "ground_truth": {
            "root_cause": root_cause,
            "contributing_factors": (
                contributing_factors
            ),
            "evidence_refs": (
                evidence_refs
            ),
            "source": (
                ground_truth_source
            ),
            "quality": quality,
            "reviewed_at": reviewed_at,
            "resolution_summary": (
                resolution_summary
            ),
        },
        "metadata": {
            "dataset_origin": (
                "human_entered_real_incident"
            ),
            "anonymized": True,
            "test_fixture": False,
        },
    }


def validate_dataset(
    *,
    root: Path,
    dataset_path: Path,
) -> dict[str, Any]:
    install_import_paths(
        root
    )

    from services.agent_runtime.app.evaluation.real_incident.loader import (
        RealIncidentDatasetLoader,
    )

    dataset = (
        RealIncidentDatasetLoader()
        .load(
            dataset_path
        )
    )

    replay = (
        dataset.to_replay_source()
    )

    replay_fields = set(
        type(
            replay
        ).model_fields
    )

    if "ground_truth" in replay_fields:
        raise RuntimeError(
            "Ground Truth leaked into replay source"
        )

    if "timeline" in replay_fields:
        raise RuntimeError(
            "Human timeline leaked into replay source"
        )

    production_observations = [
        item
        for item
        in dataset.observations
        if item.production_signal
    ]

    supported_kinds = {
        "pod_state",
        "memory_working_set",
        "memory_limit",
        "restart_count",
    }

    supported_observations = [
        item
        for item
        in production_observations
        if item.kind in supported_kinds
    ]

    return {
        "incident_id": (
            dataset.incident_id
        ),
        "event_id": str(
            dataset.event.header.event_id
        ),
        "trace_id": str(
            dataset.event.header.trace_id
        ),
        "observation_count": len(
            dataset.observations
        ),
        "supported_observation_count": len(
            supported_observations
        ),
        "timeline_count": len(
            dataset.timeline
        ),
        "ground_truth_source": (
            dataset.ground_truth.source
        ),
        "replay_has_ground_truth": False,
        "replay_has_timeline": False,
    }


def run_tests(
    *,
    root: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "uv",
            "run",
            "pytest",
            (
                "services/agent_runtime/tests/"
                "test_real_incident_dataset.py"
            ),
            (
                "services/agent_runtime/tests/"
                "test_historical_evidence_replay.py"
            ),
            (
                "services/agent_runtime/tests/"
                "test_historical_incident_investigation_runner.py"
            ),
            "-q",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    install_import_paths(
        root
    )

    after_path = (
        root
        / AFTER_NAME
    )

    error_path = (
        root
        / ERROR_NAME
    )

    dataset_path = (
        root
        / DATASET_RELATIVE_PATH
    )

    for output in (
        after_path,
        error_path,
    ):
        try:
            output.unlink()

        except FileNotFoundError:
            pass

    report = [
        "Real Incident #001 Capture v1",
        (
            "GeneratedAt: "
            + datetime.now()
            .astimezone()
            .isoformat()
        ),
        "",
        "Purpose:",
        "- capture one real anonymized incident",
        "- generate canonical RealIncidentDataset JSON",
        "- generate valid event_id / trace_id UUID values",
        "- validate Ground Truth isolation",
        "- run focused Dataset / Historical Replay / Runner tests",
        "- do NOT call Bailian or any external LLM",
    ]

    try:
        if dataset_path.exists():
            backup = (
                dataset_path
                .with_name(
                    (
                        dataset_path.name
                        + ".before_capture_"
                        + datetime.now()
                        .strftime(
                            "%Y%m%d_%H%M%S"
                        )
                        + ".bak"
                    )
                )
            )

            backup.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            backup.write_bytes(
                dataset_path.read_bytes()
            )

            report.append(
                (
                    "Existing dataset backup: "
                    + str(
                        backup.relative_to(
                            root
                        )
                    )
                )
            )

        payload = collect_dataset()

        write_text(
            dataset_path,
            (
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ),
        )

        summary = validate_dataset(
            root=root,
            dataset_path=dataset_path,
        )

        if (
            summary[
                "supported_observation_count"
            ]
            == 0
        ):
            raise RuntimeError(
                "Dataset contains no currently supported real observations. "
                "Capture at least one of pod_state, memory_working_set, "
                "memory_limit, or restart_count."
            )

        tests = run_tests(
            root=root
        )

        if tests.returncode != 0:
            raise RuntimeError(
                "Focused Real Incident tests failed"
            )

        report.extend(
            [
                "",
                "=" * 120,
                "DATASET SUMMARY",
                "=" * 120,
                "",
                json.dumps(
                    summary,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                "",
                "=" * 120,
                "FOCUSED TEST RESULT",
                "=" * 120,
                "",
                (
                    tests.stdout.rstrip()
                    or "<EMPTY>"
                ),
                "",
                "STDERR",
                "-" * 120,
                (
                    tests.stderr.rstrip()
                    or "<EMPTY>"
                ),
                "",
                "=" * 120,
                "RESULT",
                "=" * 120,
                "",
                "PASSED",
                "",
                (
                    "Dataset: "
                    + str(
                        dataset_path.relative_to(
                            root
                        )
                    )
                ),
                "",
                "Ground Truth isolation: PASSED",
                "Human Timeline isolation: PASSED",
                "External LLM request: NOT SENT",
                "",
                "Next stage:",
                (
                    "Run this exact Dataset through "
                    "Historical LLM Investigation with --provider bailian."
                ),
            ]
        )

        write_text(
            after_path,
            "\n".join(
                report
            )
            + "\n",
        )

        print("")
        print(
            "=" * 72
        )
        print(
            "REAL INCIDENT #001 CAPTURE PASSED"
        )
        print(
            "=" * 72
        )
        print("")
        print(
            "Dataset:"
        )
        print(
            dataset_path
        )
        print("")
        print(
            "Upload BOTH files:"
        )
        print(
            dataset_path
        )
        print(
            after_path
        )
        print("")

        return 0

    except Exception as exc:
        error_lines = [
            "Real Incident #001 Capture v1 FAILED",
            (
                "GeneratedAt: "
                + datetime.now()
                .astimezone()
                .isoformat()
            ),
            "",
            "Exception:",
            (
                f"{type(exc).__name__}: "
                f"{exc}"
            ),
            "",
            "Traceback:",
            traceback.format_exc(),
            "",
            "PARTIAL REPORT",
            "=" * 120,
            *report,
        ]

        write_text(
            error_path,
            "\n".join(
                error_lines
            )
            + "\n",
        )

        print("")
        print(
            "=" * 72
        )
        print(
            "REAL INCIDENT #001 CAPTURE FAILED"
        )
        print(
            "=" * 72
        )
        print("")
        print(
            "Upload:"
        )
        print(
            error_path
        )
        print("")

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
