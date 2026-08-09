from __future__ import annotations

import argparse
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


AFTER_NAME = "bailian_connectivity_preflight_v1_after.txt"
ERROR_NAME = "bailian_connectivity_preflight_v1_error.txt"

DEFAULT_BASE_URL = (
    "https://dashscope.aliyuncs.com"
    "/compatible-mode/v1"
)

DEFAULT_MODEL = "qwen-plus"


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
        "Repository root not found. "
        "Run this script from inside ai-reliability-platform."
    )


def install_import_paths(
    root: Path,
) -> None:
    candidates = [
        root,
        root / "packages" / "common" / "src",
    ]

    for candidate in reversed(
        candidates
    ):
        value = str(
            candidate
        )

        if value not in sys.path:
            sys.path.insert(
                0,
                value,
            )


def read_text(
    path: Path,
) -> str:
    return path.read_text(
        encoding="utf-8-sig",
        errors="strict",
    )


def write_text(
    path: Path,
    text: str,
) -> None:
    path.write_text(
        text.replace(
            "\r\n",
            "\n",
        ).replace(
            "\r",
            "\n",
        ),
        encoding="utf-8",
        newline="\n",
    )


def verify_app_yaml_mock(
    path: Path,
) -> None:
    text = read_text(
        path
    )

    start = text.find(
        "llm:"
    )

    if start < 0:
        raise RuntimeError(
            "configs/app.yaml has no llm section"
        )

    provider = None

    for line in text[
        start
        + len(
            "llm:"
        ) :
    ].splitlines():

        stripped = line.strip()

        if (
            stripped
            and not line.startswith(
                (
                    " ",
                    "\t",
                )
            )
        ):
            break

        if stripped.startswith(
            "provider:"
        ):
            provider = (
                stripped
                .split(
                    ":",
                    1,
                )[1]
                .strip()
            )

            break

    if provider != "mock":
        raise RuntimeError(
            "Safety invariant failed: "
            "configs/app.yaml must remain provider: mock"
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
        returncode=(
            process.returncode
        ),
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
        (
            f"COMMAND: "
            f"{result.name}"
        ),
    )

    lines.extend(
        [
            " ".join(
                result.command
            ),
            "",
            (
                f"ExitCode: "
                f"{result.returncode}"
            ),
            "",
            "STDOUT",
            "-" * 120,
            (
                result.stdout.rstrip()
                or "<EMPTY>"
            ),
            "",
            "STDERR",
            "-" * 120,
            (
                result.stderr.rstrip()
                or "<EMPTY>"
            ),
        ]
    )


def sanitize(
    value: Any,
    *,
    api_key: str,
) -> str:
    text = str(
        value
    )

    if api_key:
        text = text.replace(
            api_key,
            "<REDACTED_API_KEY>",
        )

    if len(
        text
    ) > 3000:
        text = (
            text[:3000]
            + "...<TRUNCATED>"
        )

    return text


def extract_http_error(
    exc: httpx.HTTPStatusError,
    *,
    api_key: str,
) -> dict[str, Any]:
    response = exc.response

    result: dict[str, Any] = {
        "exception_type": (
            type(
                exc
            ).__name__
        ),
        "status_code": (
            response.status_code
        ),
        "request_id": (
            response.headers.get(
                "x-request-id"
            )
            or response.headers.get(
                "request-id"
            )
        ),
        "content_type": (
            response.headers.get(
                "content-type"
            )
        ),
    }

    try:
        payload = (
            response.json()
        )

    except Exception:
        payload = None

    if isinstance(
        payload,
        dict,
    ):
        error = payload.get(
            "error"
        )

        if isinstance(
            error,
            dict,
        ):
            result[
                "api_error"
            ] = {
                "message": sanitize(
                    error.get(
                        "message"
                    ),
                    api_key=api_key,
                ),
                "type": sanitize(
                    error.get(
                        "type"
                    ),
                    api_key=api_key,
                ),
                "param": sanitize(
                    error.get(
                        "param"
                    ),
                    api_key=api_key,
                ),
                "code": sanitize(
                    error.get(
                        "code"
                    ),
                    api_key=api_key,
                ),
            }

        else:
            result[
                "response_json"
            ] = sanitize(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                ),
                api_key=api_key,
            )

    else:
        result[
            "response_text"
        ] = sanitize(
            response.text,
            api_key=api_key,
        )

    return result


