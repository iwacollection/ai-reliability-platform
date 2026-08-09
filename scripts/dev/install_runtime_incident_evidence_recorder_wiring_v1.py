from __future__ import annotations

import ast
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "runtime-incident-evidence-recorder-wiring-v1"
AFTER_NAME = "runtime_incident_evidence_recorder_wiring_v1_after.txt"
ERROR_NAME = "runtime_incident_evidence_recorder_wiring_v1_error.txt"

SETTINGS_SOURCE = 'from __future__ import annotations\n\nimport os\nfrom collections.abc import Mapping\nfrom pathlib import Path\nfrom typing import Any\n\nfrom pydantic import BaseModel, ConfigDict, Field, model_validator\n\n\nINCIDENT_EVIDENCE_ENABLE_ACKNOWLEDGEMENT = (\n    "I_ACKNOWLEDGE_READ_ONLY_PRODUCTION_INCIDENT_EVIDENCE_CAPTURE"\n)\n\nDEFAULT_INCIDENT_EVIDENCE_OUTPUT_DIR = (\n    "evaluation_data/production_incident_captures"\n)\n\n\nclass IncidentEvidenceRecorderConfigurationError(RuntimeError):\n    pass\n\n\nclass IncidentEvidenceRecorderSettings(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    enabled: bool = False\n    acknowledgement: str | None = None\n    output_dir: str = Field(\n        default=DEFAULT_INCIDENT_EVIDENCE_OUTPUT_DIR,\n        min_length=1,\n        max_length=512,\n    )\n\n    @model_validator(mode="after")\n    def validate_configuration(self):\n        if (\n            self.enabled\n            and self.acknowledgement\n            != INCIDENT_EVIDENCE_ENABLE_ACKNOWLEDGEMENT\n        ):\n            raise ValueError(\n                "enabled recorder requires exact acknowledgement"\n            )\n\n        path = Path(self.output_dir)\n\n        if path.is_absolute() or ".." in path.parts:\n            raise ValueError(\n                "recorder output directory must be repository-relative"\n            )\n\n        return self\n\n    @classmethod\n    def from_environment(\n        cls,\n        environment: Mapping[str, str] | None = None,\n    ) -> "IncidentEvidenceRecorderSettings":\n        source = (\n            environment\n            if environment is not None\n            else os.environ\n        )\n\n        try:\n            return cls(\n                enabled=_parse_bool(\n                    source.get(\n                        "AGENT_INCIDENT_EVIDENCE_RECORDER_ENABLED"\n                    ),\n                    default=False,\n                ),\n                acknowledgement=_optional_text(\n                    source.get(\n                        "AGENT_INCIDENT_EVIDENCE_RECORDER_ACKNOWLEDGEMENT"\n                    )\n                ),\n                output_dir=(\n                    _optional_text(\n                        source.get(\n                            "AGENT_INCIDENT_EVIDENCE_RECORDER_OUTPUT_DIR"\n                        )\n                    )\n                    or DEFAULT_INCIDENT_EVIDENCE_OUTPUT_DIR\n                ),\n            )\n        except Exception:\n            raise IncidentEvidenceRecorderConfigurationError(\n                "Incident Evidence Recorder configuration is invalid"\n            ) from None\n\n    def resolve_output_dir(self) -> Path:\n        repository_root = (\n            Path(__file__).resolve().parents[4]\n        )\n\n        resolved = (\n            repository_root\n            / self.output_dir\n        ).resolve()\n\n        try:\n            resolved.relative_to(\n                repository_root\n            )\n        except ValueError:\n            raise IncidentEvidenceRecorderConfigurationError(\n                "Incident Evidence Recorder output directory is invalid"\n            ) from None\n\n        return resolved\n\n\ndef _parse_bool(\n    value: Any,\n    *,\n    default: bool,\n) -> bool:\n    if value is None:\n        return default\n\n    if isinstance(value, bool):\n        return value\n\n    if not isinstance(value, str):\n        raise ValueError(\n            "boolean environment value is invalid"\n        )\n\n    normalized = value.strip().lower()\n\n    if normalized == "true":\n        return True\n\n    if normalized == "false":\n        return False\n\n    raise ValueError(\n        "boolean environment value is invalid"\n    )\n\n\ndef _optional_text(\n    value: Any,\n) -> str | None:\n    if value is None:\n        return None\n\n    if not isinstance(value, str):\n        raise ValueError(\n            "environment text value is invalid"\n        )\n\n    normalized = value.strip()\n\n    return normalized if normalized else None\n\n\n__all__ = [\n    "DEFAULT_INCIDENT_EVIDENCE_OUTPUT_DIR",\n    "INCIDENT_EVIDENCE_ENABLE_ACKNOWLEDGEMENT",\n    "IncidentEvidenceRecorderConfigurationError",\n    "IncidentEvidenceRecorderSettings",\n]\n'
INIT_SOURCE = 'from services.agent_runtime.app.incident_evidence.recorder import (\n    ProductionIncidentEvidenceRecordResult,\n    ProductionIncidentEvidenceRecorder,\n    ProductionIncidentEvidenceRecorderError,\n    ProductionIncidentEvidenceScopeError,\n    ProductionIncidentEvidenceUnavailableError,\n)\nfrom services.agent_runtime.app.incident_evidence.settings import (\n    DEFAULT_INCIDENT_EVIDENCE_OUTPUT_DIR,\n    INCIDENT_EVIDENCE_ENABLE_ACKNOWLEDGEMENT,\n    IncidentEvidenceRecorderConfigurationError,\n    IncidentEvidenceRecorderSettings,\n)\n\n\n__all__ = [\n    "DEFAULT_INCIDENT_EVIDENCE_OUTPUT_DIR",\n    "INCIDENT_EVIDENCE_ENABLE_ACKNOWLEDGEMENT",\n    "IncidentEvidenceRecorderConfigurationError",\n    "IncidentEvidenceRecorderSettings",\n    "ProductionIncidentEvidenceRecordResult",\n    "ProductionIncidentEvidenceRecorder",\n    "ProductionIncidentEvidenceRecorderError",\n    "ProductionIncidentEvidenceScopeError",\n    "ProductionIncidentEvidenceUnavailableError",\n]\n'
TEST_SOURCE = 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\nfrom types import SimpleNamespace\nfrom uuid import UUID\n\nimport pytest\n\nfrom common.domain.event import Header, Resource, Signal, StandardEvent\nfrom common.domain.event.enums import (\n    EventSource,\n    ResourceKind,\n    Severity,\n    SignalType,\n)\n\nimport services.agent_runtime.app.runtime.runtime as runtime_module\n\nfrom services.agent_runtime.app.incident_evidence.settings import (\n    INCIDENT_EVIDENCE_ENABLE_ACKNOWLEDGEMENT,\n    IncidentEvidenceRecorderConfigurationError,\n    IncidentEvidenceRecorderSettings,\n)\nfrom services.agent_runtime.app.model.context import AgentContext\nfrom services.agent_runtime.app.runtime.runtime import AgentRuntime\n\n\nNOW = datetime(\n    2026,\n    8,\n    10,\n    8,\n    20,\n    tzinfo=UTC,\n)\n\n\ndef event() -> StandardEvent:\n    return StandardEvent(\n        header=Header(\n            event_id=UUID(\n                "11111111-1111-4111-8111-111111111111"\n            ),\n            trace_id=UUID(\n                "22222222-2222-4222-8222-222222222222"\n            ),\n            source=EventSource.ALERTMANAGER,\n            occurred_at=NOW,\n        ),\n        signal=Signal(\n            type=SignalType.ALERT,\n            name="PodOOMKilled",\n            severity=Severity.CRITICAL,\n            message="payment-api restarted",\n            labels={},\n        ),\n        resources=[\n            Resource(\n                kind=ResourceKind.POD,\n                name="payment-api",\n                namespace="payment",\n                cluster="production-a",\n            )\n        ],\n    )\n\n\nclass Pipeline:\n    def __init__(self, order):\n        self.order = order\n\n    async def execute(self, context):\n        self.order.append("pipeline")\n\n        assert (\n            "incident_evidence_recorder"\n            not in context.metadata\n        )\n\n        return [\n            "authoritative-pipeline-result"\n        ]\n\n\nclass Tools:\n    pass\n\n\ndef lightweight_runtime(order) -> AgentRuntime:\n    value = object.__new__(\n        AgentRuntime\n    )\n    value.pipeline = Pipeline(order)\n    value.tools = Tools()\n    value.investigation_coordinator = None\n    return value\n\n\ndef context(value) -> AgentContext:\n    return AgentContext(\n        request_id="request-001",\n        event=event(),\n        tools=value.tools,\n        metadata={},\n    )\n\n\ndef test_settings_default_disabled():\n    settings = (\n        IncidentEvidenceRecorderSettings\n        .from_environment({})\n    )\n\n    assert settings.enabled is False\n    assert settings.acknowledgement is None\n\n\ndef test_enabled_requires_acknowledgement():\n    with pytest.raises(\n        IncidentEvidenceRecorderConfigurationError,\n        match="configuration is invalid",\n    ):\n        IncidentEvidenceRecorderSettings.from_environment(\n            {\n                "AGENT_INCIDENT_EVIDENCE_RECORDER_ENABLED": "true",\n                "AGENT_INCIDENT_EVIDENCE_RECORDER_ACKNOWLEDGEMENT": "wrong",\n            }\n        )\n\n\n@pytest.mark.parametrize(\n    "output_dir",\n    [\n        "../outside",\n        "C:/outside",\n    ],\n)\ndef test_output_dir_fails_closed(\n    output_dir,\n):\n    with pytest.raises(\n        IncidentEvidenceRecorderConfigurationError,\n        match="configuration is invalid",\n    ):\n        IncidentEvidenceRecorderSettings.from_environment(\n            {\n                "AGENT_INCIDENT_EVIDENCE_RECORDER_OUTPUT_DIR": output_dir,\n            }\n        )\n\n\n@pytest.mark.asyncio\nasync def test_disabled_does_not_construct_recorder(\n    monkeypatch,\n):\n    order = []\n    value = lightweight_runtime(order)\n    ctx = context(value)\n\n    constructions = 0\n\n    def forbidden_recorder(*args, **kwargs):\n        nonlocal constructions\n        constructions += 1\n        raise AssertionError(\n            "disabled recorder must not be constructed"\n        )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "ProductionIncidentEvidenceRecorder",\n        forbidden_recorder,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "IncidentEvidenceRecorderSettings",\n        SimpleNamespace(\n            from_environment=lambda: (\n                SimpleNamespace(\n                    enabled=False\n                )\n            )\n        ),\n    )\n\n    result = await value.execute(ctx)\n\n    assert result == [\n        "authoritative-pipeline-result"\n    ]\n    assert order == ["pipeline"]\n    assert constructions == 0\n    assert (\n        "incident_evidence_recorder"\n        not in ctx.metadata\n    )\n\n\n@pytest.mark.asyncio\nasync def test_enabled_runs_after_pipeline_with_isolated_context(\n    monkeypatch,\n    tmp_path,\n):\n    order = []\n    value = lightweight_runtime(order)\n    ctx = context(value)\n    original_event = ctx.event\n    captured = {}\n\n    class Recorder:\n        def __init__(self, output_dir):\n            assert output_dir == (\n                tmp_path / "captures"\n            )\n\n        async def record(self, recorder_context):\n            order.append("recorder")\n            captured["context"] = (\n                recorder_context\n            )\n\n            return SimpleNamespace(\n                incident_id=(\n                    "capture-11111111-1111-4111-8111-111111111111"\n                ),\n                path=(\n                    tmp_path\n                    / "captures"\n                    / "capture-111.replay.json"\n                ),\n                created=True,\n                observation_count=4,\n            )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "IncidentEvidenceRecorderSettings",\n        SimpleNamespace(\n            from_environment=lambda: (\n                SimpleNamespace(\n                    enabled=True,\n                    resolve_output_dir=lambda: (\n                        tmp_path / "captures"\n                    ),\n                )\n            )\n        ),\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "ProductionIncidentEvidenceRecorder",\n        Recorder,\n    )\n\n    result = await value.execute(ctx)\n\n    assert result == [\n        "authoritative-pipeline-result"\n    ]\n    assert order == [\n        "pipeline",\n        "recorder",\n    ]\n\n    recorder_context = captured[\n        "context"\n    ]\n\n    assert recorder_context is not ctx\n    assert (\n        recorder_context.event\n        is not original_event\n    )\n    assert (\n        recorder_context.event\n        == original_event\n    )\n    assert (\n        recorder_context.tools\n        is value.tools\n    )\n    assert recorder_context.trace is None\n    assert recorder_context.variables == {}\n    assert recorder_context.results == {}\n    assert recorder_context.metadata == {}\n\n    assert ctx.metadata[\n        "incident_evidence_recorder"\n    ] == {\n        "schema_version": "v1",\n        "shadow_mode": True,\n        "read_only": True,\n        "decision_influence": False,\n        "automatic": True,\n        "status": "captured",\n        "created": True,\n        "incident_id": (\n            "capture-11111111-1111-4111-8111-111111111111"\n        ),\n        "observation_count": 4,\n        "capture_file": (\n            "capture-111.replay.json"\n        ),\n    }\n\n\n@pytest.mark.asyncio\nasync def test_failure_is_sanitized_and_pipeline_result_survives(\n    monkeypatch,\n):\n    order = []\n    value = lightweight_runtime(order)\n    ctx = context(value)\n    secret = "secret-production-tool-detail"\n\n    class Recorder:\n        def __init__(self, output_dir):\n            pass\n\n        async def record(self, recorder_context):\n            order.append("recorder")\n            raise RuntimeError(secret)\n\n    monkeypatch.setattr(\n        runtime_module,\n        "IncidentEvidenceRecorderSettings",\n        SimpleNamespace(\n            from_environment=lambda: (\n                SimpleNamespace(\n                    enabled=True,\n                    resolve_output_dir=lambda: (\n                        SimpleNamespace()\n                    ),\n                )\n            )\n        ),\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "ProductionIncidentEvidenceRecorder",\n        Recorder,\n    )\n\n    result = await value.execute(ctx)\n\n    assert result == [\n        "authoritative-pipeline-result"\n    ]\n    assert order == [\n        "pipeline",\n        "recorder",\n    ]\n\n    snapshot = ctx.metadata[\n        "incident_evidence_recorder"\n    ]\n\n    assert snapshot["status"] == "failed"\n    assert (\n        snapshot["failure_code"]\n        == "RuntimeError"\n    )\n    assert secret not in str(ctx.metadata)\n\n\n@pytest.mark.asyncio\nasync def test_stale_metadata_is_removed_before_pipeline(\n    monkeypatch,\n):\n    order = []\n    value = lightweight_runtime(order)\n    ctx = context(value)\n\n    ctx.metadata[\n        "incident_evidence_recorder"\n    ] = {\n        "stale": True\n    }\n\n    monkeypatch.setattr(\n        runtime_module,\n        "IncidentEvidenceRecorderSettings",\n        SimpleNamespace(\n            from_environment=lambda: (\n                SimpleNamespace(\n                    enabled=False\n                )\n            )\n        ),\n    )\n\n    result = await value.execute(ctx)\n\n    assert result == [\n        "authoritative-pipeline-result"\n    ]\n    assert (\n        "incident_evidence_recorder"\n        not in ctx.metadata\n    )\n'
RUNTIME_IMPORT_BLOCK = 'from services.agent_runtime.app.incident_evidence.recorder import (\n    ProductionIncidentEvidenceRecorder,\n)\nfrom services.agent_runtime.app.incident_evidence.settings import (\n    IncidentEvidenceRecorderSettings,\n)\n\n'
RUNTIME_METHOD_SOURCE = '    async def _record_incident_evidence_shadow(\n        self,\n        context: AgentContext,\n    ) -> None:\n        """\n        Best-effort, decision-isolated production evidence preservation.\n\n        Runs only after the authoritative PlannerPipeline succeeds.\n        Disabled mode constructs no Recorder and issues no production Probe.\n        """\n\n        try:\n            settings = (\n                IncidentEvidenceRecorderSettings\n                .from_environment()\n            )\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n            return\n\n        if not settings.enabled:\n            return\n\n        recorder_context = AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n        try:\n            recorder = ProductionIncidentEvidenceRecorder(\n                settings.resolve_output_dir()\n            )\n\n            result = await recorder.record(\n                recorder_context\n            )\n\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "captured",\n                "created": result.created,\n                "incident_id": result.incident_id,\n                "observation_count": (\n                    result.observation_count\n                ),\n                "capture_file": result.path.name,\n            }\n\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n'


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
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    path.write_text(
        text.replace("\r\n", "\n").replace("\r", "\n"),
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


def find_runtime_class(tree: ast.Module) -> ast.ClassDef:
    matches = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.ClassDef)
            and node.name == "AgentRuntime"
        )
    ]

    if len(matches) != 1:
        raise RuntimeError(
            "runtime.py must contain exactly one AgentRuntime class"
        )

    return matches[0]


