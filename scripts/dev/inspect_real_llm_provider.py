from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


SCRIPT_NAME = "inspect_real_llm_provider.py"
AFTER_NAME = "real_llm_provider_inspect_after.txt"
ERROR_NAME = "real_llm_provider_inspect_error.txt"


SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|authorization|access[_-]?key|secret[_-]?key)"
)

CONFIG_REF_RE = re.compile(
    r"(?i)("
    r"os\.getenv|os\.environ|OPENAI|LLM_|API_KEY|BASE_URL|MODEL|"
    r"api_key|base_url|model|provider"
    r")"
)


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def find_repo_root(start: Path) -> Path:
    candidates = [start, *start.parents]

    for candidate in candidates:
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate

    raise RuntimeError(
        "Repository root not found. Run this script from inside "
        "ai-reliability-platform or place it under scripts/dev."
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


def redact_line(line: str) -> str:
    """
    Redact obvious secret assignments in YAML/Python-like text while
    preserving field names so configuration contracts remain reviewable.
    """

    yaml_match = re.match(
        r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_.-]+)\s*:\s*(?P<value>.*)$",
        line,
    )

    if yaml_match and SECRET_KEY_RE.search(yaml_match.group("key")):
        return (
            f"{yaml_match.group('indent')}"
            f"{yaml_match.group('key')}: <REDACTED>"
        )

    assignment_match = re.match(
        r"^(?P<indent>\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$",
        line,
    )

    if (
        assignment_match
        and SECRET_KEY_RE.search(assignment_match.group("key"))
        and (
            '"' in assignment_match.group("value")
            or "'" in assignment_match.group("value")
        )
    ):
        return (
            f"{assignment_match.group('indent')}"
            f"{assignment_match.group('key')} = <REDACTED>"
        )

    return line


def read_text(path: Path) -> str:
    return path.read_text(
        encoding="utf-8-sig",
        errors="replace",
    )


def relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def section(lines: list[str], title: str) -> None:
    lines.append("")
    lines.append("=" * 120)
    lines.append(title)
    lines.append("=" * 120)
    lines.append("")


def append_file(
    *,
    lines: list[str],
    root: Path,
    path: Path,
    redact: bool = False,
) -> None:
    section(lines, relative(root, path))

    if not path.exists():
        lines.append("MISSING")
        return

    text = read_text(path)

    # Apply secret-like assignment redaction to every inspected source file.
    # YAML additionally uses the same path, so secret field names remain
    # visible while their literal values do not.
    text = "\n".join(
        redact_line(line)
        for line in text.splitlines()
    )

    lines.append(text.rstrip())


def discover_config_files(root: Path) -> list[Path]:
    found: list[Path] = []

    common_root = root / "packages" / "common"
    if common_root.exists():
        for path in common_root.rglob("*.py"):
            lowered = str(path).lower()
            if "config" in lowered or "setting" in lowered:
                found.append(path)

    for path in (
        root / "configs" / "app.yaml",
        root / "config" / "app.yaml",
        root / "app.yaml",
    ):
        if path.exists():
            found.append(path)

    return sorted(set(found))


def discover_llm_references(root: Path) -> list[str]:
    llm_root = root / "services" / "agent_runtime" / "app" / "llm"

    if not llm_root.exists():
        return ["LLM directory missing"]

    output: list[str] = []

    for path in sorted(llm_root.rglob("*.py")):
        try:
            lines = read_text(path).splitlines()
        except OSError:
            continue

        matches = [
            f"{line_number}: {line}"
            for line_number, line in enumerate(lines, start=1)
            if CONFIG_REF_RE.search(line)
        ]

        if not matches:
            continue

        output.append(f"FILE: {relative(root, path)}")
        output.extend(matches)
        output.append("")

    if not output:
        output.append("NO MATCHING LLM CONFIG REFERENCES FOUND")

    return output


def discover_relevant_tests(root: Path) -> list[Path]:
    tests_root = root / "services" / "agent_runtime" / "tests"

    preferred_names = [
        "test_real_llm_historical_run.py",
        "test_historical_incident_investigation_runner.py",
    ]

    selected: list[Path] = []

    for name in preferred_names:
        path = tests_root / name
        if path.exists():
            selected.append(path)

    keywords = (
        "openai_compatible",
        "create_llm_gateway",
        "LLMGateway",
        "provider_factory",
    )

    if tests_root.exists():
        for path in sorted(tests_root.glob("test_*.py")):
            if path in selected:
                continue

            try:
                text = read_text(path)
            except OSError:
                continue

            if any(keyword in text for keyword in keywords):
                selected.append(path)

            if len(selected) >= 6:
                break

    return selected


def provider_name(root: Path) -> CommandResult:
    return run_command(
        root=root,
        name="Configured provider name",
        command=[
            "uv",
            "run",
            "python",
            "-c",
            (
                "from common.config import get_settings; "
                "print(get_settings().llm.provider)"
            ),
        ],
    )


def py_compile_files(root: Path, files: Iterable[Path]) -> CommandResult:
    file_args = [
        relative(root, path)
        for path in files
        if path.exists()
    ]

    return run_command(
        root=root,
        name="Python syntax",
        command=[
            "uv",
            "run",
            "python",
            "-m",
            "py_compile",
            *file_args,
        ],
    )


