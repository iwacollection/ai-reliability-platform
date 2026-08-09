from __future__ import annotations

import ast
import hashlib
import importlib.util
import subprocess
import traceback

from datetime import datetime
from pathlib import Path


OUTPUT_NAME = "feishu_official_channel_transport_current_snapshot_v1.txt"
ERROR_NAME = "feishu_official_channel_transport_current_snapshot_v1_error.txt"

REQUIRED_FILES = (
    "pyproject.toml",
    "services/agent_runtime/app/conversation/feishu.py",
    "services/agent_runtime/app/conversation/chatops.py",
    "services/agent_runtime/app/conversation/identity.py",
    "services/agent_runtime/app/conversation/write_bridge.py",
    "services/agent_runtime/app/conversation/orchestrator.py",
    "services/agent_runtime/app/runtime/runtime.py",
    "services/agent_runtime/tests/test_feishu_chatops_adapter.py",
)

OPTIONAL_FILES = (
    "uv.lock",
    "services/agent_runtime/app/main.py",
    "services/agent_runtime/app/conversation/feishu_transport.py",
    "services/agent_runtime/app/conversation/feishu_runtime.py",
    "services/agent_runtime/tests/test_feishu_channel_transport.py",
    "services/agent_runtime/tests/test_feishu_long_connection_transport.py",
    "scripts/dev/run_feishu_chatops_long_connection_v1.py",
)

DISCOVERY_ROOTS = (
    "services/agent_runtime/app",
    "services/agent_runtime/tests",
    "scripts/dev",
)

