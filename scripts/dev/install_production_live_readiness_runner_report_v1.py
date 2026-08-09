from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "production-live-readiness-runner-report-v1"

AFTER_NAME = (
    "production_live_readiness_runner_report_v1_after.txt"
)

ERROR_NAME = (
    "production_live_readiness_runner_report_v1_error.txt"
)

EXPECTED_RAW_HASHES = {'services/agent_runtime/app/runtime/runtime.py': 'be3df28faaf881e45293ec4b5819c0a72cbce95e68ee8e51df4c83f31c318656', 'services/agent_runtime/app/investigation/live_readiness.py': '28ab39ae92371c490dcdfa19744483e72e474198a43a8118e4b32ebbf9ec8b37'}

RUNNER_SOURCE = 'from __future__ import annotations\n\nimport argparse\nimport asyncio\nimport json\nfrom datetime import (\n    UTC,\n    datetime,\n)\nfrom pathlib import Path\nfrom typing import Any\n\nfrom common.domain.event import (\n    Header,\n    Resource,\n    Signal,\n    StandardEvent,\n)\nfrom common.domain.event.enums import (\n    EventSource,\n    ResourceKind,\n    Severity,\n    SignalType,\n)\n\nfrom services.agent_runtime.app.investigation.live_readiness import (\n    ProductionReadinessLiveProbe,\n)\nfrom services.agent_runtime.app.model.context import (\n    AgentContext,\n)\nfrom services.agent_runtime.app.runtime.runtime import (\n    AgentRuntime,\n)\n\n\nREPORT_SCHEMA_VERSION = "v1"\n\nDEFAULT_OUTPUT = (\n    "production_live_readiness_report_v1.txt"\n)\n\n\nclass RunnerInputError(\n    ValueError\n):\n    pass\n\n\ndef find_repo_root(\n    start: Path,\n) -> Path:\n    for candidate in (\n        start,\n        *start.parents,\n    ):\n        if (\n            (candidate / "pyproject.toml").exists()\n            and (candidate / "services").exists()\n            and (candidate / "packages").exists()\n        ):\n            return candidate\n\n    raise RunnerInputError(\n        "repository_root_not_found"\n    )\n\n\ndef bounded_scope_text(\n    value: str,\n    *,\n    field: str,\n) -> str:\n    if (\n        not isinstance(\n            value,\n            str,\n        )\n        or not value\n        or value != value.strip()\n        or len(\n            value\n        )\n        > 256\n        or "\\x00" in value\n    ):\n        raise RunnerInputError(\n            field\n            + "_invalid"\n        )\n\n    return value\n\n\ndef validate_live_intent(\n    *,\n    acknowledgement: str | None,\n    reason: str | None,\n) -> None:\n    if (\n        acknowledgement\n        != ProductionReadinessLiveProbe\n        .ACKNOWLEDGEMENT\n    ):\n        raise RunnerInputError(\n            "live_acknowledgement_invalid"\n        )\n\n    if (\n        not isinstance(\n            reason,\n            str,\n        )\n        or not reason.strip()\n        or reason != reason.strip()\n        or len(\n            reason\n        )\n        > 512\n    ):\n        raise RunnerInputError(\n            "live_reason_invalid"\n        )\n\n\ndef output_path(\n    *,\n    root: Path,\n    value: str,\n) -> Path:\n    if (\n        not isinstance(\n            value,\n            str,\n        )\n        or not value\n        or value != value.strip()\n        or "\\x00" in value\n    ):\n        raise RunnerInputError(\n            "output_path_invalid"\n        )\n\n    relative = Path(\n        value\n    )\n\n    if (\n        relative.is_absolute()\n        or ".." in relative.parts\n    ):\n        raise RunnerInputError(\n            "output_path_invalid"\n        )\n\n    return (\n        root\n        / relative\n    )\n\n\ndef build_event(\n    *,\n    cluster: str,\n    namespace: str,\n    pod: str,\n) -> StandardEvent:\n    return StandardEvent(\n        header=Header(\n            source=EventSource.ALERTMANAGER,\n            occurred_at=datetime.now(\n                UTC\n            ),\n        ),\n        signal=Signal(\n            type=SignalType.ALERT,\n            name=(\n                "ProductionReadinessLiveProbe"\n            ),\n            severity=Severity.INFO,\n            message=(\n                "Explicit production read readiness proof"\n            ),\n        ),\n        resources=[\n            Resource(\n                kind=ResourceKind.POD,\n                name=pod,\n                namespace=namespace,\n                cluster=cluster,\n            )\n        ],\n    )\n\n\ndef base_report(\n    *,\n    mode: str,\n    cluster: str | None,\n    namespace: str | None,\n    pod: str | None,\n) -> dict[str, Any]:\n    return {\n        "schema_version": (\n            REPORT_SCHEMA_VERSION\n        ),\n        "generated_at": (\n            datetime.now(\n                UTC\n            ).isoformat()\n        ),\n        "read_only": True,\n        "decision_influence": False,\n        "automatic_execution": False,\n        "mode": mode,\n        "scope": {\n            "cluster": cluster,\n            "namespace": namespace,\n            "pod": pod,\n        },\n        "static_readiness": None,\n        "live_readiness": None,\n        "issues": [],\n    }\n\n\ndef write_report(\n    *,\n    path: Path,\n    report: dict[str, Any],\n) -> None:\n    path.parent.mkdir(\n        parents=True,\n        exist_ok=True,\n    )\n\n    path.write_text(\n        json.dumps(\n            report,\n            ensure_ascii=False,\n            indent=2,\n            sort_keys=True,\n        )\n        + "\\n",\n        encoding="utf-8",\n        newline="\\n",\n    )\n\n\ndef parser() -> argparse.ArgumentParser:\n    value = argparse.ArgumentParser(\n        description=(\n            "Explicit read-only production "\n            "multi-cluster readiness runner."\n        )\n    )\n\n    value.add_argument(\n        "--cluster",\n        required=True,\n        help="Exact Incident cluster identity.",\n    )\n\n    value.add_argument(\n        "--namespace",\n        required=True,\n        help="Exact Kubernetes namespace.",\n    )\n\n    value.add_argument(\n        "--pod",\n        required=True,\n        help="Exact Kubernetes Pod name.",\n    )\n\n    value.add_argument(\n        "--live",\n        action="store_true",\n        help=(\n            "Perform the two bounded live reads. "\n            "Without this flag the runner is static-only."\n        ),\n    )\n\n    value.add_argument(\n        "--acknowledgement",\n        default=None,\n        help=(\n            "Required only with --live."\n        ),\n    )\n\n    value.add_argument(\n        "--reason",\n        default=None,\n        help=(\n            "Required only with --live. "\n            "The reason is not written to the report."\n        ),\n    )\n\n    value.add_argument(\n        "--output",\n        default=DEFAULT_OUTPUT,\n        help=(\n            "Repository-relative sanitized report path."\n        ),\n    )\n\n    return value\n\n\nasync def run(\n    args: argparse.Namespace,\n) -> tuple[\n    dict[str, Any],\n    int,\n]:\n    cluster = bounded_scope_text(\n        args.cluster,\n        field="cluster",\n    )\n\n    namespace = bounded_scope_text(\n        args.namespace,\n        field="namespace",\n    )\n\n    pod = bounded_scope_text(\n        args.pod,\n        field="pod",\n    )\n\n    mode = (\n        "live"\n        if args.live\n        else "static_only"\n    )\n\n    report = base_report(\n        mode=mode,\n        cluster=cluster,\n        namespace=namespace,\n        pod=pod,\n    )\n\n    if args.live:\n        validate_live_intent(\n            acknowledgement=(\n                args.acknowledgement\n            ),\n            reason=args.reason,\n        )\n\n    event = build_event(\n        cluster=cluster,\n        namespace=namespace,\n        pod=pod,\n    )\n\n    try:\n        runtime = AgentRuntime()\n\n    except Exception:\n        report[\n            "issues"\n        ] = [\n            "runtime_initialization_failed"\n        ]\n\n        return (\n            report,\n            3,\n        )\n\n    gate = getattr(\n        runtime,\n        "production_multi_cluster_readiness",\n        None,\n    )\n\n    if gate is None:\n        report[\n            "issues"\n        ] = [\n            "static_readiness_unavailable"\n        ]\n\n        return (\n            report,\n            2,\n        )\n\n    try:\n        static_report = gate.evaluate_event(\n            event\n        )\n\n    except Exception:\n        report[\n            "issues"\n        ] = [\n            "static_readiness_evaluation_failed"\n        ]\n\n        return (\n            report,\n            2,\n        )\n\n    report[\n        "static_readiness"\n    ] = static_report.snapshot()\n\n    if not static_report.ready:\n        report[\n            "issues"\n        ] = list(\n            static_report.issues\n        )\n\n        return (\n            report,\n            2,\n        )\n\n    if not args.live:\n        return (\n            report,\n            0,\n        )\n\n    context = AgentContext(\n        event=event,\n        memory=runtime.memory,\n        tools=runtime.tools,\n        skills=runtime.skills,\n        metadata={},\n    )\n\n    try:\n        live_snapshot = await (\n            runtime\n            .run_production_multi_cluster_live_readiness(\n                context,\n                acknowledgement=(\n                    args.acknowledgement\n                ),\n                reason=args.reason,\n            )\n        )\n\n    except Exception:\n        report[\n            "issues"\n        ] = [\n            "live_readiness_execution_failed"\n        ]\n\n        return (\n            report,\n            4,\n        )\n\n    report[\n        "live_readiness"\n    ] = dict(\n        live_snapshot\n    )\n\n    if (\n        live_snapshot.get(\n            "ready"\n        )\n        is not True\n    ):\n        report[\n            "issues"\n        ] = list(\n            live_snapshot.get(\n                "issues",\n                [\n                    "live_readiness_not_ready"\n                ],\n            )\n        )\n\n        return (\n            report,\n            4,\n        )\n\n    return (\n        report,\n        0,\n    )\n\n\ndef main() -> int:\n    args = parser().parse_args()\n\n    try:\n        root = find_repo_root(\n            Path.cwd().resolve()\n        )\n\n        report_path = output_path(\n            root=root,\n            value=args.output,\n        )\n\n        try:\n            report, exit_code = asyncio.run(\n                run(\n                    args\n                )\n            )\n\n        except RunnerInputError as exc:\n            report = base_report(\n                mode=(\n                    "live"\n                    if args.live\n                    else "static_only"\n                ),\n                cluster=(\n                    args.cluster\n                    if isinstance(\n                        args.cluster,\n                        str,\n                    )\n                    else None\n                ),\n                namespace=(\n                    args.namespace\n                    if isinstance(\n                        args.namespace,\n                        str,\n                    )\n                    else None\n                ),\n                pod=(\n                    args.pod\n                    if isinstance(\n                        args.pod,\n                        str,\n                    )\n                    else None\n                ),\n            )\n\n            report[\n                "issues"\n            ] = [\n                str(\n                    exc\n                )\n            ]\n\n            exit_code = 5\n\n        except Exception:\n            report = base_report(\n                mode=(\n                    "live"\n                    if args.live\n                    else "static_only"\n                ),\n                cluster=None,\n                namespace=None,\n                pod=None,\n            )\n\n            report[\n                "issues"\n            ] = [\n                "runner_internal_failure"\n            ]\n\n            exit_code = 6\n\n        write_report(\n            path=report_path,\n            report=report,\n        )\n\n        print(\n            "=" * 72\n        )\n        print(\n            "PRODUCTION LIVE READINESS RUNNER V1"\n        )\n        print(\n            "=" * 72\n        )\n        print(\n            f"mode={report[\'mode\']}"\n        )\n        print(\n            "ready="\n            + str(\n                (\n                    report.get(\n                        "live_readiness"\n                    )\n                    or report.get(\n                        "static_readiness"\n                    )\n                    or {}\n                ).get(\n                    "ready",\n                    False,\n                )\n            )\n        )\n        print(\n            "report="\n            + str(\n                report_path\n            )\n        )\n\n        return exit_code\n\n    except RunnerInputError:\n        print(\n            "PRODUCTION LIVE READINESS RUNNER V1 INPUT ERROR"\n        )\n\n        return 5\n\n\nif __name__ == "__main__":\n    raise SystemExit(\n        main()\n    )\n'


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