async def capture_real_investigation_prompt(
    root: Path,
) -> tuple[
    str,
    str,
]:
    install_import_paths(
        root
    )

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
        def __init__(
            self,
        ) -> None:
            self.system_prompt: (
                str
                | None
            ) = None

            self.prompt: (
                str
                | None
            ) = None

        async def complete(
            self,
            *,
            system_prompt: str,
            prompt: str,
        ) -> str:
            self.system_prompt = (
                system_prompt
            )

            self.prompt = prompt

            return json.dumps(
                {
                    "hypotheses": [
                        {
                            "hypothesis_id": (
                                "capture-only"
                            ),
                            "cause": (
                                "capture-only"
                            ),
                            "confidence": 0.1,
                            "supporting_evidence_ids": [],
                            "conflicting_evidence_ids": [],
                            "missing_evidence": [
                                "production evidence"
                            ],
                        }
                    ],
                    "rationale_summary": (
                        "Capture prompt without network."
                    ),
                    "stop": False,
                    "stop_reason": None,
                    "next_probe": (
                        "kubernetes_pod_state"
                    ),
                    "conclusion": None,
                }
            )

    capture = CaptureLLM()

    reasoner = (
        LLMInvestigationReasoner(
            capture
        )
    )

    scope = (
        InvestigationScope(
            alert_name=(
                "PodOOMKilled"
            ),
            alert_message=(
                "Bailian connectivity preflight only. "
                "No production action is allowed."
            ),
            resource=(
                "payment-api"
            ),
            namespace=(
                "payment"
            ),
            cluster=(
                "production-a"
            ),
        )
    )

    state = (
        InvestigationState(
            scope=scope
        )
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
            "Could not capture Investigation prompt"
        )

    return (
        capture.system_prompt,
        capture.prompt,
    )


async def send_one_bailian_request(
    *,
    root: Path,
    system_prompt: str,
    prompt: str,
):
    install_import_paths(
        root
    )

    from services.agent_runtime.app.llm.models import (
        ChatRequest,
    )
    from services.agent_runtime.app.llm.provider_factory import (
        create_llm_provider,
    )

    provider = create_llm_provider(
        provider_name=(
            "bailian"
        )
    )

    response = await provider.chat(
        ChatRequest(
            system_prompt=(
                system_prompt
            ),
            user_prompt=prompt,
            temperature=0.0,
        )
    )

    return response


