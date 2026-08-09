from __future__ import annotations

import ast
import hashlib
import subprocess
import traceback

from datetime import datetime
from pathlib import Path


OUTPUT_NAME = "feishu_live_runner_existing_targets_snapshot_v1.txt"
ERROR_NAME = "feishu_live_runner_existing_targets_snapshot_v1_error.txt"

TARGETS = (
    "services/agent_runtime/app/conversation/feishu_live_runtime.py",
    "services/agent_runtime/tests/test_feishu_live_runtime.py",
    "scripts/dev/run_feishu_live_channel_v1.py",
)

BASELINE_FILES = (
    "services/agent_runtime/app/conversation/feishu_channel_transport.py",
    "services/agent_runtime/app/conversation/feishu.py",
    "services/agent_runtime/app/runtime/runtime.py",
    "services/agent_runtime/app/main.py",
    "pyproject.toml",
    "uv.lock",
)


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate

    raise RuntimeError(
        "Repository root not found. Run this script inside ai-reliability-platform."
    )


def normalize(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def read_text(path: Path) -> str:
    return normalize(
        path.read_text(
            encoding="utf-8-sig",
            errors="replace",
        )
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def syntax_status(path: Path, text: str) -> str:
    if path.suffix.lower() != ".py":
        return "<NOT_APPLICABLE>"

    try:
        ast.parse(text)
        return "PASSED"
    except SyntaxError as exc:
        return (
            f"FAILED: {exc.msg} "
            f"(line={exc.lineno}, offset={exc.offset})"
        )


def append_file(
    report: list[str],
    *,
    root: Path,
    relative: str,
    required: bool,
    full_content: bool = True,
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
        report.append("<NOT PRESENT>")

        if required:
            raise RuntimeError(
                "Required baseline file is missing: "
                + relative
            )

        return

    text = read_text(path)

    report.extend(
        [
            "sha256_raw=" + sha256(path),
            "bytes=" + str(path.stat().st_size),
            "lines=" + str(len(text.splitlines())),
            "ast=" + syntax_status(path, text),
        ]
    )

    if full_content:
        report.extend(
            [
                "",
                "FULL CURRENT FILE",
                "-" * 120,
                text.rstrip(),
            ]
        )


def run_command(
    root: Path,
    command: list[str],
) -> tuple[int, str, str]:
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
        normalize(process.stdout),
        normalize(process.stderr),
    )


def append_command(
    report: list[str],
    *,
    root: Path,
    title: str,
    command: list[str],
) -> None:
    returncode, stdout, stderr = run_command(
        root,
        command,
    )

    report.extend(
        [
            "",
            "=" * 120,
            title,
            "=" * 120,
            "",
            "COMMAND:",
            " ".join(command),
            "",
            "ExitCode: " + str(returncode),
            "",
            "STDOUT",
            "-" * 120,
            stdout.rstrip() or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            stderr.rstrip() or "<EMPTY>",
        ]
    )


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    output = root / OUTPUT_NAME
    error = root / ERROR_NAME

    for target in (output, error):
        try:
            target.unlink()
        except FileNotFoundError:
            pass

    report = [
        "Feishu Live Runner Existing Targets Snapshot v1",
        (
            "GeneratedAt: "
            + datetime.now()
            .astimezone()
            .isoformat()
        ),
        "",
        "Purpose:",
        "- inspect pre-existing Live Runner v1 targets after installer refused overwrite",
        "- determine whether files are identical prior output, partial output, or divergent edits",
        "- capture complete current file contents before any reconcile/overwrite decision",
        "",
        "Safety:",
        "- read-only",
        "- no source modification",
        "- no credential read",
        "- no Feishu connection",
        "- no network send",
        "- no LLM/Kubernetes/Prometheus call",
        "- no Approval/Action/Verification mutation",
    ]

    try:
        report.extend(
            [
                "",
                "=" * 120,
                "EXISTING LIVE RUNNER TARGETS",
                "=" * 120,
            ]
        )

        for relative in TARGETS:
            append_file(
                report,
                root=root,
                relative=relative,
                required=False,
                full_content=True,
            )

        report.extend(
            [
                "",
                "=" * 120,
                "CURRENT BASELINE HASHES",
                "=" * 120,
            ]
        )

        for relative in BASELINE_FILES:
            append_file(
                report,
                root=root,
                relative=relative,
                required=True,
                full_content=False,
            )

        selected = [
            *TARGETS,
            *BASELINE_FILES,
        ]

        append_command(
            report,
            root=root,
            title="GIT STATUS FOR LIVE RUNNER TARGETS",
            command=[
                "git",
                "status",
                "--short",
                "--",
                *selected,
            ],
        )

        append_command(
            report,
            root=root,
            title="CURRENT DIFF FOR LIVE RUNNER TARGETS",
            command=[
                "git",
                "diff",
                "--",
                *selected,
            ],
        )

        append_command(
            report,
            root=root,
            title="UV LOCK CONSISTENCY",
            command=[
                "uv",
                "lock",
                "--check",
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
                "Upload only:",
                OUTPUT_NAME,
            ]
        )

        output.write_text(
            "\n".join(report) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print("FEISHU LIVE RUNNER EXISTING TARGETS SNAPSHOT V1 PASSED")
        print("=" * 72)
        print()
        print("No source file was modified.")
        print()
        print("Upload only:")
        print(output)

        return 0

    except Exception as exc:
        error.write_text(
            "\n".join(
                [
                    "Feishu Live Runner Existing Targets Snapshot v1 FAILED",
                    (
                        "GeneratedAt: "
                        + datetime.now()
                        .astimezone()
                        .isoformat()
                    ),
                    "",
                    type(exc).__name__
                    + ": "
                    + str(exc),
                    "",
                    traceback.format_exc(),
                    "",
                    "PARTIAL REPORT",
                    "=" * 120,
                    *report,
                    "",
                    "Upload only:",
                    ERROR_NAME,
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print("FEISHU LIVE RUNNER EXISTING TARGETS SNAPSHOT V1 FAILED")
        print("=" * 72)
        print()
        print("No source file was intentionally modified.")
        print()
        print("Upload only:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
