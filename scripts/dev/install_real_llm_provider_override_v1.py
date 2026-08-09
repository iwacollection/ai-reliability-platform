from __future__ import annotations

import re
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCRIPT_VERSION = "real-llm-provider-override-v1"
AFTER_NAME = "real_llm_provider_override_v1_after.txt"
ERROR_NAME = "real_llm_provider_override_v1_error.txt"


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
    )


def write_text(path: Path, text: str) -> None:
    normalized = (
        text
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )

    path.write_text(
        normalized,
        encoding="utf-8",
        newline="\n",
    )


def backup_file(path: Path) -> Path:
    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup = path.with_name(
        f"{path.name}.before_{SCRIPT_VERSION}_{timestamp}.bak"
    )

    shutil.copy2(
        path,
        backup,
    )

    return backup


def replace_once(
    text: str,
    old: str,
    new: str,
    *,
    label: str,
) -> str:
    count = text.count(
        old
    )

    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one anchor, found {count}"
        )

    return text.replace(
        old,
        new,
        1,
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


def add_section(
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


def add_command_result(
    lines: list[str],
    result: CommandResult,
) -> None:
    add_section(
        lines,
        f"COMMAND: {result.name}",
    )

    lines.append(
        " ".join(
            result.command
        )
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


def patch_provider_factory(
    path: Path,
) -> None:
    text = read_text(
        path
    )

    old = '''def create_llm_provider() -> BaseLLMProvider:
    """
    Create LLM provider from application config.
    """

    settings = get_settings()

    registry = create_llm_registry()

    provider = registry.get(
        settings.llm.provider,
    )

    return provider
'''

    new = '''def create_llm_provider(
    provider_name: str | None = None,
) -> BaseLLMProvider:
    """
    Create an LLM provider.

    Default behavior remains unchanged:
    when provider_name is None, settings.llm.provider is used.

    An explicit provider override is intended for bounded entrypoints such
    as historical real-LLM evaluation. It does not mutate Settings or
    configs/app.yaml.
    """

    settings = get_settings()

    if provider_name is None:
        resolved_provider = (
            settings.llm.provider
        )
    else:
        if not isinstance(
            provider_name,
            str,
        ):
            raise TypeError(
                "LLM provider override must be text"
            )

        resolved_provider = (
            provider_name.strip()
        )

        if not resolved_provider:
            raise ValueError(
                "LLM provider override cannot be blank"
            )

    registry = create_llm_registry()

    provider = registry.get(
        resolved_provider,
    )

    return provider
'''

    text = replace_once(
        text,
        old,
        new,
        label="provider_factory.py",
    )

    write_text(
        path,
        text,
    )


def patch_gateway_factory(
    path: Path,
) -> None:
    text = read_text(
        path
    )

    text = replace_once(
        text,
        "def create_llm_gateway() -> LLMGateway:\n",
        (
            "def create_llm_gateway(\n"
            "    provider_name: str | None = None,\n"
            ") -> LLMGateway:\n"
        ),
        label="gateway factory signature",
    )

    text = replace_once(
        text,
        "    provider = create_llm_provider()\n",
        (
            "    provider = create_llm_provider(\n"
            "        provider_name=provider_name,\n"
            "    )\n"
        ),
        label="gateway provider construction",
    )

    old_doc = '''    The provider registration key intentionally remains "openai" in this
    stage to preserve the existing routing contract exactly.
'''

    new_doc = '''    The provider registration key intentionally remains "openai" in this
    stage to preserve the existing routing contract exactly.

    provider_name is a construction-time override only. When it is None,
    application configuration remains the source of truth.
'''

    text = replace_once(
        text,
        old_doc,
        new_doc,
        label="gateway factory documentation",
    )

    write_text(
        path,
        text,
    )


def patch_llm_run(
    path: Path,
) -> None:
    text = read_text(
        path
    )

    text = replace_once(
        text,
        '''def create_historical_llm_runtime(
    *,
    limits: InvestigationLimits | None = None,
) -> AgentRuntime:
''',
        '''def create_historical_llm_runtime(
    *,
    limits: InvestigationLimits | None = None,
    provider_name: str | None = None,
) -> AgentRuntime:
''',
        label="historical runtime signature",
    )

    text = replace_once(
        text,
        '''    provider_name = (
        configured_llm_provider_name()
    )

    if (
        provider_name.strip().lower()
        == "mock"
    ):
        raise HistoricalLLMRunConfigurationError(
            "Real LLM historical Investigation refuses the mock provider"
        )
''',
        '''    if provider_name is None:
        resolved_provider_name = (
            configured_llm_provider_name()
        )
    else:
        if not isinstance(
            provider_name,
            str,
        ):
            raise TypeError(
                "Historical LLM provider override must be text"
            )

        resolved_provider_name = (
            provider_name.strip()
        )

        if not resolved_provider_name:
            raise HistoricalLLMRunConfigurationError(
                "Historical LLM provider override cannot be blank"
            )

    if (
        resolved_provider_name.lower()
        == "mock"
    ):
        raise HistoricalLLMRunConfigurationError(
            "Real LLM historical Investigation refuses the mock provider"
        )
''',
        label="historical provider resolution",
    )

    text = replace_once(
        text,
        '''    try:
        gateway = create_llm_gateway()

    except Exception:
        raise HistoricalLLMRunConfigurationError(
            "Shared LLM Gateway could not be constructed"
        ) from None
''',
        '''    try:
        if provider_name is None:
            gateway = create_llm_gateway()
        else:
            gateway = create_llm_gateway(
                provider_name=(
                    resolved_provider_name
                )
            )

    except Exception:
        raise HistoricalLLMRunConfigurationError(
            "Shared LLM Gateway could not be constructed"
        ) from None
''',
        label="historical gateway construction",
    )

    text = replace_once(
        text,
        '''    runtime.llm_gateway = gateway

    runtime.investigation_settings = (
''',
        '''    runtime.llm_gateway = gateway

    runtime.historical_llm_provider_name = (
        resolved_provider_name
    )

    runtime.investigation_settings = (
''',
        label="historical runtime provider identity",
    )

    text = replace_once(
        text,
        '''async def run_real_llm_historical_incident(
    path: str | Path,
    *,
    replay_at: datetime | None = None,
    limits: InvestigationLimits | None = None,
) -> HistoricalIncidentInvestigationResult:
''',
        '''async def run_real_llm_historical_incident(
    path: str | Path,
    *,
    replay_at: datetime | None = None,
    limits: InvestigationLimits | None = None,
    provider_name: str | None = None,
) -> HistoricalIncidentInvestigationResult:
''',
        label="historical run signature",
    )

    text = replace_once(
        text,
        '''    runtime = (
        create_historical_llm_runtime(
            limits=limits,
        )
    )
''',
        '''    runtime = (
        create_historical_llm_runtime(
            limits=limits,
            provider_name=provider_name,
        )
    )
''',
        label="historical runtime call",
    )

    text = replace_once(
        text,
        '''def safe_result_payload(
    result: HistoricalIncidentInvestigationResult,
) -> dict[str, Any]:
''',
        '''def safe_result_payload(
    result: HistoricalIncidentInvestigationResult,
    *,
    provider_name: str | None = None,
) -> dict[str, Any]:
''',
        label="safe result signature",
    )

    text = replace_once(
        text,
        '''        "configured_provider": (
            configured_llm_provider_name()
        ),
''',
        '''        "configured_provider": (
            provider_name
            if provider_name is not None
            else configured_llm_provider_name()
        ),
''',
        label="safe result provider",
    )

    text = replace_once(
        text,
        '''    result = await (
        run_real_llm_historical_incident(
            args.incident,
            replay_at=replay_at,
        )
    )

    payload = safe_result_payload(
        result
    )
''',
        '''    result = await (
        run_real_llm_historical_incident(
            args.incident,
            replay_at=replay_at,
            provider_name=args.provider,
        )
    )

    payload = safe_result_payload(
        result,
        provider_name=args.provider,
    )
''',
        label="CLI provider propagation",
    )

    text = replace_once(
        text,
        '''    parser.add_argument(
        "--output",
        default=(
            "historical_llm_"
            "investigation_result.json"
        ),
        help=(
            "Safe Agent result JSON output path"
        ),
    )

    args = parser.parse_args()
''',
        '''    parser.add_argument(
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
            "If omitted, configs/app.yaml remains the source of truth."
        ),
    )

    args = parser.parse_args()
''',
        label="CLI provider argument",
    )

    write_text(
        path,
        text,
    )


def write_override_tests(
    path: Path,
) -> None:
    content = r'''from types import SimpleNamespace

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
            "Provider override composition test must not call network"
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


def test_explicit_provider_override_selects_openai_without_mutating_settings(
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


def test_gateway_factory_forwards_explicit_provider_override(
    monkeypatch,
):
    captured = []

    def provider_factory(
        provider_name=None,
    ):
        captured.append(
            provider_name
        )

        return MockProvider()

    monkeypatch.setattr(
        gateway_factory_module,
        "create_llm_provider",
        provider_factory,
    )

    gateway = create_llm_gateway(
        provider_name="openai"
    )

    assert captured == [
        "openai"
    ]

    assert gateway is not None


def test_historical_runtime_explicit_override_bypasses_mock_app_default(
    monkeypatch,
):
    captured = []

    monkeypatch.setattr(
        run_module,
        "get_settings",
        mock_settings,
    )

    def gateway_factory(
        provider_name=None,
    ):
        captured.append(
            provider_name
        )

        return NoNetworkGateway()

    monkeypatch.setattr(
        run_module,
        "create_llm_gateway",
        gateway_factory,
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

    assert (
        runtime.investigation_settings.enabled
        is True
    )


def test_historical_runtime_rejects_explicit_mock_before_gateway_creation(
    monkeypatch,
):
    gateway_calls = 0

    monkeypatch.setattr(
        run_module,
        "get_settings",
        mock_settings,
    )

    def forbidden_gateway(
        provider_name=None,
    ):
        nonlocal gateway_calls

        gateway_calls += 1

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

    assert gateway_calls == 0


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
    ],
)
def test_historical_runtime_rejects_blank_provider_override(
    value,
):
    with pytest.raises(
        HistoricalLLMRunConfigurationError,
        match="cannot be blank",
    ):
        create_historical_llm_runtime(
            provider_name=value
        )
'''

    write_text(
        path,
        content,
    )


def verify_app_yaml_still_mock(
    path: Path,
) -> str:
    text = read_text(
        path
    )

    llm_match = re.search(
        r"(?ms)^llm:\s*\n(?P<body>.*?)(?=^[A-Za-z_][A-Za-z0-9_]*:\s*$|\Z)",
        text,
    )

    if llm_match is None:
        raise RuntimeError(
            "Could not locate llm section in configs/app.yaml"
        )

    provider_match = re.search(
        r"(?m)^\s*provider:\s*([A-Za-z0-9_.-]+)\s*$",
        llm_match.group("body"),
    )

    if provider_match is None:
        raise RuntimeError(
            "Could not locate llm.provider in configs/app.yaml"
        )

    provider = provider_match.group(1)

    if provider != "mock":
        raise RuntimeError(
            "Safety invariant failed: configs/app.yaml must remain provider: mock"
        )

    return provider


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after_path = root / AFTER_NAME
    error_path = root / ERROR_NAME

    for output_path in (
        after_path,
        error_path,
    ):
        try:
            output_path.unlink()
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

    app_yaml = root / "configs" / "app.yaml"

    required = [
        provider_factory,
        gateway_factory,
        llm_run,
        app_yaml,
    ]

    for path in required:
        if not path.exists():
            raise RuntimeError(
                f"Required file is missing: {path}"
            )

    report = [
        "Real LLM Explicit Provider Override v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Goal:",
        "- keep configs/app.yaml provider=mock as safe default",
        "- allow explicit non-mock provider only for intentional real runs",
        "- run focused tests with zero intentional external LLM requests",
    ]

    backups: list[tuple[Path, Path]] = []
    created_test = not override_test.exists()

    try:
        add_section(
            report,
            "PRE-INSTALL SAFETY",
        )

        default_provider = verify_app_yaml_still_mock(
            app_yaml
        )

        report.append(
            f"configs/app.yaml llm.provider={default_provider}"
        )

        for path in (
            provider_factory,
            gateway_factory,
            llm_run,
        ):
            backup = backup_file(
                path
            )

            backups.append(
                (
                    path,
                    backup,
                )
            )

            report.append(
                f"backup={backup.relative_to(root)}"
            )

        if override_test.exists():
            backup = backup_file(
                override_test
            )

            backups.append(
                (
                    override_test,
                    backup,
                )
            )

        patch_provider_factory(
            provider_factory
        )

        patch_gateway_factory(
            gateway_factory
        )

        patch_llm_run(
            llm_run
        )

        write_override_tests(
            override_test
        )

        add_section(
            report,
            "POST-INSTALL SAFETY",
        )

        default_provider = verify_app_yaml_still_mock(
            app_yaml
        )

        report.append(
            f"configs/app.yaml llm.provider={default_provider}"
        )

        syntax_result = run_command(
            root,
            "Python syntax",
            [
                "uv",
                "run",
                "python",
                "-m",
                "py_compile",
                str(provider_factory.relative_to(root)),
                str(gateway_factory.relative_to(root)),
                str(llm_run.relative_to(root)),
                str(override_test.relative_to(root)),
            ],
        )

        add_command_result(
            report,
            syntax_result,
        )

        if syntax_result.returncode != 0:
            raise RuntimeError(
                "Python syntax verification failed"
            )

        preflight_result = run_command(
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

        add_command_result(
            report,
            preflight_result,
        )

        if preflight_result.returncode != 0:
            raise RuntimeError(
                "Explicit provider construction preflight failed"
            )

        test_files = [
            "services/agent_runtime/tests/test_llm_provider_override.py",
            "services/agent_runtime/tests/test_real_llm_historical_run.py",
            "services/agent_runtime/tests/test_historical_incident_investigation_runner.py",
            "services/agent_runtime/tests/test_investigation_reasoner.py",
            "services/agent_runtime/tests/test_llm_config.py",
            "services/agent_runtime/tests/test_openai_provider.py",
        ]

        tests_result = run_command(
            root,
            "Focused provider override + historical LLM compatibility tests",
            [
                "uv",
                "run",
                "pytest",
                *test_files,
                "-q",
            ],
        )

        add_command_result(
            report,
            tests_result,
        )

        if tests_result.returncode != 0:
            raise RuntimeError(
                "Focused provider override tests failed"
            )

        diff_result = run_command(
            root,
            "Git diff",
            [
                "git",
                "diff",
                "--",
                str(provider_factory.relative_to(root)),
                str(gateway_factory.relative_to(root)),
                str(llm_run.relative_to(root)),
                str(override_test.relative_to(root)),
            ],
        )

        add_command_result(
            report,
            diff_result,
        )

        add_section(
            report,
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "Architecture after this stage:",
                "",
                "normal local/runtime/test path:",
                "configs/app.yaml provider=mock",
                "    -> create_llm_gateway()",
                "    -> MockProvider",
                "",
                "explicit real historical path:",
                "--provider openai",
                "    -> create_llm_gateway(provider_name='openai')",
                "    -> OpenAICompatibleProvider",
                "",
                "Verified:",
                "- global app.yaml default remained mock",
                "- default provider factory behavior remained unchanged",
                "- explicit provider override selects openai",
                "- override does not mutate Settings/app.yaml",
                "- explicit mock override fails closed",
                "- Historical LLM Runtime can use explicit override",
                "- no intentional external LLM request was made",
                "",
                "Existing OpenAI-compatible environment contract:",
                "- OPENAI_BASE_URL",
                "- OPENAI_API_KEY",
                "- OPENAI_MODEL",
            ]
        )

        write_text(
            after_path,
            "\n".join(report) + "\n",
        )

        print("=" * 72)
        print(
            "REAL LLM PROVIDER OVERRIDE V1 PASSED"
        )
        print("=" * 72)
        print("")
        print(
            "Safe default preserved: configs/app.yaml provider=mock"
        )
        print(
            "Real-run override available: --provider openai"
        )
        print("")
        print("Upload:")
        print(after_path)

        return 0

    except Exception as exc:
        rollback_messages = []

        for original, backup in reversed(
            backups
        ):
            try:
                shutil.copy2(
                    backup,
                    original,
                )

                rollback_messages.append(
                    f"RESTORED {original.relative_to(root)}"
                )

            except Exception as rollback_exc:
                rollback_messages.append(
                    "ROLLBACK FAILED "
                    f"{original.relative_to(root)}: "
                    f"{type(rollback_exc).__name__}: {rollback_exc}"
                )

        if created_test and override_test.exists():
            try:
                override_test.unlink()

                rollback_messages.append(
                    "REMOVED newly-created "
                    + str(
                        override_test.relative_to(root)
                    )
                )

            except Exception as rollback_exc:
                rollback_messages.append(
                    "ROLLBACK FAILED removing new test: "
                    f"{type(rollback_exc).__name__}: {rollback_exc}"
                )

        error_lines = [
            "Real LLM Explicit Provider Override v1 FAILED",
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
            *rollback_messages,
            "",
            "PARTIAL REPORT",
            "=" * 120,
            *report,
        ]

        write_text(
            error_path,
            "\n".join(error_lines) + "\n",
        )

        print("=" * 72)
        print(
            "REAL LLM PROVIDER OVERRIDE V1 FAILED"
        )
        print("=" * 72)
        print("")
        print(
            "Production files were rolled back where possible."
        )
        print("")
        print("Upload:")
        print(error_path)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
