from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


AFTER_NAME = "real_llm_transport_diagnostic_v1_after.txt"
ERROR_NAME = "real_llm_transport_diagnostic_v1_error.txt"


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
        "Repository root not found."
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
            sys.path.insert(0, value)


def write_text(path: Path, text: str) -> None:
    path.write_text(
        text.replace("\r\n", "\n").replace("\r", "\n"),
        encoding="utf-8",
        newline="\n",
    )


def read_text(path: Path) -> str:
    return path.read_text(
        encoding="utf-8-sig",
        errors="strict",
    )


def verify_app_yaml_mock(path: Path) -> None:
    text = read_text(path)

    start = text.find("llm:")
    if start < 0:
        raise RuntimeError(
            "configs/app.yaml has no llm section"
        )

    provider = None

    for line in text[start + len("llm:") :].splitlines():
        stripped = line.strip()

        if stripped and not line.startswith((" ", "\t")):
            break

        if stripped.startswith("provider:"):
            provider = stripped.split(":", 1)[1].strip()
            break

    if provider != "mock":
        raise RuntimeError(
            "configs/app.yaml must remain provider: mock"
        )


def run_command(
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


def sanitize_text(
    value: Any,
    *,
    api_key: str,
) -> str:
    text = str(value)

    if api_key:
        text = text.replace(
            api_key,
            "<REDACTED_API_KEY>",
        )

    if len(text) > 2000:
        text = text[:2000] + "...<TRUNCATED>"

    return text


def extract_http_error(
    exc: httpx.HTTPStatusError,
    *,
    api_key: str,
) -> dict[str, Any]:
    response = exc.response

    result: dict[str, Any] = {
        "exception_type": type(exc).__name__,
        "status_code": response.status_code,
        "request_id": (
            response.headers.get("x-request-id")
            or response.headers.get("request-id")
        ),
        "content_type": response.headers.get(
            "content-type"
        ),
    }

    try:
        payload = response.json()
    except Exception:
        payload = None

    if isinstance(payload, dict):
        error = payload.get("error")

        if isinstance(error, dict):
            result["api_error"] = {
                "message": sanitize_text(
                    error.get("message"),
                    api_key=api_key,
                ),
                "type": sanitize_text(
                    error.get("type"),
                    api_key=api_key,
                ),
                "param": sanitize_text(
                    error.get("param"),
                    api_key=api_key,
                ),
                "code": sanitize_text(
                    error.get("code"),
                    api_key=api_key,
                ),
            }
        else:
            result["response_json"] = json.loads(
                sanitize_text(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                    ),
                    api_key=api_key,
                )
            )
    else:
        result["response_text"] = sanitize_text(
            response.text,
            api_key=api_key,
        )

    return result


async def capture_real_investigation_prompt(
    root: Path,
) -> tuple[str, str]:
    """
    Build the exact prompt through the real Investigation Reasoner without
    calling an external model.
    """

    install_import_paths(root)

    from services.agent_runtime.app.investigation.llm_gateway_adapter import (
        BaseInvestigationLLM,
    )
    from services.agent_runtime.app.investigation.models import (
        InvestigationScope,
        InvestigationState,
    )
    from services.agent_runtime.app.investigation.reasoner import (
        LLMInvestigationReasoner,
    )

    class CaptureLLM(
        BaseInvestigationLLM
    ):
        def __init__(self) -> None:
            self.system_prompt = None
            self.prompt = None

        async def complete(
            self,
            *,
            system_prompt: str,
            prompt: str,
        ) -> str:
            self.system_prompt = system_prompt
            self.prompt = prompt

            # Valid response only so Reasoner.decide can complete locally.
            return json.dumps(
                {
                    "hypotheses": [
                        {
                            "hypothesis_id": "capture-only",
                            "cause": "capture-only",
                            "confidence": 0.1,
                            "supporting_evidence_ids": [],
                            "conflicting_evidence_ids": [],
                            "missing_evidence": [
                                "production evidence"
                            ],
                        }
                    ],
                    "rationale_summary": (
                        "Capture the exact real prompt without network."
                    ),
                    "stop": False,
                    "stop_reason": None,
                    "next_probe": "kubernetes_pod_state",
                    "conclusion": None,
                }
            )

    capture = CaptureLLM()

    reasoner = LLMInvestigationReasoner(
        capture
    )

    scope = InvestigationScope(
        alert_name="PodOOMKilled",
        alert_message=(
            "Transport diagnostic only. "
            "No production action is allowed."
        ),
        resource="payment-api",
        namespace="payment",
        cluster="production-a",
    )

    state = InvestigationState(
        scope=scope
    )

    await reasoner.decide(
        scope,
        state,
    )

    if (
        not isinstance(
            capture.system_prompt,
            str,
        )
        or not isinstance(
            capture.prompt,
            str,
        )
    ):
        raise RuntimeError(
            "Failed to capture Investigation prompt"
        )

    return (
        capture.system_prompt,
        capture.prompt,
    )


async def one_direct_provider_request(
    *,
    root: Path,
    system_prompt: str,
    prompt: str,
) -> dict[str, Any]:
    """
    Exactly one external request.

    This intentionally bypasses Gateway retry/sanitization so the diagnostic
    can reveal the real transport/API error without changing production code.
    """

    install_import_paths(root)

    from services.agent_runtime.app.llm.models import (
        ChatRequest,
    )
    from services.agent_runtime.app.llm.provider_factory import (
        create_llm_provider,
    )

    provider = create_llm_provider(
        provider_name="openai"
    )

    request = ChatRequest(
        system_prompt=system_prompt,
        user_prompt=prompt,
        temperature=0.0,
    )

    response = await provider.chat(
        request
    )

    return {
        "provider": provider.name,
        "model": response.model,
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
        "total_tokens": response.total_tokens,
        "content": response.content,
    }


