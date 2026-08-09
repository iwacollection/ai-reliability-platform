from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
import traceback
from datetime import datetime
from pathlib import Path


OUTPUT_NAME = "llm_gateway_reliability_current_snapshot_v1.txt"
ERROR_NAME = "llm_gateway_reliability_current_snapshot_v1_error.txt"

CORE_FILES = (
    "services/agent_runtime/app/llm/providers/openai_compatible.py",
    "services/agent_runtime/app/llm/client.py",
    "services/agent_runtime/app/llm/observed_client.py",
    "services/agent_runtime/app/llm/gateway/executor.py",
    "services/agent_runtime/app/llm/gateway/gateway.py",
    "services/agent_runtime/app/llm/gateway/circuit_breaker.py",
    "services/agent_runtime/app/llm/gateway/rate_limiter.py",
    "services/agent_runtime/app/llm/gateway/provider_health.py",
    "services/agent_runtime/app/llm/gateway/fallback.py",
    "services/agent_runtime/app/llm/gateway/provider_manager.py",
    "services/agent_runtime/app/llm/gateway/factory.py",
    "services/agent_runtime/app/investigation/llm_gateway_adapter.py",
    "services/agent_runtime/app/investigation/reasoner.py",
    "services/agent_runtime/app/evaluation/real_incident/llm_run.py",
    "scripts/dev/run_investigation_intelligence_benchmark_v1.py",
    "scripts/dev/run_investigation_benchmark_batch_v1.py",
    "packages/common/src/common/config/settings.py",
    "configs/app.yaml",
)

TEST_PATTERNS = (
    "services/agent_runtime/tests/test_llm*.py",
    "services/agent_runtime/tests/test_*gateway*.py",
    "services/agent_runtime/tests/test_investigation_execution_resilience.py",
    "services/agent_runtime/tests/test_investigation_llm*.py",
    "services/agent_runtime/tests/test_real_llm*.py",
)

SECRET_KEY_PATTERN = re.compile(
    r"(?i)(api[_-]?key|authorization|token|secret|password|credential)"
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
        "Repository root not found. "
        "Run from inside ai-reliability-platform."
    )


