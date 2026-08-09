from __future__ import annotations

import ast
import hashlib
import importlib.util
import subprocess
import traceback
from datetime import datetime
from pathlib import Path


OUTPUT_NAME = "feishu_chatops_current_code_snapshot_v1.txt"
ERROR_NAME = "feishu_chatops_current_code_snapshot_v1_error.txt"

REQUIRED_FILES = (
    "pyproject.toml",
    "services/agent_runtime/app/runtime/runtime.py",
    "services/agent_runtime/app/conversation/__init__.py",
    "services/agent_runtime/app/conversation/models.py",
    "services/agent_runtime/app/conversation/chatops.py",
    "services/agent_runtime/app/conversation/identity.py",
    "services/agent_runtime/app/conversation/write_bridge.py",
    "services/agent_runtime/app/conversation/orchestrator.py",
    "services/agent_runtime/tests/test_chatops_authenticated_write_bridge.py",
    "services/agent_runtime/tests/test_conversation_orchestrator.py",
    "services/agent_runtime/tests/test_durable_conversation_chatops_contract.py",
)

OPTIONAL_FILES = (
    "services/agent_runtime/app/conversation/feishu.py",
    "services/agent_runtime/tests/test_feishu_chatops_adapter_core.py",
    "services/agent_runtime/tests/test_feishu_chatops_adapter.py",
    "services/agent_runtime/app/conversation/feishu_transport.py",
    "services/agent_runtime/app/conversation/feishu_runtime.py",
    "scripts/dev/run_feishu_chatops_long_connection_v1.py",
    "scripts/dev/install_feishu_chatops_adapter_core_v1.py",
    "uv.lock",
)

DISCOVERY_ROOTS = (
    "services/agent_runtime/app/conversation",
    "services/agent_runtime/tests",
    "scripts/dev",
)

TOKENS = (
    "feishu",
    "lark_oapi",
    "lark-oapi",
    "im.message.receive_v1",
    "card.action.trigger",
    "FeishuLongConnectionTrustBoundary",
    "FeishuActorAttestationRegistry",
    "FeishuChatOpsAdapter",
    "ChatOpsAuthenticatedWriteBridge",
    "create_chatops_authenticated_write_bridge",
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


def normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def read_text(path: Path) -> str:
    return normalize_text(
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
    return str(path.relative_to(root)).replace("\\", "/")


def append_full_file(
    report: list[str],
    *,
    root: Path,
    relative: str,
    required: bool,
    include_full_content: bool = True,
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
                f"Required current file is missing: {relative}"
            )
        return

    raw = path.read_bytes()
    text = read_text(path)

    report.extend(
        [
            f"sha256_raw={sha256_bytes(raw)}",
            f"bytes={len(raw)}",
            f"lines={len(text.splitlines())}",
            f"ast={syntax_status(path, text)}",
            "",
            "MATCHING FEISHU / CHATOPS HITS",
            "-" * 120,
        ]
    )

    hits = []
    for number, line in enumerate(text.splitlines(), start=1):
        matched = [
            token
            for token in TOKENS
            if token.lower() in line.lower()
        ]
        if matched:
            hits.append(
                f"L{number}: tokens={matched} text={line}"
            )

    report.extend(hits or ["<NONE>"])

    if include_full_content:
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
                "Large generated dependency lockfile; SHA256 and matching lines are captured above.",
            ]
        )


def discover_files(root: Path) -> list[Path]:
    found: set[Path] = set()

    for base_relative in DISCOVERY_ROOTS:
        base = root / base_relative
        if not base.exists():
            continue

        for path in base.rglob("*.py"):
            if not path.is_file():
                continue

            name = path.name.lower()
            if "feishu" in name or "chatops" in name:
                found.add(path)
                continue

            try:
                text = read_text(path)
            except Exception:
                continue

            if any(
                token.lower() in text.lower()
                for token in TOKENS
            ):
                found.add(path)

    return sorted(
        found,
        key=lambda path: rel(path, root).lower(),
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
        normalize_text(process.stdout),
        normalize_text(process.stderr),
    )