def find_method(
    class_node: ast.ClassDef,
    name: str,
):
    matches = [
        node
        for node in class_node.body
        if (
            isinstance(
                node,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                ),
            )
            and node.name == name
        )
    ]

    if len(matches) != 1:
        raise RuntimeError(
            f"AgentRuntime must contain exactly one {name} method"
        )

    return matches[0]


def is_pipeline_execute_assignment(node: ast.stmt) -> bool:
    if not isinstance(node, ast.Assign):
        return False

    if len(node.targets) != 1:
        return False

    target = node.targets[0]

    if (
        not isinstance(target, ast.Name)
        or target.id != "results"
    ):
        return False

    value = node.value

    if not isinstance(value, ast.Await):
        return False

    call = value.value

    if not isinstance(call, ast.Call):
        return False

    func = call.func

    return (
        isinstance(func, ast.Attribute)
        and func.attr == "execute"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "pipeline"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "self"
    )


def patch_runtime(path: Path) -> None:
    text = read_text(path)

    if any(
        token in text
        for token in (
            "ProductionIncidentEvidenceRecorder",
            "IncidentEvidenceRecorderSettings",
            "_record_incident_evidence_shadow",
        )
    ):
        raise RuntimeError(
            "Runtime Incident Evidence Recorder wiring already appears present"
        )

    tree = ast.parse(text)
    runtime_class = find_runtime_class(tree)
    execute = find_method(
        runtime_class,
        "execute",
    )

    assignments = [
        node
        for node in execute.body
        if is_pipeline_execute_assignment(node)
    ]

    if len(assignments) != 1:
        raise RuntimeError(
            "Could not identify the authoritative Pipeline execute assignment"
        )

    # 1. Imports before AgentRuntime.
    lines = text.splitlines(keepends=True)
    lines.insert(
        runtime_class.lineno - 1,
        RUNTIME_IMPORT_BLOCK,
    )
    text = "".join(lines)

    # 2. Remove stale Recorder metadata immediately before Pipeline.
    tree = ast.parse(text)
    runtime_class = find_runtime_class(tree)
    execute = find_method(
        runtime_class,
        "execute",
    )
    assignment = next(
        node
        for node in execute.body
        if is_pipeline_execute_assignment(node)
    )

    lines = text.splitlines(keepends=True)
    lines.insert(
        assignment.lineno - 1,
        (
            "        context.metadata.pop(\n"
            "            \"incident_evidence_recorder\",\n"
            "            None,\n"
            "        )\n\n"
        ),
    )
    text = "".join(lines)

    # 3. Run Recorder only after successful authoritative Pipeline.
    tree = ast.parse(text)
    runtime_class = find_runtime_class(tree)
    execute = find_method(
        runtime_class,
        "execute",
    )
    assignment = next(
        node
        for node in execute.body
        if is_pipeline_execute_assignment(node)
    )

    if assignment.end_lineno is None:
        raise RuntimeError(
            "Pipeline execute assignment has no end boundary"
        )

    lines = text.splitlines(keepends=True)
    lines.insert(
        assignment.end_lineno,
        (
            "\n"
            "        # Evidence Recorder is evaluation-only and best-effort.\n"
            "        await self._record_incident_evidence_shadow(\n"
            "            context\n"
            "        )\n"
        ),
    )
    text = "".join(lines)

    # 4. Append helper method at the end of AgentRuntime.
    tree = ast.parse(text)
    runtime_class = find_runtime_class(tree)

    if runtime_class.end_lineno is None:
        raise RuntimeError(
            "AgentRuntime class has no end boundary"
        )

    lines = text.splitlines(keepends=True)
    lines.insert(
        runtime_class.end_lineno,
        "\n" + RUNTIME_METHOD_SOURCE.strip("\n") + "\n",
    )

    updated = "".join(lines)

    ast.parse(updated)
    write_text(path, updated)