def normalize_text(value: str) -> str:
    return (
        value
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def read_text(path: Path) -> str:
    return normalize_text(
        path.read_text(
            encoding="utf-8-sig",
            errors="replace",
        )
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def ast_status(value: str) -> str:
    try:
        ast.parse(value)
        return "PASSED"
    except SyntaxError as exc:
        return (
            "FAILED: "
            f"{exc.msg} "
            f"(line={exc.lineno}, offset={exc.offset})"
        )


def collect_tests(root: Path) -> list[Path]:
    found: set[Path] = set()

    for pattern in TEST_PATTERNS:
        found.update(
            path
            for path in root.glob(pattern)
            if path.is_file()
        )

    return sorted(
        found,
        key=lambda path: str(
            path.relative_to(root)
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
        normalize_text(process.stdout),
        normalize_text(process.stderr),
    )


def redact_config_text(
    relative: str,
    source: str,
) -> str:
    """
    Snapshot source code exactly, but redact secret-like config values.

    configs/app.yaml is expected to contain no secret values today, but this
    remains fail-safe if that changes later.
    """
    if relative != "configs/app.yaml":
        return source

    result = []

    for line in source.splitlines():
        if ":" not in line:
            result.append(line)
            continue

        key, value = line.split(":", 1)

        if SECRET_KEY_PATTERN.search(key):
            result.append(
                key + ": <REDACTED>"
            )
        else:
            result.append(line)

    return "\n".join(result) + "\n"


def top_level_symbols(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    result = []

    for node in tree.body:
        if isinstance(
            node,
            (
                ast.ClassDef,
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        ):
            result.append(
                f"{type(node).__name__}: "
                f"{node.name} "
                f"(line {node.lineno})"
            )

    return result


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    output = root / OUTPUT_NAME
    error = root / ERROR_NAME

    for path in (output, error):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    try:
        paths: list[Path] = []
        missing = []

        for relative in CORE_FILES:
            path = root / relative

            if path.exists() and path.is_file():
                paths.append(path)
            else:
                missing.append(relative)

        tests = collect_tests(root)

        for path in tests:
            if path not in paths:
                paths.append(path)

        report = [
            "=" * 120,
            "LLM GATEWAY RELIABILITY CURRENT SNAPSHOT v1",
            "=" * 120,
            "",
            "GeneratedAt: "
            + datetime.now().astimezone().isoformat(),
            "RepositoryRoot: " + str(root),
            "",
            "Purpose:",
            "- capture the exact CURRENT LLM Gateway / Bailian transport stack",
            "- capture Investigation adapter + reasoner interaction",
            "- capture current related tests and benchmark runners",
            "- prepare Gateway Transport Reliability work without stale-code patching",
            "",
            "Safety:",
            "- read-only",
            "- no external LLM request",
            "- no Kubernetes or Prometheus request",
            "- no environment-variable values are printed",
            "- no API keys/tokens are printed",
            "- configs/app.yaml secret-like values are redacted",
            "",
            "IMPORTANT ENVIRONMENT PRESENCE ONLY",
            "-" * 120,
        ]

        for name in (
            "DASHSCOPE_API_KEY",
            "BAILIAN_BASE_URL",
            "BAILIAN_MODEL",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
        ):
            report.append(
                f"{name}_PRESENT={bool(os.getenv(name))}"
            )

        report.extend(
            [
                "",
                "FILES",
                "-" * 120,
            ]
        )

        for path in paths:
            report.append(
                str(path.relative_to(root))
            )

        report.extend(
            [
                "",
                "MISSING EXPECTED FILES",
                "-" * 120,
                *(missing or ["<NONE>"]),
                "",
                "STRUCTURAL INDEX",
                "=" * 120,
            ]
        )

        for path in paths:
            relative = str(
                path.relative_to(root)
            ).replace("\\", "/")

            source = read_text(path)
            visible_source = redact_config_text(
                relative,
                source,
            )

            report.extend(
                [
                    "",
                    relative,
                    "sha256="
                    + sha256_text(source),
                    "lines="
                    + str(len(source.splitlines())),
                    "ast="
                    + (
                        "N/A-YAML"
                        if relative.endswith(".yaml")
                        else ast_status(source)
                    ),
                ]
            )

            for symbol in top_level_symbols(
                source
            ):
                report.append(
                    "  " + symbol
                )

            if visible_source != source:
                report.append(
                    "snapshot_content_redacted=True"
                )

        report.extend(
            [
                "",
                "KEY RELIABILITY REFERENCES",
                "=" * 120,
            ]
        )

        terms = (
            "retry_attempts",
            "asyncio.sleep",
            "LLM execution failed",
            "CircuitBreakerOpen",
            "failure_threshold",
            "recovery_timeout",
            "RateLimitExceeded",
            "requests_per_minute",
            "httpx.AsyncClient",
            "response.raise_for_status",
            "InvestigationLLMExecutionError",
            "InvestigationLLMUnavailableError",
            "_complete_with_execution_retry",
            "enable_fallback",
        )

        for term in terms:
            report.extend(
                [
                    "",
                    f"TERM: {term}",
                ]
            )

            matches = []

            for path in paths:
                relative = str(
                    path.relative_to(root)
                ).replace("\\", "/")

                if relative.endswith(".yaml"):
                    continue

                source = read_text(path)

                for number, line in enumerate(
                    source.splitlines(),
                    start=1,
                ):
                    if term in line:
                        matches.append(
                            f"{relative}:{number}: {line.strip()}"
                        )

            report.extend(
                matches or ["<NO MATCH>"]
            )

        rel_paths = [
            str(path.relative_to(root))
            for path in paths
        ]

        status_code, status_out, status_err = (
            run_command(
                root,
                [
                    "git",
                    "status",
                    "--short",
                    "--",
                    *rel_paths,
                ],
            )
        )

        report.extend(
            [
                "",
                "GIT STATUS",
                "=" * 120,
                f"ExitCode: {status_code}",
                status_out.rstrip() or "<EMPTY>",
                "",
                "STDERR:",
                status_err.rstrip() or "<EMPTY>",
                "",
                "FULL CURRENT FILE CONTENT",
                "=" * 120,
            ]
        )

        for path in paths:
            relative = str(
                path.relative_to(root)
            ).replace("\\", "/")

            source = read_text(path)
            visible_source = redact_config_text(
                relative,
                source,
            )

            report.extend(
                [
                    "",
                    "=" * 120,
                    relative,
                    "=" * 120,
                    "sha256=" + sha256_text(source),
                    "",
                    visible_source.rstrip("\n"),
                    "",
                ]
            )

        report.extend(
            [
                "",
                "=" * 120,
                "RESULT",
                "=" * 120,
                "",
                "SNAPSHOT=COMPLETED",
                f"files_captured={len(paths)}",
                f"related_tests_captured={len(tests)}",
                "",
                "No repository source file was modified.",
                "No external request was sent.",
                "",
            ]
        )

        output.write_text(
            "\n".join(report),
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print(
            "LLM GATEWAY RELIABILITY CURRENT SNAPSHOT V1 COMPLETED"
        )
        print("=" * 72)
        print("")
        print(
            f"Files captured: {len(paths)}"
        )
        print(
            f"Related tests captured: {len(tests)}"
        )
        print("")
        print("Upload only:")
        print(output)

        return 0

    except Exception as exc:
        error.write_text(
            "\n".join(
                [
                    "LLM Gateway Reliability Current Snapshot v1 FAILED",
                    "",
                    f"{type(exc).__name__}: {exc}",
                    "",
                    traceback.format_exc(),
                ]
            ),
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print(
            "LLM GATEWAY RELIABILITY CURRENT SNAPSHOT V1 FAILED"
        )
        print("=" * 72)
        print("")
        print("Upload only:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