def raw_sha256(
    path: Path,
) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def write_text(
    path: Path,
    value: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    normalized = (
        value
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )

    if not normalized.endswith(
        "\n"
    ):
        normalized += "\n"

    path.write_text(
        normalized,
        encoding="utf-8",
        newline="\n",
    )


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

    actual = raw_sha256(
        path
    )

    expected = EXPECTED_RAW_HASHES[
        relative
    ]

    if actual != expected:
        raise RuntimeError(
            (
                f"{relative} changed after the installed Live Probe baseline. "
                f"expected_raw_sha256={expected} actual_raw_sha256={actual}. "
                "Refusing stale Production Live Readiness Runner installation."
            )
        )


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after = root / AFTER_NAME
    error = root / ERROR_NAME

    runner = (
        root
        / "scripts"
        / "dev"
        / "run_production_live_readiness_v1.py"
    )

    preexisting = runner.exists()
    backup = None

    report = [
        "Production Live Readiness Runner / Report v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Safety:",
        "- default execution is static-only and sends zero live backend requests",
        "- --live is required for real backend reads",
        "- --live additionally requires the exact Live Probe acknowledgement and a non-empty reason",
        "- the reason and acknowledgement are never written to the report",
        "- one repository-level sanitized report file is written per execution",
        "",
        "Runner scope:",
        "- --cluster is required",
        "- --namespace is required",
        "- --pod is required",
        "- no cluster, namespace, or pod value is hard-coded",
        "",
        "Static-only:",
        "- constructs Runtime from current configured read connections",
        "- evaluates ProductionMultiClusterReadinessGate only",
        "- no Kubernetes/Prometheus Tool call",
        "",
        "Live:",
        "- delegates to AgentRuntime.run_production_multi_cluster_live_readiness(...)",
        "- Live Probe itself enforces static readiness, acknowledgement, reason, bounded timeout, exact cluster, and read-only result shape",
        "",
        "Report:",
        "- default path: production_live_readiness_report_v1.txt",
        "- contains static/live readiness snapshots and sanitized issue codes",
        "- contains no endpoint URL, credential, acknowledgement, reason, backend payload, or raw exception text",
        "",
        "Installer performs compile/help/source checks only and sends no network request.",
    ]

    try:
        section(
            report,
            "CURRENT RAW HASH PREFLIGHT",
        )

        for relative in EXPECTED_RAW_HASHES:
            verify_hash(
                root=root,
                relative=relative,
            )

            report.append(
                relative
                + "="
                + EXPECTED_RAW_HASHES[
                    relative
                ]
            )

        if preexisting:
            backup = backup_file(
                runner
            )

            report.append(
                "runner_backup="
                + str(
                    backup.relative_to(
                        root
                    )
                )
            )

        write_text(
            runner,
            RUNNER_SOURCE,
        )

        syntax = run_command(
            root=root,
            name="Runner Python syntax",
            command=[
                "uv",
                "run",
                "python",
                "-m",
                "py_compile",
                str(
                    runner.relative_to(
                        root
                    )
                ),
            ],
        )

        add_command(
            report,
            syntax,
        )

        if syntax.returncode != 0:
            raise RuntimeError(
                "Production Live Readiness Runner syntax failed"
            )

        help_check = run_command(
            root=root,
            name="Runner help / zero-network CLI check",
            command=[
                "uv",
                "run",
                "python",
                str(
                    runner.relative_to(
                        root
                    )
                ),
                "--help",
            ],
        )

        add_command(
            report,
            help_check,
        )

        if help_check.returncode != 0:
            raise RuntimeError(
                "Production Live Readiness Runner help check failed"
            )

        architecture = run_command(
            root=root,
            name="Runner architecture / safety preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "p=Path(r'scripts/dev/run_production_live_readiness_v1.py').read_text(encoding='utf-8'); "
                    "print('static_default='+str('action=\"store_true\"' in p)); "
                    "print('explicit_runtime_method='+str('run_production_multi_cluster_live_readiness' in p)); "
                    "print('ack_constant='+str('ACKNOWLEDGEMENT' in p)); "
                    "print('single_report='+str('production_live_readiness_report_v1.txt' in p)); "
                    "print('hardcoded_payment='+str('payment-api' in p or 'production-a' in p)); "
                    "assert 'action=\"store_true\"' in p; "
                    "assert 'run_production_multi_cluster_live_readiness' in p; "
                    "assert 'ACKNOWLEDGEMENT' in p; "
                    "assert 'production_live_readiness_report_v1.txt' in p; "
                    "assert 'payment-api' not in p; "
                    "assert 'production-a' not in p"
                ),
            ],
        )

        add_command(
            report,
            architecture,
        )

        if architecture.returncode != 0:
            raise RuntimeError(
                "Production Live Readiness Runner architecture preflight failed"
            )

        authority = run_command(
            root=root,
            name="Runner write-authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "p=Path(r'scripts/dev/run_production_live_readiness_v1.py').read_text(encoding='utf-8'); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService','KubernetesProductionExecutor','.post(','.patch(','.put(','.delete('] if x in p]; "
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
                "Production Live Readiness Runner authority boundary failed"
            )

        status = run_command(
            root=root,
            name="Git status",
            command=[
                "git",
                "status",
                "--short",
                "--",
                str(
                    runner.relative_to(
                        root
                    )
                ),
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
                "Production Live Readiness Runner / Report v1 is installed.",
                "",
                "Default safe command shape:",
                "uv run python scripts/dev/run_production_live_readiness_v1.py --cluster <cluster> --namespace <namespace> --pod <pod>",
                "",
                "Explicit live command shape:",
                "uv run python scripts/dev/run_production_live_readiness_v1.py --cluster <cluster> --namespace <namespace> --pod <pod> --live --acknowledgement I_ACKNOWLEDGE_PRODUCTION_READINESS_LIVE_READS --reason \"operator pre-production connectivity proof\"",
                "",
                "Do not run the live command until the intended real read connections and credentials are configured.",
            ]
        )

        write_text(
            after,
            "\n".join(
                report
            )
            + "\n",
        )

        print(
            "=" * 72
        )
        print(
            "PRODUCTION LIVE READINESS RUNNER / REPORT V1 PASSED"
        )
        print(
            "=" * 72
        )
        print()
        print(
            "Installer sent no Kubernetes/Prometheus/LLM request."
        )
        print()
        print(
            "Upload only:"
        )
        print(
            after
        )

        return 0

    except Exception as exc:
        rollback = []

        if backup is not None:
            try:
                shutil.copy2(
                    backup,
                    runner,
                )

                rollback.append(
                    "RESTORED "
                    + str(
                        runner.relative_to(
                            root
                        )
                    )
                )

            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK FAILED: "
                    + (
                        f"{type(rollback_exc).__name__}: "
                        f"{rollback_exc}"
                    )
                )

        elif (
            not preexisting
            and runner.exists()
        ):
            try:
                runner.unlink()

                rollback.append(
                    "REMOVED newly-created "
                    + str(
                        runner.relative_to(
                            root
                        )
                    )
                )

            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK REMOVE FAILED: "
                    + (
                        f"{type(rollback_exc).__name__}: "
                        f"{rollback_exc}"
                    )
                )

        write_text(
            error,
            "\n".join(
                [
                    "Production Live Readiness Runner / Report v1 FAILED",
                    (
                        "GeneratedAt: "
                        + datetime.now().astimezone().isoformat()
                    ),
                    "",
                    (
                        f"{type(exc).__name__}: {exc}"
                    ),
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

        print(
            "=" * 72
        )
        print(
            "PRODUCTION LIVE READINESS RUNNER / REPORT V1 FAILED"
        )
        print(
            "=" * 72
        )
        print()
        print(
            "Upload only:"
        )
        print(
            error
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
