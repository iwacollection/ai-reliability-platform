from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


AFTER_NAME = "real_llm_connectivity_preflight_v1_after.txt"
ERROR_NAME = "real_llm_connectivity_preflight_v1_error.txt"


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
        "Repository root not found. Run this script from inside "
        "ai-reliability-platform."
    )


def read_text(path: Path) -> str:
    return path.read_text(
        encoding="utf-8-sig",
        errors="strict",
    )


def write_text(path: Path, text: str) -> None:
    path.write_text(
        text.replace("\r\n", "\n").replace("\r", "\n"),
        encoding="utf-8",
        newline="\n",
    )


def verify_app_yaml_mock(path: Path) -> None:
    text = read_text(path)

    start = text.find("llm:")
    if start < 0:
        raise RuntimeError(
            "configs/app.yaml has no llm section"
        )

    remainder = text[start + len("llm:") :]

    provider = None

    for line in remainder.splitlines():
        stripped = line.strip()

        if stripped and not line.startswith((" ", "\t")):
            break

        if stripped.startswith("provider:"):
            provider = stripped.split(":", 1)[1].strip()
            break

    if provider != "mock":
        raise RuntimeError(
            "Safety invariant failed: configs/app.yaml must remain provider: mock"
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


def add_command(
    lines: list[str],
    result: CommandResult,
) -> None:
    section(
        lines,
        f"COMMAND: {result.name}",
    )

    lines.append(
        " ".join(result.command)
    )
    lines.append("")
    lines.append(
        f"ExitCode: {result.returncode}"
    )
    lines.append("")
    lines.append("STDOUT")
    lines.append("-" * 120)
    lines.append(
        result.stdout.rstrip()
        or "<EMPTY>"
    )
    lines.append("")
    lines.append("STDERR")
    lines.append("-" * 120)
    lines.append(
        result.stderr.rstrip()
        or "<EMPTY>"
    )


async def run_live_investigation_preflight() -> dict:
    from services.agent_runtime.app.investigation.llm_gateway_adapter import (
        InvestigationLLMGatewayAdapter,
    )
    from services.agent_runtime.app.investigation.models import (
        InvestigationScope,
        InvestigationState,
    )
    from services.agent_runtime.app.investigation.reasoner import (
        LLMInvestigationReasoner,
    )
    from services.agent_runtime.app.llm.gateway.factory import (
        create_llm_gateway,
    )

    gateway = create_llm_gateway(
        provider_name="openai"
    )

    adapter = InvestigationLLMGatewayAdapter(
        gateway
    )

    reasoner = LLMInvestigationReasoner(
        adapter
    )

    scope = InvestigationScope(
        alert_name="PodOOMKilled",
        alert_message=(
            "Connectivity preflight only. "
            "No production action is allowed."
        ),
        resource="payment-api",
        namespace="payment",
        cluster="production-a",
    )

    state = InvestigationState(
        scope=scope
    )

    decision = await reasoner.decide(
        scope,
        state,
    )

    return decision.model_dump(
        mode="json"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run focused local tests and exactly one intentional "
            "real LLM Investigation connectivity request."
        )
    )

    parser.add_argument(
        "--skip-live",
        action="store_true",
        help=(
            "Run only local checks/tests and do not send the real LLM request."
        ),
    )

    args = parser.parse_args()

    root = find_repo_root(
        Path.cwd().resolve()
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
        "Real LLM Connectivity + Investigation JSON Contract Preflight v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Safety:",
        "- configs/app.yaml must remain provider=mock",
        "- no Kubernetes Tool is constructed by this script",
        "- no Action/Approval/Verification path is invoked",
        "- API key value is never printed",
        "- live mode sends exactly one Investigation reasoning request",
    ]

    try:
        verify_app_yaml_mock(
            root / "configs" / "app.yaml"
        )

        section(
            report,
            "ENVIRONMENT PREFLIGHT",
        )

        base_url = os.getenv(
            "OPENAI_BASE_URL",
            "https://api.openai.com/v1",
        )

        model = os.getenv(
            "OPENAI_MODEL",
            "gpt-5",
        )

        api_key_present = bool(
            os.getenv(
                "OPENAI_API_KEY",
                "",
            )
        )

        report.append(
            f"base_url={base_url}"
        )
        report.append(
            f"model={model}"
        )
        report.append(
            f"api_key_present={api_key_present}"
        )
        report.append(
            "api_key_value=<NEVER_PRINTED>"
        )

        if not api_key_present:
            raise RuntimeError(
                "OPENAI_API_KEY is not present in this process environment"
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
                "services/agent_runtime/app/llm/provider_factory.py",
                "services/agent_runtime/app/llm/gateway/factory.py",
                "services/agent_runtime/app/investigation/llm_gateway_adapter.py",
                "services/agent_runtime/app/investigation/reasoner.py",
                "services/agent_runtime/app/evaluation/real_incident/llm_run.py",
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
            name="Focused no-network compatibility tests",
            command=[
                "uv",
                "run",
                "pytest",
                "services/agent_runtime/tests/test_llm_provider_override.py",
                "services/agent_runtime/tests/test_real_llm_historical_run.py",
                "services/agent_runtime/tests/test_historical_incident_investigation_runner.py",
                "services/agent_runtime/tests/test_investigation_reasoner.py",
                "services/agent_runtime/tests/test_llm_config.py",
                "-q",
            ],
        )

        add_command(
            report,
            tests,
        )

        if tests.returncode != 0:
            raise RuntimeError(
                "Focused no-network compatibility tests failed"
            )

        if args.skip_live:
            section(
                report,
                "LIVE REQUEST",
            )
            report.append(
                "SKIPPED by --skip-live"
            )
        else:
            section(
                report,
                "LIVE REQUEST",
            )

            report.append(
                "About to send exactly one request through:"
            )
            report.append(
                "OpenAICompatibleProvider"
            )
            report.append(
                "-> Shared LLM Gateway"
            )
            report.append(
                "-> InvestigationLLMGatewayAdapter"
            )
            report.append(
                "-> LLMInvestigationReasoner"
            )
            report.append("")

            decision = asyncio.run(
                run_live_investigation_preflight()
            )

            report.append(
                "LIVE_REQUEST=PASSED"
            )
            report.append("")
            report.append(
                "Validated InvestigationDecision:"
            )
            report.append(
                json.dumps(
                    decision,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )

        section(
            report,
            "RESULT",
        )

        report.append(
            "PASSED"
        )
        report.append("")
        report.append(
            "configs/app.yaml provider=mock"
        )
        report.append(
            "explicit provider override=openai"
        )
        report.append(
            f"model={model}"
        )
        report.append(
            f"live_request_sent={not args.skip_live}"
        )

        write_text(
            after,
            "\n".join(report) + "\n",
        )

        print("=" * 72)
        print("REAL LLM CONNECTIVITY PREFLIGHT PASSED")
        print("=" * 72)
        print("")
        print(f"Model: {model}")
        print(
            f"Live request sent: {not args.skip_live}"
        )
        print("")
        print("Upload:")
        print(after)

        return 0

    except Exception as exc:
        error_lines = [
            "Real LLM Connectivity + Investigation JSON Contract "
            "Preflight v1 FAILED",
            f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
            "",
            "Exception:",
            f"{type(exc).__name__}: {exc}",
            "",
            "Traceback:",
            traceback.format_exc(),
            "",
            "Important:",
            "- API key value was not intentionally printed",
            "- no production Action was invoked by this script",
            "",
            "PARTIAL REPORT",
            "=" * 120,
            *report,
        ]

        write_text(
            error,
            "\n".join(error_lines) + "\n",
        )

        print("=" * 72)
        print("REAL LLM CONNECTIVITY PREFLIGHT FAILED")
        print("=" * 72)
        print("")
        print("Upload:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