DISCOVERY_TOKENS = (
    "lark_oapi",
    "lark_channel",
    "FeishuChannel",
    "FeishuChatOpsAdapter",
    "FeishuLongConnectionTrustBoundary",
    "FeishuActorAttestationRegistry",
    "FeishuChatOpsActorVerifier",
    "im.message.receive_v1",
    "card.action.trigger",
    "LARK_APP_ID",
    "LARK_APP_SECRET",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
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
        "Repository root not found. Run this script from inside ai-reliability-platform."
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


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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


def rel(path: Path, root: Path) -> str:
    return str(
        path.relative_to(root)
    ).replace("\\", "/")


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
                "Required current file is missing: "
                + relative
            )
        return

    raw = path.read_bytes()
    text = read_text(path)

    report.extend(
        [
            "sha256_raw="
            + sha256_bytes(raw),
            "bytes="
            + str(len(raw)),
            "lines="
            + str(len(text.splitlines())),
            "ast="
            + syntax_status(path, text),
            "",
            "FEISHU / CHANNEL / CREDENTIAL-REFERENCE HITS",
            "-" * 120,
        ]
    )

    hits: list[str] = []

    for number, line in enumerate(
        text.splitlines(),
        start=1,
    ):
        matched = [
            token
            for token in DISCOVERY_TOKENS
            if token.lower() in line.lower()
        ]

        if matched:
            hits.append(
                "L"
                + str(number)
                + ": tokens="
                + repr(matched)
                + " text="
                + line
            )

    report.extend(
        hits
        if hits
        else ["<NONE>"]
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
    else:
        report.extend(
            [
                "",
                "FULL CONTENT OMITTED",
                "-" * 120,
                (
                    "Large generated dependency lockfile. "
                    "SHA256 and relevant matching lines are captured above."
                ),
            ]
        )


def discover_files(
    root: Path,
) -> list[Path]:
    found: set[Path] = set()

    for relative in DISCOVERY_ROOTS:
        base = root / relative

        if not base.exists():
            continue

        for path in base.rglob("*.py"):
            if not path.is_file():
                continue

            try:
                text = read_text(path)
            except Exception:
                continue

            name = path.name.lower()

            if (
                "feishu" in name
                or "lark" in name
                or any(
                    token.lower()
                    in text.lower()
                    for token
                    in DISCOVERY_TOKENS
                )
            ):
                found.add(path)

    return sorted(
        found,
        key=lambda path: rel(
            path,
            root,
        ).lower(),
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
    title: str,
    command: list[str],
) -> None:
    returncode, stdout, stderr = run_command(
        root=ROOT,
        command=command,
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
            "ExitCode: "
            + str(returncode),
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


ROOT: Path


def main() -> int:
    global ROOT

    ROOT = find_repo_root(
        Path.cwd().resolve()
    )

    output = (
        ROOT
        / OUTPUT_NAME
    )
    error = (
        ROOT
        / ERROR_NAME
    )

    for target in (
        output,
        error,
    ):
        try:
            target.unlink()
        except FileNotFoundError:
            pass

    report = [
        "Feishu Official Channel Transport Current Snapshot v1",
        (
            "GeneratedAt: "
            + datetime.now()
            .astimezone()
            .isoformat()
        ),
        "",
        "RepositoryRoot:",
        str(ROOT),
        "",
        "Purpose:",
        "- capture the exact post-Feishu-Core repository state",
        "- inspect current dependency and entrypoint boundaries before any real Channel transport",
        "- detect both current lark_channel and legacy lark_oapi availability",
        "- capture the full Feishu Core implementation and its tests",
        "- bind the next installer to exact SHA256 values",
        "",
        "Current official SDK note:",
        "- lark_oapi.channel is now treated as a legacy Channel path",
        "- current Channel work should evaluate lark-channel-sdk / lark_channel first",
        "- this snapshot installs neither package",
        "",
        "Safety:",
        "- read-only repository inspection",
        "- no dependency installation",
        "- no Feishu connection",
        "- no outbound message",
        "- no app secret/token read",
        "- no environment-variable value dump",
        "- no LLM/Kubernetes/Prometheus call",
        "- no Approval/Action/Verification mutation",
    ]

    try:
        report.extend(
            [
                "",
                "=" * 120,
                "REQUIRED CURRENT FILES",
                "=" * 120,
            ]
        )

        for relative in REQUIRED_FILES:
            append_file(
                report,
                root=ROOT,
                relative=relative,
                required=True,
                full_content=True,
            )

        report.extend(
            [
                "",
                "=" * 120,
                "OPTIONAL TRANSPORT / ENTRYPOINT FILES",
                "=" * 120,
            ]
        )

        for relative in OPTIONAL_FILES:
            append_file(
                report,
                root=ROOT,
                relative=relative,
                required=False,
                full_content=(
                    relative
                    != "uv.lock"
                ),
            )

        discovered = (
            discover_files(
                ROOT
            )
        )

        report.extend(
            [
                "",
                "=" * 120,
                "DISCOVERED FEISHU / CHANNEL FILES",
                "=" * 120,
                "",
            ]
        )

        if discovered:
            report.extend(
                rel(
                    path,
                    ROOT,
                )
                for path
                in discovered
            )
        else:
            report.append(
                "<NONE>"
            )

        lark_oapi_available = (
            importlib.util.find_spec(
                "lark_oapi"
            )
            is not None
        )

        lark_channel_available = (
            importlib.util.find_spec(
                "lark_channel"
            )
            is not None
        )

        report.extend(
            [
                "",
                "=" * 120,
                "PYTHON PACKAGE BASELINE",
                "=" * 120,
                "",
                (
                    "lark_oapi_importable="
                    + str(
                        lark_oapi_available
                    )
                ),
                (
                    "lark_channel_importable="
                    + str(
                        lark_channel_available
                    )
                ),
            ]
        )

        dependency_query = [
            "uv",
            "tree",
        ]

        append_command(
            report,
            title="CURRENT UV DEPENDENCY TREE",
            command=dependency_query,
        )

        selected = list(
            REQUIRED_FILES
        )
        selected.extend(
            relative
            for relative
            in OPTIONAL_FILES
            if (
                ROOT
                / relative
            ).exists()
        )
        selected.extend(
            rel(path, ROOT)
            for path in discovered
            if rel(
                path,
                ROOT,
            )
            not in selected
        )

        append_command(
            report,
            title="GIT STATUS FOR FEISHU TRANSPORT BASELINE",
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
            title="CURRENT DIFF FOR FEISHU TRANSPORT BASELINE",
            command=[
                "git",
                "diff",
                "--",
                *selected,
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
            "\n".join(
                report
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print(
            "FEISHU OFFICIAL CHANNEL TRANSPORT CURRENT SNAPSHOT V1 PASSED"
        )
        print("=" * 72)
        print()
        print(
            "lark_oapi_importable="
            + str(
                lark_oapi_available
            )
        )
        print(
            "lark_channel_importable="
            + str(
                lark_channel_available
            )
        )
        print()
        print(
            "No source file was modified."
        )
        print()
        print(
            "Upload only:"
        )
        print(output)

        return 0

    except Exception as exc:
        error.write_text(
            "\n".join(
                [
                    "Feishu Official Channel Transport Current Snapshot v1 FAILED",
                    (
                        "GeneratedAt: "
                        + datetime.now()
                        .astimezone()
                        .isoformat()
                    ),
                    "",
                    (
                        type(exc).__name__
                        + ": "
                        + str(exc)
                    ),
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
        print(
            "FEISHU OFFICIAL CHANNEL TRANSPORT CURRENT SNAPSHOT V1 FAILED"
        )
        print("=" * 72)
        print()
        print(
            "No source file was intentionally modified."
        )
        print()
        print(
            "Upload only:"
        )
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