def validate_response_contract(
    *,
    root: Path,
    content: str,
) -> dict[str, Any]:
    install_import_paths(root)

    from services.agent_runtime.app.investigation.models import (
        InvestigationDecision,
    )

    try:
        payload = json.loads(
            content
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Real model returned non-JSON content"
        ) from exc

    decision = InvestigationDecision.model_validate(
        payload
    )

    return decision.model_dump(
        mode="json"
    )


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

    api_key = os.getenv(
        "OPENAI_API_KEY",
        "",
    )

    base_url = os.getenv(
        "OPENAI_BASE_URL",
        "https://api.openai.com/v1",
    )

    model = os.getenv(
        "OPENAI_MODEL",
        "gpt-5",
    )

    report = [
        "Real LLM Transport Diagnostic v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Purpose:",
        "- identify the real external API/transport failure hidden by Gateway sanitization",
        "- use the exact Investigation prompt shape",
        "- send exactly ONE external provider request",
        "- do not modify production code",
        "- do not print OPENAI_API_KEY",
    ]

    try:
        verify_app_yaml_mock(
            root / "configs" / "app.yaml"
        )

        section(
            report,
            "ENVIRONMENT",
        )

        report.extend(
            [
                f"base_url={base_url}",
                f"model={model}",
                f"api_key_present={bool(api_key)}",
                "api_key_value=<NEVER_PRINTED>",
            ]
        )

        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not present"
            )

        tests = run_command(
            root,
            "Focused no-network compatibility tests",
            [
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
                "Focused tests failed"
            )

        section(
            report,
            "PROMPT CAPTURE",
        )

        system_prompt, prompt = asyncio.run(
            capture_real_investigation_prompt(
                root
            )
        )

        report.extend(
            [
                "prompt_capture=PASSED",
                f"system_prompt_length={len(system_prompt)}",
                f"user_prompt_length={len(prompt)}",
                "prompt_content=<NOT_WRITTEN_TO_REPORT>",
            ]
        )

        section(
            report,
            "ONE DIRECT PROVIDER REQUEST",
        )

        try:
            provider_result = asyncio.run(
                one_direct_provider_request(
                    root=root,
                    system_prompt=system_prompt,
                    prompt=prompt,
                )
            )

        except httpx.HTTPStatusError as exc:
            diagnostic = extract_http_error(
                exc,
                api_key=api_key,
            )

            report.append(
                "LIVE_REQUEST=HTTP_ERROR"
            )
            report.append(
                json.dumps(
                    diagnostic,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )

            raise RuntimeError(
                "Direct provider request returned an HTTP error. "
                "See diagnostic section."
            ) from None

        except httpx.TimeoutException as exc:
            report.extend(
                [
                    "LIVE_REQUEST=TIMEOUT",
                    (
                        "exception="
                        + sanitize_text(
                            exc,
                            api_key=api_key,
                        )
                    ),
                ]
            )

            raise RuntimeError(
                "Direct provider request timed out"
            ) from None

        except httpx.RequestError as exc:
            report.extend(
                [
                    "LIVE_REQUEST=REQUEST_ERROR",
                    f"exception_type={type(exc).__name__}",
                    (
                        "exception="
                        + sanitize_text(
                            exc,
                            api_key=api_key,
                        )
                    ),
                ]
            )

            raise RuntimeError(
                "Direct provider request failed before an HTTP response"
            ) from None

        report.extend(
            [
                "LIVE_REQUEST=PASSED",
                f"provider={provider_result['provider']}",
                f"response_model={provider_result['model']}",
                f"prompt_tokens={provider_result['prompt_tokens']}",
                f"completion_tokens={provider_result['completion_tokens']}",
                f"total_tokens={provider_result['total_tokens']}",
            ]
        )

        section(
            report,
            "INVESTIGATION DECISION CONTRACT",
        )

        decision = validate_response_contract(
            root=root,
            content=provider_result[
                "content"
            ],
        )

        report.append(
            "CONTRACT=PASSED"
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

        report.extend(
            [
                "PASSED",
                "",
                "Exactly one external provider request was sent.",
                "No Gateway retry was used.",
                "No Kubernetes/Action/Approval/Verification path was invoked.",
            ]
        )

        write_text(
            after,
            "\n".join(report) + "\n",
        )

        print("=" * 72)
        print(
            "REAL LLM TRANSPORT DIAGNOSTIC PASSED"
        )
        print("=" * 72)
        print("")
        print(
            f"Model: {model}"
        )
        print("")
        print("Upload:")
        print(after)

        return 0

    except Exception as exc:
        error_lines = [
            "Real LLM Transport Diagnostic v1 FAILED",
            f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
            "",
            "Exception:",
            sanitize_text(
                f"{type(exc).__name__}: {exc}",
                api_key=api_key,
            ),
            "",
            "Traceback:",
            sanitize_text(
                traceback.format_exc(),
                api_key=api_key,
            ),
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
        print(
            "REAL LLM TRANSPORT DIAGNOSTIC FAILED"
        )
        print("=" * 72)
        print("")
        print(
            "The error file contains the sanitized HTTP/API diagnostic."
        )
        print("")
        print("Upload:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
