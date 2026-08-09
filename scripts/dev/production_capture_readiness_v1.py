from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


AFTER_NAME = "production_capture_readiness_v1_after.txt"
ERROR_NAME = "production_capture_readiness_v1_error.txt"


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate

    raise RuntimeError(
        "Repository root not found. Run from inside ai-reliability-platform."
    )


def install_import_paths(root: Path) -> None:
    for candidate in reversed(
        [
            root,
            root / "packages" / "common" / "src",
        ]
    ):
        value = str(candidate)

        if value not in sys.path:
            sys.path.insert(
                0,
                value,
            )


def write_text(path: Path, text: str) -> None:
    path.write_text(
        text.replace("\r\n", "\n").replace("\r", "\n"),
        encoding="utf-8",
        newline="\n",
    )


def section(lines: list[str], title: str) -> None:
    lines.extend(
        [
            "",
            "=" * 120,
            title,
            "=" * 120,
            "",
        ]
    )


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
            " ".join(result.command),
            "",
            f"ExitCode: {result.returncode}",
            "",
            "STDOUT",
            "-" * 120,
            result.stdout.rstrip() or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            result.stderr.rstrip() or "<EMPTY>",
        ]
    )


def bool_text(value: Any) -> str:
    return "True" if bool(value) else "False"


def environment_presence() -> dict[str, bool]:
    names = (
        "KUBERNETES_API_URL",
        "KUBERNETES_BEARER_TOKEN",
        "KUBERNETES_TOKEN_FILE",
        "KUBERNETES_CA_FILE",
        "KUBERNETES_CLUSTER_NAME",
        "KUBERNETES_ALLOW_DRY_RUN_FALLBACK",
        "PROMETHEUS_URL",
        "PROMETHEUS_BEARER_TOKEN",
        "PROMETHEUS_ALLOW_MOCK_FALLBACK",
        "AGENT_INCIDENT_EVIDENCE_RECORDER_ENABLED",
        "AGENT_INCIDENT_EVIDENCE_RECORDER_ACKNOWLEDGEMENT",
        "AGENT_INCIDENT_EVIDENCE_RECORDER_OUTPUT_DIR",
    )

    return {
        name: bool(
            os.getenv(
                name,
                "",
            ).strip()
        )
        for name in names
    }


