from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import importlib.util
import inspect
import subprocess
import traceback

from datetime import datetime
from pathlib import Path
from typing import Any


OUTPUT_NAME = "feishu_live_runner_runtime_assembly_current_snapshot_v1.txt"
ERROR_NAME = "feishu_live_runner_runtime_assembly_current_snapshot_v1_error.txt"

REQUIRED_FILES = (
    "pyproject.toml",
    "uv.lock",
    "services/agent_runtime/app/conversation/feishu.py",
    "services/agent_runtime/app/conversation/feishu_channel_transport.py",
    "services/agent_runtime/app/conversation/chatops.py",
    "services/agent_runtime/app/conversation/identity.py",
    "services/agent_runtime/app/conversation/write_bridge.py",
    "services/agent_runtime/app/conversation/orchestrator.py",
    "services/agent_runtime/app/runtime/runtime.py",
    "services/agent_runtime/app/main.py",
    "services/agent_runtime/tests/test_feishu_chatops_adapter.py",
    "services/agent_runtime/tests/test_feishu_channel_transport.py",
)

OPTIONAL_FILES = (
    "services/agent_runtime/app/conversation/__init__.py",
    "services/agent_runtime/app/security/factory.py",
    "services/agent_runtime/app/security/service.py",
    "services/agent_runtime/app/security/models.py",
    "services/agent_runtime/app/security/policy.py",
    "services/agent_runtime/app/security/authentication.py",
    "services/agent_runtime/app/runtime/config.py",
    "services/agent_runtime/app/runtime/factory.py",
    "services/agent_runtime/app/config.py",
    "services/agent_runtime/app/settings.py",
    "packages/common/src/common/config/settings.py",
)

DISCOVERY_ROOTS = (
    "services/agent_runtime/app",
    "packages/common/src",
    "scripts/dev",
)

DISCOVERY_TOKENS = (
    "BaseSettings",
    "SettingsConfigDict",
    "os.environ",
    "os.getenv",
    "from_environment",
    "create_authentication_service",
    "AgentRuntime(",
    "asyncio.run",
    "uvicorn",
    "startup",
    "shutdown",
    "lifespan",
    "connect_until_ready",
    "disconnect",
    "FeishuChannel",
    "SecurityConfig",
    "PolicyConfig",
    "group_allowlist",
    "credential_env",
)

FULL_DISCOVERY_LIMIT = 20


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


def matching_lines(
    text: str,
) -> list[str]:
    hits: list[str] = []

    for number, line in enumerate(
        text.splitlines(),
        start=1,
    ):
        matched = [
            token
            for token in DISCOVERY_TOKENS
            if token.lower()
            in line.lower()
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

    return hits


def append_file(
    report: list[str],
    *,
    root: Path,
    relative: str,
    required: bool,
    full_content: bool,
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
            "LIFECYCLE / CONFIG / CREDENTIAL REFERENCE HITS",
            "-" * 120,
        ]
    )

    hits = matching_lines(text)
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
                    "Large/generated dependency file. "
                    "SHA256 and relevant matching lines are captured above."
                ),
            ]
        )