def validate_decision(
    *,
    root: Path,
    content: str,
) -> dict[str, Any]:
    install_import_paths(
        root
    )

    from services.agent_runtime.app.investigation.models import (
        InvestigationDecision,
    )

    try:
        payload = json.loads(
            content
        )

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Bailian model returned non-JSON content"
        ) from exc

    decision = (
        InvestigationDecision
        .model_validate(
            payload
        )
    )

    return decision.model_dump(
        mode="json"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run Bailian local tests and exactly one "
            "real Investigation preflight request."
        )
    )

    parser.add_argument(
        "--base-url",
        default=(
            os.getenv(
                "BAILIAN_BASE_URL"
            )
            or DEFAULT_BASE_URL
        ),
        help=(
            "Bailian OpenAI-compatible base URL. "
            "Defaults to the public China (Beijing) endpoint."
        ),
    )

    parser.add_argument(
        "--model",
        default=(
            os.getenv(
                "BAILIAN_MODEL"
            )
            or DEFAULT_MODEL
        ),
        help=(
            "Bailian model name. "
            "Defaults to qwen-plus."
        ),
    )

    parser.add_argument(
        "--skip-live",
        action="store_true",
        help=(
            "Run only no-network validation/tests."
        ),
    )

    args = parser.parse_args()

    root = find_repo_root(
        Path.cwd().resolve()
    )

    install_import_paths(
        root
    )

    after = (
        root
        / AFTER_NAME
    )

    error = (
        root
        / ERROR_NAME
    )

    for path in (
        after,
        error,
    ):
        try:
            path.unlink()

        except FileNotFoundError:
            pass

    api_key = os.getenv(
        "DASHSCOPE_API_KEY",
        "",
    ).strip()

    base_url = (
        args.base_url
        .strip()
        .rstrip(
            "/"
        )
    )

    model = (
        args.model
        .strip()
    )

    report = [
        "Bailian Connectivity + InvestigationDecision Preflight v1",
        (
            "GeneratedAt: "
            + datetime.now()
            .astimezone()
            .isoformat()
        ),
        "",
        "Safety:",
        "- configs/app.yaml remains provider=mock",
        "- BAILIAN_BASE_URL / BAILIAN_MODEL are process-local only",
        "- DASHSCOPE_API_KEY value is never printed",
        "- exactly one external Bailian request in live mode",
        "- no Kubernetes / Action / Approval / Verification execution",
    ]

    try:
        verify_app_yaml_mock(
            root
            / "configs"
            / "app.yaml"
        )

        if not api_key:
            raise RuntimeError(
                "DASHSCOPE_API_KEY is not present"
            )

        if not base_url:
            raise RuntimeError(
                "Bailian Base URL is blank"
            )

        if not model:
            raise RuntimeError(
                "Bailian model is blank"
            )

        # Only this Python process is configured.
        # No user/system environment is mutated.
        os.environ[
            "BAILIAN_BASE_URL"
        ] = base_url

        os.environ[
            "BAILIAN_MODEL"
        ] = model

        section(
            report,
            "CONFIGURATION",
        )

        report.extend(
            [
                (
                    f"base_url="
                    f"{base_url}"
                ),
                (
                    f"model="
                    f"{model}"
                ),
                "DASHSCOPE_API_KEY_PRESENT=True",
                "DASHSCOPE_API_KEY_VALUE=<NEVER_PRINTED>",
                "",
                "Configuration persistence=False",
            ]
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
                (
                    "services/agent_runtime/app/llm/"
                    "providers/bailian_compatible.py"
                ),
                (
                    "services/agent_runtime/app/llm/"
                    "factory.py"
                ),
                (
                    "services/agent_runtime/app/llm/"
                    "provider_factory.py"
                ),
                (
                    "services/agent_runtime/app/llm/"
                    "gateway/factory.py"
                ),
                (
                    "services/agent_runtime/app/"
                    "investigation/reasoner.py"
                ),
                (
                    "services/agent_runtime/app/evaluation/"
                    "real_incident/llm_run.py"
                ),
            ],
        )

        add_command(
            report,
            syntax,
        )

        if (
            syntax.returncode
            != 0
        ):
            raise RuntimeError(
                "Python syntax verification failed"
            )

        tests = run_command(
            root=root,
            name=(
                "Bailian + Historical LLM "
                "no-network focused tests"
            ),
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_bailian_provider.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_llm_provider_override.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_real_llm_historical_run.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_historical_incident_investigation_runner.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_reasoner.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_llm_config.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            tests,
        )

        if (
            tests.returncode
            != 0
        ):
            raise RuntimeError(
                "Focused tests failed"
            )

        section(
            report,
            "PROMPT CAPTURE",
        )

        (
            system_prompt,
            prompt,
        ) = asyncio.run(
            capture_real_investigation_prompt(
                root
            )
        )

        report.extend(
            [
                "prompt_capture=PASSED",
                (
                    "system_prompt_length="
                    + str(
                        len(
                            system_prompt
                        )
                    )
                ),
                (
                    "user_prompt_length="
                    + str(
                        len(
                            prompt
                        )
                    )
                ),
                "prompt_content=<NOT_WRITTEN>",
            ]
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
                "ONE REAL BAILIAN REQUEST",
            )

            report.extend(
                [
                    "Request path:",
                    "BailianCompatibleProvider",
                    "-> Bailian OpenAI-compatible Chat Completions",
                    "-> Qwen",
                    "",
                ]
            )

            try:
                response = asyncio.run(
                    send_one_bailian_request(
                        root=root,
                        system_prompt=system_prompt,
                        prompt=prompt,
                    )
                )

            except httpx.HTTPStatusError as exc:
                diagnostic = (
                    extract_http_error(
                        exc,
                        api_key=api_key,
                    )
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
                    "Bailian returned an HTTP error. "
                    "See sanitized diagnostic."
                ) from None

            except httpx.TimeoutException as exc:
                report.extend(
                    [
                        "LIVE_REQUEST=TIMEOUT",
                        (
                            "exception="
                            + sanitize(
                                exc,
                                api_key=api_key,
                            )
                        ),
                    ]
                )

                raise RuntimeError(
                    "Bailian request timed out"
                ) from None

            except httpx.RequestError as exc:
                report.extend(
                    [
                        "LIVE_REQUEST=REQUEST_ERROR",
                        (
                            "exception_type="
                            + type(
                                exc
                            ).__name__
                        ),
                        (
                            "exception="
                            + sanitize(
                                exc,
                                api_key=api_key,
                            )
                        ),
                    ]
                )

                raise RuntimeError(
                    "Bailian request failed before HTTP response"
                ) from None

            report.extend(
                [
                    "LIVE_REQUEST=PASSED",
                    (
                        f"response_model="
                        f"{response.model}"
                    ),
                    (
                        f"prompt_tokens="
                        f"{response.prompt_tokens}"
                    ),
                    (
                        f"completion_tokens="
                        f"{response.completion_tokens}"
                    ),
                    (
                        f"total_tokens="
                        f"{response.total_tokens}"
                    ),
                ]
            )

            section(
                report,
                "INVESTIGATION DECISION CONTRACT",
            )

            decision = validate_decision(
                root=root,
                content=(
                    response.content
                ),
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
                "configs/app.yaml provider=mock",
                "provider=bailian",
                (
                    f"model="
                    f"{model}"
                ),
                (
                    "live_request_sent="
                    + str(
                        not args.skip_live
                    )
                ),
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
            "BAILIAN CONNECTIVITY PREFLIGHT PASSED"
        )
        print(
            "=" * 72
        )
        print("")
        print(
            f"Base URL: {base_url}"
        )
        print(
            f"Model: {model}"
        )
        print(
            f"Live request sent: {not args.skip_live}"
        )
        print("")
        print(
            "Upload:"
        )
        print(
            after
        )

        return 0

    except Exception as exc:
        error_lines = [
            (
                "Bailian Connectivity + "
                "InvestigationDecision Preflight v1 FAILED"
            ),
            (
                "GeneratedAt: "
                + datetime.now()
                .astimezone()
                .isoformat()
            ),
            "",
            "Exception:",
            sanitize(
                (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
                api_key=api_key,
            ),
            "",
            "Traceback:",
            sanitize(
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
            "\n".join(
                error_lines
            )
            + "\n",
        )

        print(
            "=" * 72
        )
        print(
            "BAILIAN CONNECTIVITY PREFLIGHT FAILED"
        )
        print(
            "=" * 72
        )
        print("")
        print(
            "Upload:"
        )
        print(
            error
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
