from __future__ import annotations

import hashlib
import subprocess
import traceback

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "runtime-regression-test-alignment-v1"

AFTER_NAME = "runtime_regression_test_alignment_v1_after.txt"
ERROR_NAME = "runtime_regression_test_alignment_v1_error.txt"

TARGETS = {
    "services/agent_runtime/tests/test_investigation_llm_shadow_execution.py":
        "15e4c4ee1d2b2acc119ab1f6c18022d8256788df33ac739d01388e20fd83b551",
    "services/agent_runtime/tests/test_scenario_scope_propagation.py":
        "f0d589e8dc65e9f36269a73d412678d9ac508f35d9e04139516f6f1ee8e29d3b",
}

INVESTIGATION_OLD = '''    assert kubernetes.calls[0] == {
        "action": "describe",
        "resource": "pod",
        "target": "payment-api",
        "namespace": "payment",
    }
'''

INVESTIGATION_NEW = '''    assert kubernetes.calls[0] == {
        "action": "describe",
        "resource": "pod",
        "target": "payment-api",
        "namespace": "payment",
        "cluster": "production-a",
    }
'''

SCENARIO_OLD = '''class _Runtime:
    def __init__(self) -> None:
        self.memory = None
        self.tools = None
        self.skills = None
        self.pipeline = _Pipeline()
        self.action_runtime = (
            _ActionRuntime()
        )
'''

SCENARIO_NEW = '''class _Runtime:
    def __init__(self) -> None:
        self.memory = None
        self.tools = None
        self.skills = None
        self.pipeline = _Pipeline()
        self.action_runtime = (
            _ActionRuntime()
        )

    async def execute(
        self,
        context,
    ) -> list:
        # Match the current AgentRuntime contract used by ScenarioReplayEngine
        # while keeping this test stub intentionally limited to its local
        # Pipeline implementation.
        return await self.pipeline.execute(
            context
        )
'''


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: tuple[str, ...]
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
        "Repository root not found. Run this installer inside ai-reliability-platform."
    )