def pytest_files(root: Path, files: list[Path]) -> CommandResult:
    args = [
        relative(root, path)
        for path in files
    ]

    return run_command(
        root=root,
        name="Focused provider/LLM compatibility tests",
        command=[
            "uv",
            "run",
            "pytest",
            *args,
            "-q",
        ],
    )


def command_block(lines: list[str], result: CommandResult) -> None:
    section(lines, f"COMMAND: {result.name}")
    lines.append(" ".join(result.command))
    lines.append("")
    lines.append(f"ExitCode: {result.returncode}")
    lines.append("")
    lines.append("STDOUT")
    lines.append("-" * 120)
    lines.append(result.stdout.rstrip() or "<EMPTY>")
    lines.append("")
    lines.append("STDERR")
    lines.append("-" * 120)
    lines.append(result.stderr.rstrip() or "<EMPTY>")


def main() -> int:
    start = Path.cwd().resolve()
    root = find_repo_root(start)

    after_path = root / AFTER_NAME
    error_path = root / ERROR_NAME

    for path in (after_path, error_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    report: list[str] = [
        "Real LLM Provider Inspection + Focused Tests",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Purpose:",
        "- inspect the existing real LLM provider contract",
        "- inspect configuration field names without printing secret values",
        "- verify the Real LLM Historical Run composition",
        "- run focused compatibility tests",
        "- make ZERO intentional external LLM requests",
    ]

    provider_files = [
        root / "services" / "agent_runtime" / "app" / "llm" / "providers" / "openai_compatible.py",
        root / "services" / "agent_runtime" / "app" / "llm" / "factory.py",
        root / "services" / "agent_runtime" / "app" / "llm" / "provider_factory.py",
        root / "services" / "agent_runtime" / "app" / "llm" / "registry.py",
        root / "services" / "agent_runtime" / "app" / "llm" / "gateway" / "factory.py",
        root / "services" / "agent_runtime" / "app" / "llm" / "gateway" / "router.py",
        root / "services" / "agent_runtime" / "app" / "evaluation" / "real_incident" / "llm_run.py",
    ]

    try:
        section(report, "PROVIDER IMPLEMENTATION FILES")

        for path in provider_files:
            append_file(
                lines=report,
                root=root,
                path=path,
                redact=False,
            )

        section(report, "CONFIG FILES - SECRET-LIKE VALUES REDACTED")

        config_files = discover_config_files(root)
        if not config_files:
            report.append("NO CONFIG FILES FOUND")
        else:
            for path in config_files:
                append_file(
                    lines=report,
                    root=root,
                    path=path,
                    redact=path.suffix.lower() in {".yaml", ".yml"},
                )

        section(report, "LLM CONFIG / ENVIRONMENT REFERENCES - SOURCE ONLY")
        report.extend(
            discover_llm_references(root)
        )

        provider_result = provider_name(root)
        command_block(report, provider_result)

        if provider_result.returncode != 0:
            raise RuntimeError(
                "Unable to read configured LLM provider name."
            )

        configured_provider = provider_result.stdout.strip()

        section(report, "CURRENT PROVIDER SUMMARY")
        report.append(f"configured_provider={configured_provider}")
        report.append(
            "is_mock="
            + str(
                configured_provider.lower() == "mock"
            )
        )

        syntax_targets = [
            path
            for path in provider_files
            if path.exists()
        ]

        syntax_result = py_compile_files(
            root,
            syntax_targets,
        )
        command_block(report, syntax_result)

        if syntax_result.returncode != 0:
            raise RuntimeError(
                "Provider/LLM Python syntax verification failed."
            )

        relevant_tests = discover_relevant_tests(root)

        section(report, "SELECTED FOCUSED TEST FILES")
        if not relevant_tests:
            raise RuntimeError(
                "No relevant provider/LLM focused tests were found."
            )

        for path in relevant_tests:
            report.append(
                relative(root, path)
            )

        tests_result = pytest_files(
            root,
            relevant_tests,
        )
        command_block(report, tests_result)

        if tests_result.returncode != 0:
            raise RuntimeError(
                "Focused provider/LLM compatibility tests failed."
            )

        section(report, "RESULT")
        report.append("PASSED")
        report.append("")
        report.append("Verified:")
        report.append("- provider/config contract inspected")
        report.append("- secret-like YAML values redacted")
        report.append("- configured provider name read")
        report.append("- provider/LLM source syntax passed")
        report.append("- Real LLM Historical Run compatibility tests passed")
        report.append("- no intentional external LLM request was made")

        if configured_provider.lower() == "mock":
            report.append("")
            report.append("NEXT BLOCKER:")
            report.append(
                "Current provider is mock. The first real historical Agent run "
                "must configure a supported non-mock provider."
            )
        else:
            report.append("")
            report.append("NEXT STATE:")
            report.append(
                "A non-mock provider is configured. Review the provider fields "
                "in this snapshot before intentionally running the first real Incident."
            )

        after_path.write_text(
            "\n".join(report) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print("REAL LLM PROVIDER INSPECTION PASSED")
        print("=" * 72)
        print(f"Configured provider: {configured_provider}")
        print(f"Focused test files: {len(relevant_tests)}")
        print("")
        print("Upload:")
        print(after_path)
        return 0

    except Exception as exc:
        error_lines = [
            "Real LLM Provider Inspection + Focused Tests FAILED",
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

        error_path.write_text(
            "\n".join(error_lines) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print("REAL LLM PROVIDER INSPECTION FAILED")
        print("=" * 72)
        print("")
        print("Upload:")
        print(error_path)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
