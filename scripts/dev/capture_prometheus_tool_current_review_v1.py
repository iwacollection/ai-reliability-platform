from __future__ import annotations

import hashlib
import subprocess
import traceback
from datetime import datetime
from pathlib import Path


OUTPUT_NAME = (
    "prometheus_tool_current_review_snapshot_v1.txt"
)

ERROR_NAME = (
    "prometheus_tool_current_review_snapshot_v1_error.txt"
)

TARGETS = (
    "services/agent_runtime/app/tools/prometheus/tool.py",
    "services/agent_runtime/tests/test_prometheus_tool.py",
)

RELATED_OPTIONAL = (
    "services/agent_runtime/app/tools/prometheus/router.py",
    "services/agent_runtime/app/tools/prometheus/connection_factory.py",
    "services/agent_runtime/tests/test_multi_cluster_prometheus_router.py",
    "services/agent_runtime/tests/test_multi_cluster_prometheus_connection_config.py",
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
        "Repository root not found."
    )


def sha256_bytes(
    data: bytes,
) -> str:
    return hashlib.sha256(
        data
    ).hexdigest()


def sha256_normalized_text(
    text: str,
) -> str:
    normalized = (
        text
        .replace(
            "\r\n",
            "\n",
        )
        .replace(
            "\r",
            "\n",
        )
    )

    return hashlib.sha256(
        normalized.encode(
            "utf-8"
        )
    ).hexdigest()


def run(
    *,
    root: Path,
    command: list[str],
) -> tuple[
    int,
    str,
    str,
]:
    process = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    return (
        process.returncode,
        process.stdout,
        process.stderr,
    )


def add_command(
    report: list[str],
    *,
    root: Path,
    title: str,
    command: list[str],
) -> None:
    code, stdout, stderr = run(
        root=root,
        command=command,
    )

    report.extend(
        [
            "",
            "=" * 120,
            title,
            "=" * 120,
            "",
            "COMMAND",
            " ".join(
                command
            ),
            "",
            f"ExitCode: {code}",
            "",
            "STDOUT",
            "-" * 120,
            stdout.rstrip()
            or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            stderr.rstrip()
            or "<EMPTY>",
        ]
    )


def add_file(
    report: list[str],
    *,
    root: Path,
    relative: str,
    required: bool,
) -> None:
    path = root / relative

    report.extend(
        [
            "",
            "=" * 120,
            relative,
            "=" * 120,
            "",
        ]
    )

    if not path.exists():
        if required:
            raise RuntimeError(
                f"Required file missing: {relative}"
            )

        report.append(
            "<NOT PRESENT>"
        )

        return

    raw = path.read_bytes()

    text = raw.decode(
        "utf-8-sig",
        errors="strict",
    )

    report.extend(
        [
            (
                "sha256_raw="
                + sha256_bytes(
                    raw
                )
            ),
            (
                "sha256_normalized="
                + sha256_normalized_text(
                    text
                )
            ),
            (
                "lines="
                + str(
                    len(
                        text.splitlines()
                    )
                )
            ),
            "",
            "FULL CURRENT FILE",
            "-" * 120,
            text.rstrip(),
        ]
    )


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    output = root / OUTPUT_NAME
    error = root / ERROR_NAME

    for item in (
        output,
        error,
    ):
        try:
            item.unlink()
        except FileNotFoundError:
            pass

    report = [
        "Prometheus Tool Current Review Snapshot v1",
        (
            "GeneratedAt: "
            + datetime.now().astimezone().isoformat()
        ),
        "",
        "Purpose:",
        "- capture the exact current PrometheusTool after stale-hash rejection",
        "- capture the current PrometheusTool tests before CA/TLS connection-config work",
        "- show git diff/status without modifying source code",
        "- keep Router/Connection Config related files in the same single upload when present",
        "",
        "Expected stale-hash observation from the failed installer:",
        "- reviewed old normalized SHA256: d850d949df0450bde6ad9fa7813df6a2187f297d3aa323e27726104946ead100",
        "- repository reported current normalized SHA256: d7ebd8eafbaf1727a34608bb330d6e5633d4c41dbaee2c809b6e3fa9e458da28",
        "",
        "This script is read-only.",
    ]

    try:
        for relative in TARGETS:
            add_file(
                report,
                root=root,
                relative=relative,
                required=True,
            )

        for relative in RELATED_OPTIONAL:
            add_file(
                report,
                root=root,
                relative=relative,
                required=False,
            )

        add_command(
            report,
            root=root,
            title="GIT DIFF: Prometheus Tool",
            command=[
                "git",
                "diff",
                "--",
                (
                    "services/agent_runtime/app/"
                    "tools/prometheus/tool.py"
                ),
            ],
        )

        add_command(
            report,
            root=root,
            title="GIT DIFF: Prometheus Tool Test",
            command=[
                "git",
                "diff",
                "--",
                (
                    "services/agent_runtime/tests/"
                    "test_prometheus_tool.py"
                ),
            ],
        )

        add_command(
            report,
            root=root,
            title="GIT STATUS: Prometheus Related",
            command=[
                "git",
                "status",
                "--short",
                "--",
                (
                    "services/agent_runtime/app/"
                    "tools/prometheus"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_prometheus_tool.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_prometheus_router.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_prometheus_connection_config.py"
                ),
            ],
        )

        report.extend(
            [
                "",
                "=" * 120,
                "RESULT",
                "=" * 120,
                "",
                "PASSED",
                "",
                "No source file was modified.",
                "",
                "Upload only this snapshot.",
            ]
        )

        output.write_text(
            "\n".join(
                report
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print(
            "=" * 72
        )
        print(
            "PROMETHEUS TOOL CURRENT REVIEW SNAPSHOT V1 PASSED"
        )
        print(
            "=" * 72
        )
        print()
        print(
            "No source file was modified."
        )
        print()
        print(
            "Upload only:"
        )
        print(
            output
        )

        return 0

    except Exception as exc:
        error.write_text(
            "\n".join(
                [
                    "Prometheus Tool Current Review Snapshot v1 FAILED",
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
                    "PARTIAL REPORT",
                    "=" * 120,
                    *report,
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print(
            "=" * 72
        )
        print(
            "PROMETHEUS TOOL CURRENT REVIEW SNAPSHOT V1 FAILED"
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