def normalize(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def read_text(path: Path) -> str:
    return normalize(
        path.read_text(
            encoding="utf-8-sig",
            errors="strict",
        )
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


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
        command=tuple(command),
        returncode=process.returncode,
        stdout=normalize(process.stdout),
        stderr=normalize(process.stderr),
    )


def section(report: list[str], title: str) -> None:
    report.extend(
        [
            "",
            "=" * 120,
            title,
            "=" * 120,
            "",
        ]
    )


def add_command(
    report: list[str],
    result: CommandResult,
) -> None:
    section(
        report,
        "COMMAND: " + result.name,
    )

    report.extend(
        [
            " ".join(result.command),
            "",
            "ExitCode: " + str(result.returncode),
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


def write_text(
    path: Path,
    value: str,
) -> None:
    path.write_text(
        value,
        encoding="utf-8",
        newline="\n",
    )


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

    paths = {
        relative: root / relative
        for relative in TARGETS
    }

    backups: dict[Path, bytes] = {}

    report = [
        "Runtime Regression Test Alignment v1",
        "GeneratedAt: "
        + datetime.now().astimezone().isoformat(),
        "",
        "Why this stage exists:",
        "- Feishu Adapter Core fix1 passed all Feishu focused checks.",
        "- Agent Runtime full suite exposed three stale test expectations.",
        "- this stage repairs only those test contracts before Feishu is reinstalled.",
        "",
        "Alignment 1:",
        "- Investigation Kubernetes probe is multi-cluster scoped.",
        "- the existing test already expects cluster in the Prometheus query.",
        "- Kubernetes expected call must therefore also include scope.cluster.",
        "",
        "Alignment 2:",
        "- ScenarioReplayEngine now enters runtime through runtime.execute().",
        "- its local unit-test _Runtime stub still exposes only pipeline.execute().",
        "- the stub will implement execute() by delegating to its local _Pipeline.",
        "- ScenarioReplayEngine itself remains authoritative for ActionRuntime namespace/cluster propagation.",
        "",
        "Safety:",
        "- modifies tests only",
        "- no production source file is changed",
        "- no LLM/network/Kubernetes/Prometheus request is made by installer logic",
        "- exact SHA256 preflight protects against stale edits",
        "- both test files are restored byte-for-byte on any failure",
    ]

    try:
        section(
            report,
            "SHA256 PREFLIGHT",
        )

        for relative, expected in TARGETS.items():
            path = paths[relative]

            if not path.exists():
                raise RuntimeError(
                    "Required test file is missing: "
                    + relative
                )

            actual = sha256(path)

            report.append(
                relative + "=" + actual
            )

            if actual != expected:
                raise RuntimeError(
                    relative
                    + " changed since the reviewed baseline. "
                    + "expected_sha256="
                    + expected
                    + " actual_sha256="
                    + actual
                    + ". Refusing stale test patch."
                )

            backups[path] = path.read_bytes()

        investigation_path = paths[
            "services/agent_runtime/tests/test_investigation_llm_shadow_execution.py"
        ]
        scenario_path = paths[
            "services/agent_runtime/tests/test_scenario_scope_propagation.py"
        ]

        investigation = read_text(
            investigation_path
        )

        if investigation.count(
            INVESTIGATION_OLD
        ) != 1:
            raise RuntimeError(
                "Investigation stale assertion block was not found exactly once"
            )

        investigation = investigation.replace(
            INVESTIGATION_OLD,
            INVESTIGATION_NEW,
            1,
        )

        scenario = read_text(
            scenario_path
        )

        if scenario.count(
            SCENARIO_OLD
        ) != 1:
            raise RuntimeError(
                "Scenario _Runtime stale contract block was not found exactly once"
            )

        scenario = scenario.replace(
            SCENARIO_OLD,
            SCENARIO_NEW,
            1,
        )

        write_text(
            investigation_path,
            investigation,
        )
        write_text(
            scenario_path,
            scenario,
        )

        syntax = run_command(
            root,
            "Aligned tests Python syntax",
            [
                "uv",
                "run",
                "python",
                "-m",
                "py_compile",
                str(
                    investigation_path.relative_to(root)
                ),
                str(
                    scenario_path.relative_to(root)
                ),
            ],
        )
        add_command(report, syntax)

        if syntax.returncode != 0:
            raise RuntimeError(
                "Aligned tests syntax failed"
            )

        failing_cases = run_command(
            root,
            "Previously failing three tests",
            [
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_llm_shadow_execution.py::"
                    "test_explicit_shadow_execution_runs_full_llm_evidence_loop"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_scenario_scope_propagation.py::"
                    "test_pod_oom_scope_reaches_action_plan"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_scenario_scope_propagation.py::"
                    "test_missing_scenario_scope_does_not_default"
                ),
                "-q",
            ],
        )
        add_command(report, failing_cases)

        if failing_cases.returncode != 0:
            raise RuntimeError(
                "Previously failing tests still fail"
            )

        investigation_compat = run_command(
            root,
            "Investigation multi-cluster compatibility",
            [
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_llm_shadow_execution.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_production_tool_contract.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_kubernetes_router.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_prometheus_router.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_production_scope_integrity.py"
                ),
                "-q",
            ],
        )
        add_command(report, investigation_compat)

        if investigation_compat.returncode != 0:
            raise RuntimeError(
                "Investigation multi-cluster compatibility failed"
            )

        replay_compat = run_command(
            root,
            "Runtime replay compatibility",
            [
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_scenario_scope_propagation.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_auto_shadow_orchestration.py"
                ),
                "-q",
            ],
        )
        add_command(report, replay_compat)

        if replay_compat.returncode != 0:
            raise RuntimeError(
                "Runtime replay compatibility failed"
            )

        full_suite = run_command(
            root,
            "Agent Runtime full test suite",
            [
                "uv",
                "run",
                "pytest",
                "services/agent_runtime/tests",
                "-q",
            ],
        )
        add_command(report, full_suite)

        if full_suite.returncode != 0:
            raise RuntimeError(
                "Agent Runtime full test suite still fails"
            )

        status = run_command(
            root,
            "Git status for aligned test files",
            [
                "git",
                "status",
                "--short",
                "--",
                *[
                    str(path.relative_to(root))
                    for path in paths.values()
                ],
            ],
        )
        add_command(report, status)

        section(
            report,
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "Runtime regression test baseline is aligned.",
                "",
                "Production code changed: False",
                "Test files changed: 2",
                "",
                "The full Agent Runtime suite is green again.",
                "",
                "Next stage after review:",
                "- rerun Feishu ChatOps Adapter Core v1 with the repaired baseline",
                "",
                "Upload only: " + AFTER_NAME,
            ]
        )

        after.write_text(
            "\n".join(report) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print("RUNTIME REGRESSION TEST ALIGNMENT V1 PASSED")
        print("=" * 72)
        print()
        print("Production source files changed: 0")
        print("Aligned test files: 2")
        print()
        print("Upload only:")
        print(after)

        return 0

    except Exception as exc:
        for path, raw in backups.items():
            path.write_bytes(raw)

        report.extend(
            [
                "",
                "=" * 120,
                "ROLLBACK",
                "=" * 120,
                "",
                "All touched test files were restored byte-for-byte.",
                "No production source file was modified.",
            ]
        )

        error.write_text(
            "\n".join(
                [
                    "Runtime Regression Test Alignment v1 FAILED",
                    "GeneratedAt: "
                    + datetime.now().astimezone().isoformat(),
                    "",
                    type(exc).__name__
                    + ": "
                    + str(exc),
                    "",
                    traceback.format_exc(),
                    "",
                    "PARTIAL REPORT",
                    "=" * 120,
                    *report,
                    "",
                    "Upload only: " + ERROR_NAME,
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print("RUNTIME REGRESSION TEST ALIGNMENT V1 FAILED")
        print("=" * 72)
        print()
        print("Touched test files were restored.")
        print("Production source files changed: 0")
        print()
        print("Upload only:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