def append_command(
    report: list[str],
    *,
    title: str,
    command: list[str],
    result: tuple[int, str, str],
) -> None:
    returncode, stdout, stderr = result

    report.extend(
        [
            "",
            "=" * 120,
            f"COMMAND: {title}",
            "=" * 120,
            "",
            " ".join(command),
            "",
            f"ExitCode: {returncode}",
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
    root = find_repo_root(Path.cwd().resolve())
    output = root / OUTPUT_NAME
    error = root / ERROR_NAME

    for target in (output, error):
        try:
            target.unlink()
        except FileNotFoundError:
            pass

    report = [
        "Feishu ChatOps Current Code Snapshot v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        f"RepositoryRoot: {root}",
        "",
        "PURPOSE",
        "-" * 120,
        "- establish the exact current Feishu/ChatOps development baseline",
        "- do not assume Feishu Adapter Core v1 was installed",
        "- capture the authenticated ChatOps write baseline completely",
        "- detect whether Feishu core files/tests already exist",
        "- inspect dependency state before any official SDK transport work",
        "- bind the next installer to exact current SHA256 values",
        "",
        "DECISION AFTER THIS SNAPSHOT",
        "-" * 120,
        "- if Feishu core is absent: install Feishu ChatOps Adapter Core v1",
        "- if Feishu core is present and consistent: design Official SDK Long-Connection Transport v1",
        "",
        "SAFETY",
        "-" * 120,
        "- read-only source inspection",
        "- no source modification",
        "- no dependency installation",
        "- no Feishu network connection",
        "- no LLM request",
        "- no Kubernetes/Prometheus request",
        "- no Approval/Action/Verification write",
        "- no credential/environment value dump",
    ]

    try:
        report.extend(
            [
                "",
                "=" * 120,
                "REQUIRED CURRENT BASELINE FILES",
                "=" * 120,
            ]
        )

        for relative in REQUIRED_FILES:
            append_full_file(
                report,
                root=root,
                relative=relative,
                required=True,
                include_full_content=True,
            )

        report.extend(
            [
                "",
                "=" * 120,
                "OPTIONAL FEISHU / TRANSPORT FILES",
                "=" * 120,
            ]
        )

        for relative in OPTIONAL_FILES:
            append_full_file(
                report,
                root=root,
                relative=relative,
                required=False,
                include_full_content=(relative != "uv.lock"),
            )

        discovered = discover_files(root)

        report.extend(
            [
                "",
                "=" * 120,
                "DISCOVERED FEISHU / CHATOPS FILES",
                "=" * 120,
                "",
            ]
        )

        if discovered:
            for path in discovered:
                report.append(rel(path, root))
        else:
            report.append("<NONE>")

        module_available = (
            importlib.util.find_spec("lark_oapi")
            is not None
        )

        feishu_core_path = (
            root
            / "services/agent_runtime/app/conversation/feishu.py"
        )

        feishu_test_candidates = (
            root
            / "services/agent_runtime/tests/test_feishu_chatops_adapter_core.py",
            root
            / "services/agent_runtime/tests/test_feishu_chatops_adapter.py",
        )

        report.extend(
            [
                "",
                "=" * 120,
                "BASELINE CLASSIFICATION",
                "=" * 120,
                "",
                f"feishu_core_present={feishu_core_path.exists()}",
                "feishu_core_test_present="
                + str(
                    any(path.exists() for path in feishu_test_candidates)
                ),
                f"lark_oapi_importable={module_available}",
            ]
        )

        selected_paths = list(REQUIRED_FILES)
        selected_paths.extend(
            relative
            for relative in OPTIONAL_FILES
            if (root / relative).exists()
        )
        selected_paths.extend(
            rel(path, root)
            for path in discovered
            if rel(path, root) not in selected_paths
        )

        status_command = [
            "git",
            "status",
            "--short",
            "--",
            *selected_paths,
        ]

        append_command(
            report,
            title="Git status for current Feishu/ChatOps baseline",
            command=status_command,
            result=run_command(root, status_command),
        )

        diff_command = [
            "git",
            "diff",
            "--",
            *selected_paths,
        ]

        append_command(
            report,
            title="Current diff for Feishu/ChatOps baseline",
            command=diff_command,
            result=run_command(root, diff_command),
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
                f"Upload only: {OUTPUT_NAME}",
            ]
        )

        output.write_text(
            "\n".join(report) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print("FEISHU CHATOPS CURRENT CODE SNAPSHOT V1 PASSED")
        print("=" * 72)
        print()
        print(f"feishu_core_present={feishu_core_path.exists()}")
        print(
            "feishu_core_test_present="
            + str(
                any(
                    path.exists()
                    for path in feishu_test_candidates
                )
            )
        )
        print(f"lark_oapi_importable={module_available}")
        print()
        print("No source file was modified.")
        print()
        print("Upload only:")
        print(output)
        print()
        input("Press Enter to close...")
        return 0

    except Exception as exc:
        error.write_text(
            "\n".join(
                [
                    "Feishu ChatOps Current Code Snapshot v1 FAILED",
                    f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
                    "",
                    f"{type(exc).__name__}: {exc}",
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

        print("=" * 72)
        print("FEISHU CHATOPS CURRENT CODE SNAPSHOT V1 FAILED")
        print("=" * 72)
        print()
        print("No source file was intentionally modified.")
        print()
        print("Upload only:")
        print(error)
        print()
        input("Press Enter to close...")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