def discover_runtime_compatibility_tests(
    root: Path,
) -> list[str]:
    tests_root = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
    )

    selected = []

    preferred = [
        "test_investigation_auto_shadow_orchestration.py",
        "test_investigation_rca_comparison.py",
        "test_production_incident_evidence_recorder.py",
    ]

    for name in preferred:
        path = tests_root / name

        if path.exists():
            selected.append(
                str(path.relative_to(root))
            )

    candidates = []

    for path in sorted(
        tests_root.glob("test_*.py")
    ):
        relative = str(
            path.relative_to(root)
        )

        if relative in selected:
            continue

        try:
            content = path.read_text(
                encoding="utf-8-sig",
                errors="replace",
            )
        except OSError:
            continue

        score = sum(
            1
            for token in (
                "AgentRuntime",
                "runtime.execute(",
                "investigation_shadow",
                "PlannerPipeline",
            )
            if token in content
        )

        if score >= 2:
            candidates.append(
                (
                    -score,
                    path.name,
                    path,
                )
            )

    for _score, _name, path in sorted(
        candidates
    )[:3]:
        selected.append(
            str(path.relative_to(root))
        )

    return selected


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after = root / AFTER_NAME
    error = root / ERROR_NAME

    for output in (after, error):
        try:
            output.unlink()
        except FileNotFoundError:
            pass

    runtime_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "runtime"
        / "runtime.py"
    )

    package_dir = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "incident_evidence"
    )

    recorder_file = (
        package_dir
        / "recorder.py"
    )

    settings_file = (
        package_dir
        / "settings.py"
    )

    init_file = (
        package_dir
        / "__init__.py"
    )

    core_test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_production_incident_evidence_recorder.py"
    )

    wiring_test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_runtime_incident_evidence_recorder_wiring.py"
    )

    required = [
        runtime_file,
        recorder_file,
        init_file,
        core_test_file,
    ]

    for path in required:
        if not path.exists():
            raise RuntimeError(
                f"Required Recorder/Runtime file is missing: {path}"
            )

    targets = [
        runtime_file,
        settings_file,
        init_file,
        wiring_test_file,
    ]

    preexisting = {
        path: path.exists()
        for path in targets
    }

    backups = []

    report = [
        "Runtime Incident Evidence Recorder Wiring v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Ordering:",
        "PlannerPipeline -> Evidence Recorder -> optional Investigation Shadow",
        "",
        "Safety:",
        "- disabled-default",
        "- exact acknowledgement required",
        "- Recorder failure cannot change Pipeline result",
        "- isolated AgentContext",
        "- shared Runtime-owned ToolManager only",
        "- no LLM/Action/Approval/Verification authority",
    ]

    try:
        section(report, "BACKUP")

        for path in targets:
            if path.exists():
                backup = backup_file(path)
                backups.append(
                    (path, backup)
                )
                report.append(
                    "backup="
                    + str(
                        backup.relative_to(root)
                    )
                )

        write_text(
            settings_file,
            SETTINGS_SOURCE,
        )

        write_text(
            init_file,
            INIT_SOURCE,
        )

        write_text(
            wiring_test_file,
            TEST_SOURCE,
        )

        patch_runtime(
            runtime_file
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
                str(runtime_file.relative_to(root)),
                str(recorder_file.relative_to(root)),
                str(settings_file.relative_to(root)),
                str(init_file.relative_to(root)),
                str(core_test_file.relative_to(root)),
                str(wiring_test_file.relative_to(root)),
            ],
        )

        add_command(report, syntax)

        if syntax.returncode != 0:
            raise RuntimeError(
                "Python syntax verification failed"
            )

        focused = run_command(
            root=root,
            name="Recorder Runtime wiring focused tests",
            command=[
                "uv",
                "run",
                "pytest",
                str(wiring_test_file.relative_to(root)),
                str(core_test_file.relative_to(root)),
                "-q",
            ],
        )

        add_command(report, focused)

        if focused.returncode != 0:
            raise RuntimeError(
                "Recorder Runtime wiring focused tests failed"
            )

        compatibility_tests = (
            discover_runtime_compatibility_tests(
                root
            )
        )

        section(
            report,
            "DISCOVERED RUNTIME COMPATIBILITY TESTS",
        )

        for selected in compatibility_tests:
            report.append(selected)

        if compatibility_tests:
            compatibility = run_command(
                root=root,
                name="Runtime compatibility tests",
                command=[
                    "uv",
                    "run",
                    "pytest",
                    *compatibility_tests,
                    "-q",
                ],
            )

            add_command(
                report,
                compatibility,
            )

            if compatibility.returncode != 0:
                raise RuntimeError(
                    "Runtime compatibility tests failed"
                )

        static_check = run_command(
            root=root,
            name="Recorder authority static check",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "s=Path(r'services/agent_runtime/app/runtime/runtime.py')"
                    ".read_text(encoding='utf-8'); "
                    "m=s[s.index('async def _record_incident_evidence_shadow'):]; "
                    "bad=[x for x in ['ActionRuntime(', 'ApprovalService(', "
                    "'VerificationRuntime(', 'create_llm_gateway('] if x in m]; "
                    "print('forbidden_matches='+str(bad)); "
                    "raise SystemExit(1 if bad else 0)"
                ),
            ],
        )

        add_command(
            report,
            static_check,
        )

        if static_check.returncode != 0:
            raise RuntimeError(
                "Recorder Runtime authority boundary failed"
            )

        status = run_command(
            root=root,
            name="Git status",
            command=[
                "git",
                "status",
                "--short",
                "--",
                str(runtime_file.relative_to(root)),
                str(settings_file.relative_to(root)),
                str(init_file.relative_to(root)),
                str(recorder_file.relative_to(root)),
                str(core_test_file.relative_to(root)),
                str(wiring_test_file.relative_to(root)),
            ],
        )

        add_command(
            report,
            status,
        )

        diff = run_command(
            root=root,
            name="Tracked Runtime diff",
            command=[
                "git",
                "diff",
                "--",
                str(runtime_file.relative_to(root)),
                str(init_file.relative_to(root)),
            ],
        )

        add_command(
            report,
            diff,
        )

        section(
            report,
            "FEATURE FLAG",
        )

        report.extend(
            [
                "Default:",
                "AGENT_INCIDENT_EVIDENCE_RECORDER_ENABLED=false",
                "",
                "Enable:",
                "AGENT_INCIDENT_EVIDENCE_RECORDER_ENABLED=true",
                (
                    "AGENT_INCIDENT_EVIDENCE_RECORDER_ACKNOWLEDGEMENT="
                    "I_ACKNOWLEDGE_READ_ONLY_PRODUCTION_INCIDENT_EVIDENCE_CAPTURE"
                ),
                "",
                "Optional output directory:",
                (
                    "AGENT_INCIDENT_EVIDENCE_RECORDER_OUTPUT_DIR="
                    "evaluation_data/production_incident_captures"
                ),
            ]
        )

        section(
            report,
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "Default disabled mode issues no Recorder Probe.",
                "Enabled mode runs Recorder after successful Pipeline only.",
                "Recorder failure is sanitized and never changes Pipeline result.",
                "Investigation Shadow remains independent and optional.",
            ]
        )

        write_text(
            after,
            "\n".join(report) + "\n",
        )

        print("=" * 72)
        print(
            "RUNTIME INCIDENT EVIDENCE RECORDER WIRING V1 PASSED"
        )
        print("=" * 72)
        print("")
        print("Default remains disabled.")
        print("")
        print("Upload:")
        print(after)

        return 0

    except Exception as exc:
        rollback = []

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

        for path in targets:
            if (
                not preexisting[path]
                and path.exists()
            ):
                try:
                    path.unlink()
                    rollback.append(
                        "REMOVED newly-created "
                        + str(
                            path.relative_to(root)
                        )
                    )
                except Exception as rollback_exc:
                    rollback.append(
                        "ROLLBACK FAILED removing "
                        + str(
                            path.relative_to(root)
                        )
                        + ": "
                        + f"{type(rollback_exc).__name__}: {rollback_exc}"
                    )

        error_lines = [
            "Runtime Incident Evidence Recorder Wiring v1 FAILED",
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
        print(
            "RUNTIME INCIDENT EVIDENCE RECORDER WIRING V1 FAILED"
        )
        print("=" * 72)
        print("")
        print("Modified files were rolled back where possible.")
        print("")
        print("Upload:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