def inspect_runtime_tools(
    root: Path,
) -> dict[str, Any]:
    install_import_paths(
        root
    )

    from services.agent_runtime.app.incident_evidence.settings import (
        IncidentEvidenceRecorderSettings,
    )
    from services.agent_runtime.app.tools.factory import (
        create_tool_manager,
    )

    settings = (
        IncidentEvidenceRecorderSettings
        .from_environment()
    )

    manager = create_tool_manager()

    kubernetes = manager.registry.get(
        "kubernetes"
    )

    prometheus = manager.registry.get(
        "prometheus"
    )

    kubernetes_token_present = bool(
        getattr(
            kubernetes,
            "bearer_token",
            None,
        )
    )

    kubernetes_token_file = getattr(
        kubernetes,
        "token_file",
        None,
    )

    kubernetes_token_file_present = bool(
        kubernetes_token_file
        and Path(
            kubernetes_token_file
        ).exists()
    )

    kubernetes_configured = bool(
        getattr(
            kubernetes,
            "api_url",
            None,
        )
    )

    prometheus_configured = bool(
        getattr(
            prometheus,
            "base_url",
            None,
        )
    )

    kubernetes_fallback_disabled = (
        getattr(
            kubernetes,
            "allow_dry_run_fallback",
            True,
        )
        is False
    )

    prometheus_fallback_disabled = (
        getattr(
            prometheus,
            "allow_mock_fallback",
            True,
        )
        is False
    )

    recorder_disabled = (
        settings.enabled
        is False
    )

    checks = {
        "recorder_currently_disabled": recorder_disabled,
        "kubernetes_live_endpoint_configured": kubernetes_configured,
        "kubernetes_dry_run_fallback_disabled": kubernetes_fallback_disabled,
        "kubernetes_auth_material_present": (
            kubernetes_token_present
            or kubernetes_token_file_present
        ),
        "prometheus_live_endpoint_configured": prometheus_configured,
        "prometheus_mock_fallback_disabled": prometheus_fallback_disabled,
    }

    return {
        "checks": checks,
        "recorder": {
            "enabled": settings.enabled,
            "output_dir": settings.output_dir,
        },
        "kubernetes": {
            "api_url_present": kubernetes_configured,
            "in_cluster": bool(
                getattr(
                    kubernetes,
                    "in_cluster",
                    False,
                )
            ),
            "cluster_name_present": bool(
                getattr(
                    kubernetes,
                    "cluster_name",
                    None,
                )
            ),
            "verify_tls": getattr(
                kubernetes,
                "verify_tls",
                None,
            ),
            "bearer_token_present": kubernetes_token_present,
            "token_file_present": kubernetes_token_file_present,
            "ca_file_present": bool(
                getattr(
                    kubernetes,
                    "ca_file",
                    None,
                )
            ),
            "allow_dry_run_fallback": getattr(
                kubernetes,
                "allow_dry_run_fallback",
                None,
            ),
        },
        "prometheus": {
            "base_url_present": prometheus_configured,
            "verify_tls": getattr(
                prometheus,
                "verify_tls",
                None,
            ),
            "bearer_token_present": bool(
                getattr(
                    prometheus,
                    "bearer_token",
                    None,
                )
            ),
            "allow_mock_fallback": getattr(
                prometheus,
                "allow_mock_fallback",
                None,
            ),
        },
    }


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    install_import_paths(
        root
    )

    after = root / AFTER_NAME
    error = root / ERROR_NAME

    for path in (
        after,
        error,
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    report = [
        "Production Incident Capture Readiness Preflight v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Purpose:",
        "- verify Recorder wiring remains safe",
        "- inspect live Kubernetes/Prometheus configuration without calling them",
        "- require production fallbacks to be disabled before live capture",
        "- keep Recorder disabled during readiness inspection",
        "- run focused no-network tests",
        "",
        "This script sends ZERO Kubernetes/Prometheus/LLM network requests.",
        "Credential values are never printed.",
    ]

    try:
        section(
            report,
            "ENVIRONMENT PRESENCE ONLY",
        )

        for name, present in environment_presence().items():
            report.append(
                f"{name}_PRESENT={present}"
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
                "services/agent_runtime/app/runtime/runtime.py",
                "services/agent_runtime/app/incident_evidence/recorder.py",
                "services/agent_runtime/app/incident_evidence/settings.py",
                "services/agent_runtime/app/tools/kubernetes/tool.py",
                "services/agent_runtime/app/tools/prometheus/tool.py",
            ],
        )

        add_command(
            report,
            syntax,
        )

        if syntax.returncode != 0:
            raise RuntimeError(
                "Python syntax verification failed"
            )

        tests = run_command(
            root=root,
            name="Focused production read-only capture tests",
            command=[
                "uv",
                "run",
                "pytest",
                "services/agent_runtime/tests/test_kubernetes_tool.py",
                "services/agent_runtime/tests/test_prometheus_tool.py",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_production_tool_contract.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_production_incident_evidence_recorder.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_runtime_incident_evidence_recorder_wiring.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            tests,
        )

        if tests.returncode != 0:
            raise RuntimeError(
                "Focused production read-only capture tests failed"
            )

        readiness = inspect_runtime_tools(
            root
        )

        section(
            report,
            "RUNTIME TOOL READINESS",
        )

        report.append(
            json.dumps(
                readiness,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )

        checks = readiness[
            "checks"
        ]

        missing = [
            name
            for name, passed
            in checks.items()
            if not passed
        ]

        section(
            report,
            "READINESS RESULT",
        )

        if missing:
            report.append(
                "READINESS=NOT_READY"
            )
            report.append("")
            report.append(
                "Missing/unsafe conditions:"
            )

            for item in missing:
                report.append(
                    f"- {item}"
                )

            report.extend(
                [
                    "",
                    "No production request was attempted.",
                    "Do not enable the Recorder yet.",
                ]
            )

        else:
            report.extend(
                [
                    "READINESS=READY_FOR_LIVE_CAPTURE_SMOKE",
                    "",
                    "All local configuration gates are satisfied.",
                    "Recorder is still disabled.",
                    "Next stage may perform one explicit bounded read-only live smoke.",
                ]
            )

        section(
            report,
            "EXPECTED PRODUCTION CONFIG CONTRACT",
        )

        report.extend(
            [
                "Kubernetes:",
                "- KUBERNETES_API_URL, or valid in-cluster discovery",
                "- KUBERNETES_BEARER_TOKEN or readable KUBERNETES_TOKEN_FILE",
                "- KUBERNETES_ALLOW_DRY_RUN_FALLBACK=false",
                "- KUBERNETES_VERIFY_TLS=true (recommended)",
                "- KUBERNETES_CA_FILE when required",
                "- KUBERNETES_CLUSTER_NAME recommended",
                "",
                "Prometheus:",
                "- PROMETHEUS_URL",
                "- PROMETHEUS_BEARER_TOKEN when required by your deployment",
                "- PROMETHEUS_ALLOW_MOCK_FALLBACK=false",
                "- PROMETHEUS_VERIFY_TLS=true (recommended)",
                "",
                "Recorder:",
                "- keep AGENT_INCIDENT_EVIDENCE_RECORDER_ENABLED=false",
                "  until the explicit live capture smoke stage",
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
            "PRODUCTION CAPTURE READINESS PREFLIGHT COMPLETED"
        )
        print("=" * 72)
        print("")
        print(
            "No production network request was sent."
        )
        print(
            "Readiness: "
            + (
                "READY_FOR_LIVE_CAPTURE_SMOKE"
                if not missing
                else "NOT_READY"
            )
        )
        print("")
        print("Upload:")
        print(after)

        return 0

    except Exception as exc:
        error_lines = [
            "Production Incident Capture Readiness Preflight v1 FAILED",
            f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
            "",
            "Exception:",
            f"{type(exc).__name__}: {exc}",
            "",
            "Traceback:",
            traceback.format_exc(),
            "",
            "PARTIAL REPORT",
            "=" * 120,
            *report,
        ]

        write_text(
            error,
            "\n".join(
                error_lines
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "PRODUCTION CAPTURE READINESS PREFLIGHT FAILED"
        )
        print("=" * 72)
        print("")
        print("Upload:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
