from __future__ import annotations

import ast
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "real-llm-provider-override-v2"
AFTER_NAME = "real_llm_provider_override_v2_after.txt"
ERROR_NAME = "real_llm_provider_override_v2_error.txt"


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
        "Repository root not found. Run from inside ai-reliability-platform."
    )


def read_text(path: Path) -> str:
    return path.read_text(
        encoding="utf-8-sig",
        errors="strict",
    )


def write_text(path: Path, text: str) -> None:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    path.write_text(
        normalized,
        encoding="utf-8",
        newline="\n",
    )


def backup_file(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(
        f"{path.name}.before_{VERSION}_{stamp}.bak"
    )
    shutil.copy2(path, backup)
    return backup


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


def replace_top_level_function(
    path: Path,
    function_name: str,
    new_source: str,
) -> None:
    text = read_text(path)
    tree = ast.parse(text)

    matches = [
        node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
        and node.name == function_name
    ]

    if len(matches) != 1:
        raise RuntimeError(
            f"{path.name}:{function_name}: "
            f"expected exactly one top-level function, found {len(matches)}"
        )

    node = matches[0]

    if node.end_lineno is None:
        raise RuntimeError(
            f"{path.name}:{function_name}: AST end_lineno unavailable"
        )

    lines = text.splitlines(keepends=True)

    replacement = (
        new_source.strip("\n")
        + "\n"
    )

    updated = (
        "".join(lines[: node.lineno - 1])
        + replacement
        + "".join(lines[node.end_lineno :])
    )

    ast.parse(updated)
    write_text(path, updated)


def verify_app_yaml_mock(path: Path) -> None:
    text = read_text(path)

    marker = "llm:"
    start = text.find(marker)

    if start < 0:
        raise RuntimeError(
            "configs/app.yaml has no llm section"
        )

    remainder = text[start + len(marker) :]

    provider_line = None

    for line in remainder.splitlines():
        stripped = line.strip()

        if stripped and not line.startswith((" ", "\t")):
            break

        if stripped.startswith("provider:"):
            provider_line = stripped
            break

    if provider_line is None:
        raise RuntimeError(
            "configs/app.yaml has no llm.provider"
        )

    value = provider_line.split(":", 1)[1].strip()

    if value != "mock":
        raise RuntimeError(
            "Safety invariant failed: configs/app.yaml must remain provider: mock"
        )


PROVIDER_FACTORY_FUNCTION = r'''
def create_llm_provider(
    provider_name: str | None = None,
) -> BaseLLMProvider:
    # Default behavior remains application-config driven.
    # Explicit override is only for bounded, intentional entrypoints.
    settings = get_settings()

    if provider_name is None:
        resolved_provider = settings.llm.provider
    else:
        if not isinstance(provider_name, str):
            raise TypeError(
                "LLM provider override must be text"
            )

        resolved_provider = provider_name.strip()

        if not resolved_provider:
            raise ValueError(
                "LLM provider override cannot be blank"
            )

    registry = create_llm_registry()

    return registry.get(
        resolved_provider,
    )
'''


GATEWAY_FACTORY_FUNCTION = r'''
def create_llm_gateway(
    provider_name: str | None = None,
) -> LLMGateway:
    # provider_name=None preserves the existing application-config behavior.
    # Explicit provider_name is a construction-time override only.
    provider = create_llm_provider(
        provider_name=provider_name,
    )

    base_llm_client = LLMClient(
        provider,
    )

    observed_llm_client = ObservedLLMClient(
        base_llm_client,
    )

    return LLMGateway(
        clients={
            "openai": observed_llm_client,
        },
        router=LLMRouter(),
    )
'''


HISTORICAL_RUNTIME_FUNCTION = r'''
def create_historical_llm_runtime(
    *,
    limits: InvestigationLimits | None = None,
    provider_name: str | None = None,
) -> AgentRuntime:
    # Historical evaluation reuses the canonical Gateway/Reasoner chain,
    # while allowing an explicit non-mock provider without mutating app.yaml.
    if provider_name is None:
        resolved_provider_name = configured_llm_provider_name()
    else:
        if not isinstance(provider_name, str):
            raise TypeError(
                "Historical LLM provider override must be text"
            )

        resolved_provider_name = provider_name.strip()

        if not resolved_provider_name:
            raise HistoricalLLMRunConfigurationError(
                "Historical LLM provider override cannot be blank"
            )

    if resolved_provider_name.lower() == "mock":
        raise HistoricalLLMRunConfigurationError(
            "Real LLM historical Investigation refuses the mock provider"
        )

    resolved_limits = (
        limits
        if limits is not None
        else InvestigationLimits(
            max_iterations=6,
            max_tool_calls=10,
            timeout_seconds=30,
        )
    )

    if not isinstance(
        resolved_limits,
        InvestigationLimits,
    ):
        raise TypeError(
            "Historical LLM Investigation limits are invalid"
        )

    try:
        if provider_name is None:
            gateway = create_llm_gateway()
        else:
            gateway = create_llm_gateway(
                provider_name=resolved_provider_name,
            )
    except Exception:
        raise HistoricalLLMRunConfigurationError(
            "Shared LLM Gateway could not be constructed"
        ) from None

    adapter = InvestigationLLMGatewayAdapter(
        gateway
    )

    reasoner = LLMInvestigationReasoner(
        adapter
    )

    investigation_settings = InvestigationSettings(
        enabled=True,
        acknowledgement=(
            INVESTIGATION_ENABLE_ACKNOWLEDGEMENT
        ),
        limits=resolved_limits,
    )

    coordinator = EvidenceDrivenInvestigationCoordinator(
        reasoner=reasoner,
        probe_executor=HistoricalRuntimeProbeGuard(),
        limits=resolved_limits,
    )

    runtime = object.__new__(
        AgentRuntime
    )

    runtime.llm_gateway = gateway
    runtime.historical_llm_provider_name = (
        resolved_provider_name
    )
    runtime.investigation_settings = (
        investigation_settings
    )
    runtime.investigation_coordinator = (
        coordinator
    )

    return runtime
'''


RUN_INCIDENT_FUNCTION = r'''
async def run_real_llm_historical_incident(
    path: str | Path,
    *,
    replay_at: datetime | None = None,
    limits: InvestigationLimits | None = None,
    provider_name: str | None = None,
) -> HistoricalIncidentInvestigationResult:
    runtime = create_historical_llm_runtime(
        limits=limits,
        provider_name=provider_name,
    )

    runner = HistoricalIncidentInvestigationRunner(
        runtime
    )

    return await runner.run_file(
        path,
        replay_at=replay_at,
    )
'''


SAFE_RESULT_FUNCTION = r'''
def safe_result_payload(
    result: HistoricalIncidentInvestigationResult,
    *,
    provider_name: str | None = None,
) -> dict[str, Any]:
    if not isinstance(
        result,
        HistoricalIncidentInvestigationResult,
    ):
        raise TypeError(
            "Historical LLM Investigation result is invalid"
        )

    investigation = result.investigation
    conclusion = investigation.conclusion

    configured_provider = (
        provider_name
        if provider_name is not None
        else configured_llm_provider_name()
    )

    return {
        "schema_version": "v1",
        "run_mode": (
            "real_llm_historical_investigation"
        ),
        "configured_provider": configured_provider,
        "incident_id": result.incident_id,
        "incident_time": (
            result.incident_time.isoformat()
        ),
        "replay_at": result.replay_at.isoformat(),
        "shadow_mode": True,
        "read_only": True,
        "decision_influence": False,
        "agent": {
            "status": investigation.status.value,
            "stop_reason": (
                investigation.stop_reason.value
                if investigation.stop_reason is not None
                else None
            ),
            "iteration_count": (
                investigation.iteration_count
            ),
            "tool_call_count": (
                investigation.tool_call_count
            ),
            "attempted_probes": [
                probe.value
                for probe
                in investigation.attempted_probes
            ],
            "hypotheses": [
                item.model_dump(
                    mode="json"
                )
                for item
                in investigation.hypotheses
            ],
            "evidence": [
                item.model_dump(
                    mode="json"
                )
                for item
                in investigation.evidence
            ],
            "conclusion": (
                conclusion.model_dump(
                    mode="json"
                )
                if conclusion is not None
                else None
            ),
        },
    }
'''


ASYNC_MAIN_FUNCTION = r'''
async def _async_main(
    args,
) -> int:
    replay_at = _parse_replay_at(
        args.replay_at
    )

    result = await run_real_llm_historical_incident(
        args.incident,
        replay_at=replay_at,
        provider_name=args.provider,
    )

    payload = safe_result_payload(
        result,
        provider_name=args.provider,
    )

    output_path = Path(
        args.output
    )

    output_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    agent = payload["agent"]

    print(
        "Historical LLM Investigation completed"
    )
    print(
        f"Provider: {payload['configured_provider']}"
    )
    print(
        f"Incident: {payload['incident_id']}"
    )
    print(
        f"Status: {agent['status']}"
    )
    print(
        f"Stop reason: {agent['stop_reason']}"
    )
    print(
        "Attempted probes: "
        + ", ".join(
            agent["attempted_probes"]
        )
    )

    conclusion = agent["conclusion"]

    if conclusion is None:
        print("Conclusion: NONE")
    else:
        print(
            "Conclusion: "
            + str(
                conclusion.get(
                    "root_cause"
                )
            )
        )

    print(
        f"Result file: {output_path}"
    )

    return 0
'''


MAIN_FUNCTION = r'''
def main(
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run one real LLM historical "
            "SRE Investigation"
        )
    )

    parser.add_argument(
        "incident",
        help=(
            "Validated Real Incident Dataset JSON"
        ),
    )

    parser.add_argument(
        "--replay-at",
        default=None,
        help=(
            "Optional timezone-aware ISO-8601 "
            "point-in-time replay cutoff"
        ),
    )

    parser.add_argument(
        "--output",
        default=(
            "historical_llm_"
            "investigation_result.json"
        ),
        help=(
            "Safe Agent result JSON output path"
        ),
    )

    parser.add_argument(
        "--provider",
        default=None,
        help=(
            "Explicit non-mock provider override. "
            "Example: openai. "
            "If omitted, configs/app.yaml remains "
            "the source of truth."
        ),
    )

    args = parser.parse_args()

    try:
        return asyncio.run(
            _async_main(
                args
            )
        )
    except Exception as exc:
        print(
            f"{type(exc).__name__}: "
            f"{str(exc)}"
        )
        return 1
'''


OVERRIDE_TEST_FILE = r'''
from types import SimpleNamespace

import pytest

import services.agent_runtime.app.evaluation.real_incident.llm_run as run_module
import services.agent_runtime.app.llm.gateway.factory as gateway_factory_module
import services.agent_runtime.app.llm.provider_factory as provider_factory_module

from services.agent_runtime.app.evaluation.real_incident.llm_run import (
    HistoricalLLMRunConfigurationError,
    create_historical_llm_runtime,
)
from services.agent_runtime.app.llm.gateway.factory import (
    create_llm_gateway,
)
from services.agent_runtime.app.llm.provider_factory import (
    create_llm_provider,
)
from services.agent_runtime.app.llm.providers.mock import (
    MockProvider,
)
from services.agent_runtime.app.llm.providers.openai_compatible import (
    OpenAICompatibleProvider,
)


class NoNetworkGateway:
    async def chat(
        self,
        request,
    ):
        raise AssertionError(
            "Provider override test must not call an external LLM"
        )


def mock_settings():
    return SimpleNamespace(
        llm=SimpleNamespace(
            provider="mock"
        )
    )


def test_default_provider_behavior_remains_mock(
    monkeypatch,
):
    monkeypatch.setattr(
        provider_factory_module,
        "get_settings",
        mock_settings,
    )

    provider = create_llm_provider()

    assert isinstance(
        provider,
        MockProvider,
    )


def test_explicit_provider_override_selects_openai(
    monkeypatch,
):
    monkeypatch.setattr(
        provider_factory_module,
        "get_settings",
        mock_settings,
    )

    provider = create_llm_provider(
        provider_name="openai"
    )

    assert isinstance(
        provider,
        OpenAICompatibleProvider,
    )

    assert (
        mock_settings().llm.provider
        == "mock"
    )


def test_gateway_factory_forwards_override(
    monkeypatch,
):
    captured = []

    def fake_provider_factory(
        provider_name=None,
    ):
        captured.append(
            provider_name
        )
        return MockProvider()

    monkeypatch.setattr(
        gateway_factory_module,
        "create_llm_provider",
        fake_provider_factory,
    )

    gateway = create_llm_gateway(
        provider_name="openai"
    )

    assert captured == [
        "openai"
    ]

    assert gateway is not None


def test_historical_runtime_override_uses_openai(
    monkeypatch,
):
    captured = []

    monkeypatch.setattr(
        run_module,
        "get_settings",
        mock_settings,
    )

    def fake_gateway_factory(
        provider_name=None,
    ):
        captured.append(
            provider_name
        )
        return NoNetworkGateway()

    monkeypatch.setattr(
        run_module,
        "create_llm_gateway",
        fake_gateway_factory,
    )

    runtime = create_historical_llm_runtime(
        provider_name="openai"
    )

    assert captured == [
        "openai"
    ]

    assert (
        runtime.historical_llm_provider_name
        == "openai"
    )


def test_explicit_mock_override_fails_before_gateway(
    monkeypatch,
):
    calls = 0

    monkeypatch.setattr(
        run_module,
        "get_settings",
        mock_settings,
    )

    def forbidden_gateway(
        provider_name=None,
    ):
        nonlocal calls
        calls += 1
        raise AssertionError(
            "Mock override must fail before Gateway construction"
        )

    monkeypatch.setattr(
        run_module,
        "create_llm_gateway",
        forbidden_gateway,
    )

    with pytest.raises(
        HistoricalLLMRunConfigurationError,
        match="refuses the mock provider",
    ):
        create_historical_llm_runtime(
            provider_name="mock"
        )

    assert calls == 0


@pytest.mark.parametrize(
    "provider_name",
    [
        "",
        "   ",
    ],
)
def test_blank_override_fails_closed(
    provider_name,
):
    with pytest.raises(
        HistoricalLLMRunConfigurationError,
        match="cannot be blank",
    ):
        create_historical_llm_runtime(
            provider_name=provider_name
        )
'''


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after = root / AFTER_NAME
    error = root / ERROR_NAME

    for output in (
        after,
        error,
    ):
        try:
            output.unlink()
        except FileNotFoundError:
            pass

    provider_factory = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "llm"
        / "provider_factory.py"
    )

    gateway_factory = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "llm"
        / "gateway"
        / "factory.py"
    )

    llm_run = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "evaluation"
        / "real_incident"
        / "llm_run.py"
    )

    override_test = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_llm_provider_override.py"
    )

    app_yaml = (
        root
        / "configs"
        / "app.yaml"
    )

    for required in (
        provider_factory,
        gateway_factory,
        llm_run,
        app_yaml,
    ):
        if not required.exists():
            raise RuntimeError(
                f"Required file missing: {required}"
            )

    report = [
        "Real LLM Explicit Provider Override v2",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Installer strategy:",
        "- AST function-boundary replacement",
        "- configs/app.yaml stays provider=mock",
        "- no external LLM request",
        "- focused tests included",
    ]

    backups: list[
        tuple[Path, Path]
    ] = []

    test_preexisted = (
        override_test.exists()
    )

    try:
        section(
            report,
            "PRE-INSTALL SAFETY",
        )

        verify_app_yaml_mock(
            app_yaml
        )

        report.append(
            "configs/app.yaml provider=mock"
        )

        for target in (
            provider_factory,
            gateway_factory,
            llm_run,
        ):
            backup = backup_file(
                target
            )

            backups.append(
                (
                    target,
                    backup,
                )
            )

            report.append(
                "backup="
                + str(
                    backup.relative_to(root)
                )
            )

        if test_preexisted:
            backup = backup_file(
                override_test
            )

            backups.append(
                (
                    override_test,
                    backup,
                )
            )

        replace_top_level_function(
            provider_factory,
            "create_llm_provider",
            PROVIDER_FACTORY_FUNCTION,
        )

        replace_top_level_function(
            gateway_factory,
            "create_llm_gateway",
            GATEWAY_FACTORY_FUNCTION,
        )

        replace_top_level_function(
            llm_run,
            "create_historical_llm_runtime",
            HISTORICAL_RUNTIME_FUNCTION,
        )

        replace_top_level_function(
            llm_run,
            "run_real_llm_historical_incident",
            RUN_INCIDENT_FUNCTION,
        )

        replace_top_level_function(
            llm_run,
            "safe_result_payload",
            SAFE_RESULT_FUNCTION,
        )

        replace_top_level_function(
            llm_run,
            "_async_main",
            ASYNC_MAIN_FUNCTION,
        )

        replace_top_level_function(
            llm_run,
            "main",
            MAIN_FUNCTION,
        )

        write_text(
            override_test,
            OVERRIDE_TEST_FILE.strip("\n")
            + "\n",
        )

        verify_app_yaml_mock(
            app_yaml
        )

        section(
            report,
            "POST-INSTALL SAFETY",
        )

        report.append(
            "configs/app.yaml provider=mock"
        )

        syntax_targets = [
            provider_factory,
            gateway_factory,
            llm_run,
            override_test,
        ]

        syntax = run_command(
            root,
            "Python syntax",
            [
                "uv",
                "run",
                "python",
                "-m",
                "py_compile",
                *[
                    str(
                        path.relative_to(root)
                    )
                    for path in syntax_targets
                ],
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

        preflight = run_command(
            root,
            "Explicit OpenAI-compatible provider construction preflight",
            [
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from services.agent_runtime.app.llm.provider_factory "
                    "import create_llm_provider; "
                    "p=create_llm_provider(provider_name='openai'); "
                    "print('provider='+p.name); "
                    "print('base_url='+p.base_url); "
                    "print('model='+p.model); "
                    "print('api_key_present='+str(bool(p.api_key)))"
                ),
            ],
        )

        add_command(
            report,
            preflight,
        )

        if preflight.returncode != 0:
            raise RuntimeError(
                "Explicit provider preflight failed"
            )

        test_files = [
            "services/agent_runtime/tests/test_llm_provider_override.py",
            "services/agent_runtime/tests/test_real_llm_historical_run.py",
            "services/agent_runtime/tests/test_historical_incident_investigation_runner.py",
            "services/agent_runtime/tests/test_investigation_reasoner.py",
            "services/agent_runtime/tests/test_llm_config.py",
        ]

        tests = run_command(
            root,
            "Focused provider override + Historical LLM tests",
            [
                "uv",
                "run",
                "pytest",
                *test_files,
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

        diff = run_command(
            root,
            "Git diff",
            [
                "git",
                "diff",
                "--",
                str(
                    provider_factory.relative_to(root)
                ),
                str(
                    gateway_factory.relative_to(root)
                ),
                str(
                    llm_run.relative_to(root)
                ),
                str(
                    override_test.relative_to(root)
                ),
            ],
        )

        add_command(
            report,
            diff,
        )

        section(
            report,
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "Safe default preserved:",
                "configs/app.yaml provider=mock",
                "",
                "Explicit intentional real-run path:",
                "llm_run.py --provider openai",
                "",
                "OpenAI-compatible environment contract:",
                "OPENAI_BASE_URL",
                "OPENAI_API_KEY",
                "OPENAI_MODEL",
                "",
                "No external LLM request was made by this installer.",
            ]
        )

        write_text(
            after,
            "\n".join(report) + "\n",
        )

        print("=" * 72)
        print("REAL LLM PROVIDER OVERRIDE V2 PASSED")
        print("=" * 72)
        print("")
        print("Safe default preserved: provider=mock")
        print("Explicit real-run override: --provider openai")
        print("")
        print("Upload:")
        print(after)

        return 0

    except Exception as exc:
        rollback: list[str] = []

        for original, backup in reversed(
            backups
        ):
            try:
                shutil.copy2(
                    backup,
                    original,
                )

                rollback.append(
                    "RESTORED "
                    + str(
                        original.relative_to(root)
                    )
                )
            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK FAILED "
                    + str(
                        original.relative_to(root)
                    )
                    + ": "
                    + f"{type(rollback_exc).__name__}: {rollback_exc}"
                )

        if (
            not test_preexisted
            and override_test.exists()
        ):
            try:
                override_test.unlink()

                rollback.append(
                    "REMOVED newly-created "
                    + str(
                        override_test.relative_to(root)
                    )
                )
            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK FAILED removing new test: "
                    + f"{type(rollback_exc).__name__}: {rollback_exc}"
                )

        error_lines = [
            "Real LLM Explicit Provider Override v2 FAILED",
            f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
            "",
            "Exception:",
            f"{type(exc).__name__}: {exc}",
            "",
            "Traceback:",
            traceback.format_exc(),
            "",
            "ROLLBACK",
            "=" * 120,
            *rollback,
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
        print("REAL LLM PROVIDER OVERRIDE V2 FAILED")
        print("=" * 72)
        print("")
        print("Modified files were rolled back where possible.")
        print("")
        print("Upload:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