def discover_relevant_files(
    root: Path,
    already: set[str],
) -> list[Path]:
    found: set[Path] = set()

    for base_relative in DISCOVERY_ROOTS:
        base = root / base_relative

        if not base.exists():
            continue

        for path in base.rglob("*.py"):
            if not path.is_file():
                continue

            relative = rel(path, root)

            if relative in already:
                continue

            try:
                text = read_text(path)
            except Exception:
                continue

            if any(
                token.lower()
                in text.lower()
                for token in DISCOVERY_TOKENS
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


def safe_signature(
    value: Any,
) -> str:
    try:
        return str(
            inspect.signature(value)
        )
    except Exception as exc:
        return (
            "<SIGNATURE_UNAVAILABLE:"
            + type(exc).__name__
            + ">"
        )


def append_sdk_contract(
    report: list[str],
) -> None:
    report.extend(
        [
            "",
            "=" * 120,
            "INSTALLED LARK CHANNEL SDK RUNTIME CONTRACT",
            "=" * 120,
            "",
        ]
    )

    spec = importlib.util.find_spec(
        "lark_channel"
    )

    report.append(
        "lark_channel_importable="
        + str(spec is not None)
    )

    if spec is None:
        return

    try:
        version = (
            importlib.metadata
            .version(
                "lark-channel-sdk"
            )
        )
    except Exception as exc:
        version = (
            "<VERSION_UNAVAILABLE:"
            + type(exc).__name__
            + ">"
        )

    report.append(
        "lark_channel_version="
        + version
    )

    try:
        import websockets

        report.append(
            "websockets_version="
            + str(
                getattr(
                    websockets,
                    "__version__",
                    "<UNKNOWN>",
                )
            )
        )
    except Exception as exc:
        report.append(
            "websockets_version="
            + "<IMPORT_FAILED:"
            + type(exc).__name__
            + ">"
        )

    try:
        from lark_channel import (
            FeishuChannel,
            PolicyConfig,
            SecurityConfig,
        )

        report.extend(
            [
                "",
                "FeishuChannel.__init__="
                + safe_signature(
                    FeishuChannel
                ),
                "FeishuChannel.connect="
                + safe_signature(
                    FeishuChannel.connect
                ),
                "FeishuChannel.connect_until_ready="
                + safe_signature(
                    FeishuChannel.connect_until_ready
                ),
                "FeishuChannel.start_background="
                + safe_signature(
                    FeishuChannel.start_background
                ),
                "FeishuChannel.wait_ready="
                + safe_signature(
                    FeishuChannel.wait_ready
                ),
                "FeishuChannel.disconnect="
                + safe_signature(
                    FeishuChannel.disconnect
                ),
                "FeishuChannel.update_policy="
                + safe_signature(
                    FeishuChannel.update_policy
                ),
                "",
                "SecurityConfig="
                + safe_signature(
                    SecurityConfig
                ),
                "PolicyConfig="
                + safe_signature(
                    PolicyConfig
                ),
            ]
        )

        for label, factory in (
            (
                "SecurityConfig.audit",
                lambda: SecurityConfig(
                    mode="audit"
                ),
            ),
            (
                "SecurityConfig.strict",
                lambda: SecurityConfig(
                    mode="strict"
                ),
            ),
            (
                "PolicyConfig.default",
                lambda: PolicyConfig(),
            ),
        ):
            try:
                value = factory()
                report.append(
                    label
                    + "="
                    + repr(value)
                )
            except Exception as exc:
                report.append(
                    label
                    + "=<CONSTRUCTION_FAILED:"
                    + type(exc).__name__
                    + ":"
                    + str(exc)
                    + ">"
                )

    except Exception as exc:
        report.append(
            "sdk_contract_introspection_failed="
            + type(exc).__name__
            + ":"
            + str(exc)
        )


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    output = root / OUTPUT_NAME
    error = root / ERROR_NAME

    for target in (
        output,
        error,
    ):
        try:
            target.unlink()
        except FileNotFoundError:
            pass

    report = [
        "Feishu Live Channel Runner + Runtime Assembly Current Snapshot v1",
        (
            "GeneratedAt: "
            + datetime.now()
            .astimezone()
            .isoformat()
        ),
        "",
        "RepositoryRoot:",
        str(root),
        "",
        "Purpose:",
        "- capture the exact post-Transport repository state",
        "- inspect current Runtime startup/shutdown and configuration patterns",
        "- inspect credential-reference conventions without reading credential values",
        "- introspect the installed lark-channel-sdk lifecycle/config signatures",
        "- record the websockets dependency version selected by the SDK",
        "- bind the next Live Runner installer to exact current SHA256 values",
        "",
        "Target next-stage boundary:",
        "- explicit credentials by environment-variable reference",
        "- explicit FeishuChannel construction",
        "- SecurityConfig audit-first live policy",
        "- explicit group allowlist policy",
        "- explicit connect/disconnect lifecycle",
        "- no automatic AgentRuntime startup wiring unless separately enabled",
        "",
        "Safety:",
        "- read-only repository inspection",
        "- does not read any environment variable values",
        "- does not instantiate FeishuChannel with credentials",
        "- does not connect to Feishu",
        "- does not send any message",
        "- does not call LLM/Kubernetes/Prometheus",
        "- does not mutate Approval/Action/Verification",
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
                root=root,
                relative=relative,
                required=True,
                full_content=(
                    relative
                    != "uv.lock"
                ),
            )

        report.extend(
            [
                "",
                "=" * 120,
                "OPTIONAL CONFIG / SECURITY FILES",
                "=" * 120,
            ]
        )

        for relative in OPTIONAL_FILES:
            append_file(
                report,
                root=root,
                relative=relative,
                required=False,
                full_content=True,
            )

        known = set(
            REQUIRED_FILES
        ) | set(
            OPTIONAL_FILES
        )

        discovered = discover_relevant_files(
            root,
            known,
        )

        report.extend(
            [
                "",
                "=" * 120,
                "DISCOVERED LIFECYCLE / CONFIG FILES",
                "=" * 120,
                "",
            ]
        )

        if discovered:
            for path in discovered:
                report.append(
                    rel(path, root)
                )
        else:
            report.append(
                "<NONE>"
            )

        report.extend(
            [
                "",
                "=" * 120,
                "DISCOVERED FILE CONTENT (BOUNDED)",
                "=" * 120,
            ]
        )

        for path in discovered[
            :FULL_DISCOVERY_LIMIT
        ]:
            append_file(
                report,
                root=root,
                relative=rel(
                    path,
                    root,
                ),
                required=False,
                full_content=True,
            )

        if len(discovered) > FULL_DISCOVERY_LIMIT:
            report.extend(
                [
                    "",
                    (
                        "DISCOVERY_CONTENT_TRUNCATED="
                        + str(
                            len(discovered)
                            - FULL_DISCOVERY_LIMIT
                        )
                    ),
                    (
                        "All discovered paths are listed above; "
                        "only the first bounded set is expanded fully."
                    ),
                ]
            )

        append_sdk_contract(
            report
        )

        append_command(
            report,
            root=root,
            title="CURRENT UV DEPENDENCY TREE",
            command=[
                "uv",
                "tree",
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

        selected = [
            *REQUIRED_FILES,
            *[
                relative
                for relative
                in OPTIONAL_FILES
                if (
                    root
                    / relative
                ).exists()
            ],
            *[
                rel(path, root)
                for path in discovered
            ],
        ]

        append_command(
            report,
            root=root,
            title="GIT STATUS FOR LIVE RUNNER BASELINE",
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
            title="CURRENT DIFF FOR LIVE RUNNER BASELINE",
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
            "FEISHU LIVE RUNNER + RUNTIME ASSEMBLY CURRENT SNAPSHOT V1 PASSED"
        )
        print("=" * 72)
        print()
        print(
            "No source file was modified."
        )
        print(
            "No credential value was read."
        )
        print(
            "No Feishu connection was opened."
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
                    (
                        "Feishu Live Channel Runner + "
                        "Runtime Assembly Current Snapshot v1 FAILED"
                    ),
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
            "FEISHU LIVE RUNNER + RUNTIME ASSEMBLY CURRENT SNAPSHOT V1 FAILED"
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
