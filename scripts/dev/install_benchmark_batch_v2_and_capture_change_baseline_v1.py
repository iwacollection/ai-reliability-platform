from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from datetime import datetime
from pathlib import Path


VERSION = "benchmark-batch-v2-change-baseline-v1"

AFTER_NAME = (
    "benchmark_batch_v2_change_investigation_baseline_v1_after.txt"
)

ERROR_NAME = (
    "benchmark_batch_v2_change_investigation_baseline_v1_error.txt"
)

EXPECTED_BATCH_HASH = '9fe2a1de4bc007f512eeac196cee2de873cb2404eecb2edf3cdfbc2adcc5fb4b'

BATCH_SOURCE = 'from __future__ import annotations\n\nimport argparse\nimport json\nimport subprocess\nimport sys\nfrom datetime import datetime, timezone\nfrom pathlib import Path\n\n\nREPORT_JSON = Path("investigation_intelligence_benchmark_v1_report.json")\nREPORT_TXT = Path("investigation_intelligence_benchmark_v1_report.txt")\nERROR_TXT = Path("investigation_intelligence_benchmark_v1_error.txt")\n\nDEFAULT_OUTPUT = Path("investigation_benchmark_batch_bundle.json")\n\n\ndef cleanup_temp() -> None:\n    for path in (\n        REPORT_JSON,\n        REPORT_TXT,\n        ERROR_TXT,\n    ):\n        try:\n            path.unlink()\n        except FileNotFoundError:\n            pass\n\n\ndef run_once(\n    *,\n    runner: Path,\n    provider: str,\n    scenario: str | None,\n    mode: str | None,\n) -> dict:\n    cleanup_temp()\n\n    command = [\n        "uv",\n        "run",\n        "python",\n        str(runner),\n        "--provider",\n        provider,\n    ]\n\n    if scenario:\n        command.extend(\n            [\n                "--scenario",\n                scenario,\n            ]\n        )\n    elif mode:\n        command.extend(\n            [\n                "--mode",\n                mode,\n            ]\n        )\n    else:\n        raise RuntimeError(\n            "Either scenario or mode is required."\n        )\n\n    process = subprocess.run(\n        command,\n        capture_output=True,\n        text=True,\n        encoding="utf-8",\n        errors="replace",\n        check=False,\n    )\n\n    result = {\n        "command": command,\n        "exit_code": process.returncode,\n        "stdout": process.stdout,\n        "stderr": process.stderr,\n        "report_json": None,\n        "report_text": None,\n        "error_text": None,\n    }\n\n    if REPORT_JSON.exists():\n        try:\n            result["report_json"] = json.loads(\n                REPORT_JSON.read_text(\n                    encoding="utf-8"\n                )\n            )\n        except Exception as exc:\n            result["report_json_parse_error"] = (\n                f"{type(exc).__name__}: {exc}"\n            )\n            result["report_json_raw"] = (\n                REPORT_JSON.read_text(\n                    encoding="utf-8",\n                    errors="replace",\n                )\n            )\n\n    if REPORT_TXT.exists():\n        result["report_text"] = (\n            REPORT_TXT.read_text(\n                encoding="utf-8",\n                errors="replace",\n            )\n        )\n\n    if ERROR_TXT.exists():\n        result["error_text"] = (\n            ERROR_TXT.read_text(\n                encoding="utf-8",\n                errors="replace",\n            )\n        )\n\n    cleanup_temp()\n\n    return result\n\n\ndef summarize_run(\n    payload: dict,\n) -> dict:\n    report = payload.get(\n        "report_json"\n    )\n\n    if not isinstance(\n        report,\n        dict,\n    ):\n        return {\n            "exit_code": payload.get(\n                "exit_code"\n            ),\n            "status": "execution_error",\n            "scenario_count": 0,\n        }\n\n    scenarios = report.get(\n        "scenarios"\n    ) or []\n\n    first = (\n        scenarios[0]\n        if len(\n            scenarios\n        ) == 1\n        else None\n    )\n\n    summary = {\n        "exit_code": payload.get(\n            "exit_code"\n        ),\n        "overall_score": report.get(\n            "overall_score"\n        ),\n        "outcome_accuracy": report.get(\n            "outcome_accuracy"\n        ),\n        "abstention_accuracy": report.get(\n            "abstention_accuracy"\n        ),\n        "sufficient_evidence_accuracy": report.get(\n            "sufficient_evidence_accuracy"\n        ),\n        "average_tool_calls": report.get(\n            "average_tool_calls"\n        ),\n        "guard_rescue_count": report.get(\n            "guard_rescue_count"\n        ),\n        "guard_rescue_rate": report.get(\n            "guard_rescue_rate"\n        ),\n        "scenario_count": len(\n            scenarios\n        ),\n    }\n\n    if isinstance(\n        first,\n        dict,\n    ):\n        summary.update(\n            {\n                "scenario_key": first.get(\n                    "scenario_key"\n                ),\n                "score": first.get(\n                    "score"\n                ),\n                "final_status": first.get(\n                    "final_status"\n                ),\n                "final_stop_reason": first.get(\n                    "final_stop_reason"\n                ),\n                "failure_code": first.get(\n                    "failure_code"\n                ),\n                "epistemic_guard_code": first.get(\n                    "epistemic_guard_code"\n                ),\n                "guard_rescued": first.get(\n                    "guard_rescued"\n                ),\n                "tool_call_count": first.get(\n                    "tool_call_count"\n                ),\n                "outcome_correct": first.get(\n                    "outcome_correct"\n                ),\n            }\n        )\n\n    return summary\n\n\ndef main() -> int:\n    parser = argparse.ArgumentParser(\n        description=(\n            "Run Investigation Intelligence Benchmark multiple times "\n            "and emit exactly one merged JSON bundle."\n        )\n    )\n\n    parser.add_argument(\n        "--provider",\n        default="bailian",\n    )\n\n    parser.add_argument(\n        "--scenario",\n        action="append",\n        default=[],\n        help=(\n            "Scenario key. Repeat --scenario for multiple scenarios."\n        ),\n    )\n\n    parser.add_argument(\n        "--mode",\n        choices=[\n            "smoke",\n            "full",\n        ],\n        default=None,\n    )\n\n    parser.add_argument(\n        "--repeat",\n        type=int,\n        default=1,\n    )\n\n    parser.add_argument(\n        "--output",\n        default=str(\n            DEFAULT_OUTPUT\n        ),\n    )\n\n    parser.add_argument(\n        "--runner",\n        default=(\n            "scripts/dev/"\n            "run_investigation_intelligence_benchmark_v1.py"\n        ),\n    )\n\n    args = parser.parse_args()\n\n    if args.repeat < 1:\n        parser.error(\n            "--repeat must be >= 1"\n        )\n\n    if bool(\n        args.scenario\n    ) == bool(\n        args.mode\n    ):\n        parser.error(\n            "Use either --scenario ... or --mode ..., not both."\n        )\n\n    runner = Path(\n        args.runner\n    )\n\n    if not runner.exists():\n        raise SystemExit(\n            f"Benchmark runner not found: {runner}"\n        )\n\n    output = Path(\n        args.output\n    )\n\n    targets = (\n        [\n            {\n                "kind": "scenario",\n                "value": scenario,\n            }\n            for scenario in args.scenario\n        ]\n        if args.scenario\n        else [\n            {\n                "kind": "mode",\n                "value": args.mode,\n            }\n        ]\n    )\n\n    bundle = {\n        "schema_version": "investigation-benchmark-batch-v2",\n        "generated_at": datetime.now(\n            timezone.utc\n        ).isoformat(),\n        "provider": args.provider,\n        "repeat": args.repeat,\n        "targets": targets,\n        "runs": [],\n    }\n\n    total = (\n        len(\n            targets\n        )\n        * args.repeat\n    )\n\n    current = 0\n\n    for target in targets:\n        for run_no in range(\n            1,\n            args.repeat + 1,\n        ):\n            current += 1\n\n            print(\n                "=" * 72\n            )\n            print(\n                (\n                    f"[{current}/{total}] "\n                    f"{target[\'kind\']}={target[\'value\']} "\n                    f"run={run_no}/{args.repeat}"\n                )\n            )\n            print(\n                "=" * 72\n            )\n\n            payload = run_once(\n                runner=runner,\n                provider=args.provider,\n                scenario=(\n                    target["value"]\n                    if target[\n                        "kind"\n                    ] == "scenario"\n                    else None\n                ),\n                mode=(\n                    target["value"]\n                    if target[\n                        "kind"\n                    ] == "mode"\n                    else None\n                ),\n            )\n\n            bundle[\n                "runs"\n            ].append(\n                {\n                    "target_kind": target[\n                        "kind"\n                    ],\n                    "target": target[\n                        "value"\n                    ],\n                    "run": run_no,\n                    "summary": summarize_run(\n                        payload\n                    ),\n                    "raw": payload,\n                }\n            )\n\n    summaries = [\n        run[\n            "summary"\n        ]\n        for run in bundle[\n            "runs"\n        ]\n    ]\n\n    scenario_results = []\n\n    for run in bundle[\n        "runs"\n    ]:\n        report = (\n            run.get(\n                "raw",\n                {},\n            )\n            .get(\n                "report_json"\n            )\n        )\n\n        if not isinstance(\n            report,\n            dict,\n        ):\n            continue\n\n        scenarios = report.get(\n            "scenarios"\n        ) or []\n\n        for scenario_result in scenarios:\n            if not isinstance(\n                scenario_result,\n                dict,\n            ):\n                continue\n\n            scenario_results.append(\n                {\n                    "target": run.get(\n                        "target"\n                    ),\n                    "run": run.get(\n                        "run"\n                    ),\n                    **scenario_result,\n                }\n            )\n\n    execution_failure_codes = {\n        "InvestigationLLMExecutionError",\n        "InvestigationReasonerExecutionRetryError",\n        "InvestigationLLMUnavailableError",\n    }\n\n    per_scenario = {}\n\n    for item in scenario_results:\n        key = item.get(\n            "scenario_key"\n        )\n\n        if not key:\n            continue\n\n        bucket = per_scenario.setdefault(\n            key,\n            {\n                "scenario_key": key,\n                "runs": 0,\n                "outcome_correct_count": 0,\n                "guard_rescued_count": 0,\n                "reasoner_error_count": 0,\n                "execution_failure_count": 0,\n                "tool_calls": [],\n                "scores": [],\n                "stop_reasons": {},\n            },\n        )\n\n        bucket[\n            "runs"\n        ] += 1\n\n        if item.get(\n            "outcome_correct"\n        ) is True:\n            bucket[\n                "outcome_correct_count"\n            ] += 1\n\n        if item.get(\n            "guard_rescued"\n        ) is True:\n            bucket[\n                "guard_rescued_count"\n            ] += 1\n\n        if (\n            item.get(\n                "final_stop_reason"\n            )\n            == "reasoner_error"\n        ):\n            bucket[\n                "reasoner_error_count"\n            ] += 1\n\n        if item.get(\n            "failure_code"\n        ) in execution_failure_codes:\n            bucket[\n                "execution_failure_count"\n            ] += 1\n\n        tool_calls = item.get(\n            "tool_call_count"\n        )\n\n        if isinstance(\n            tool_calls,\n            (\n                int,\n                float,\n            ),\n        ):\n            bucket[\n                "tool_calls"\n            ].append(\n                float(\n                    tool_calls\n                )\n            )\n\n        score = item.get(\n            "score"\n        )\n\n        if isinstance(\n            score,\n            (\n                int,\n                float,\n            ),\n        ):\n            bucket[\n                "scores"\n            ].append(\n                float(\n                    score\n                )\n            )\n\n        reason = (\n            item.get(\n                "final_stop_reason"\n            )\n            or "<NONE>"\n        )\n\n        bucket[\n            "stop_reasons"\n        ][\n            reason\n        ] = (\n            bucket[\n                "stop_reasons"\n            ].get(\n                reason,\n                0,\n            )\n            + 1\n        )\n\n    for bucket in per_scenario.values():\n        runs = max(\n            1,\n            bucket[\n                "runs"\n            ],\n        )\n\n        bucket[\n            "outcome_accuracy"\n        ] = round(\n            (\n                bucket[\n                    "outcome_correct_count"\n                ]\n                / runs\n            )\n            * 100.0,\n            1,\n        )\n\n        bucket[\n            "average_tool_calls"\n        ] = (\n            round(\n                sum(\n                    bucket[\n                        "tool_calls"\n                    ]\n                )\n                / len(\n                    bucket[\n                        "tool_calls"\n                    ]\n                ),\n                3,\n            )\n            if bucket[\n                "tool_calls"\n            ]\n            else None\n        )\n\n        bucket[\n            "average_score"\n        ] = (\n            round(\n                sum(\n                    bucket[\n                        "scores"\n                    ]\n                )\n                / len(\n                    bucket[\n                        "scores"\n                    ]\n                ),\n                3,\n            )\n            if bucket[\n                "scores"\n            ]\n            else None\n        )\n\n        del bucket[\n            "tool_calls"\n        ]\n\n        del bucket[\n            "scores"\n        ]\n\n    scenario_count = len(\n        scenario_results\n    )\n\n    outcome_correct_count = sum(\n        1\n        for item in scenario_results\n        if item.get(\n            "outcome_correct"\n        )\n        is True\n    )\n\n    reasoner_error_count = sum(\n        1\n        for item in scenario_results\n        if item.get(\n            "final_stop_reason"\n        )\n        == "reasoner_error"\n    )\n\n    execution_error_count = sum(\n        1\n        for item in scenario_results\n        if item.get(\n            "failure_code"\n        )\n        in execution_failure_codes\n    )\n\n    guard_rescued_count = sum(\n        1\n        for item in scenario_results\n        if item.get(\n            "guard_rescued"\n        )\n        is True\n    )\n\n    bundle[\n        "aggregate"\n    ] = {\n        "run_count": len(\n            summaries\n        ),\n        "scenario_result_count": scenario_count,\n        "execution_error_count": execution_error_count,\n        "reasoner_error_count": reasoner_error_count,\n        "outcome_correct_count": outcome_correct_count,\n        "outcome_accuracy": (\n            round(\n                (\n                    outcome_correct_count\n                    / scenario_count\n                )\n                * 100.0,\n                1,\n            )\n            if scenario_count\n            else None\n        ),\n        "guard_rescued_count": guard_rescued_count,\n        "guard_rescue_rate": (\n            round(\n                (\n                    guard_rescued_count\n                    / scenario_count\n                )\n                * 100.0,\n                1,\n            )\n            if scenario_count\n            else None\n        ),\n        "per_scenario": sorted(\n            per_scenario.values(),\n            key=lambda item: item[\n                "scenario_key"\n            ],\n        ),\n    }\n\n    output.write_text(\n        json.dumps(\n            bundle,\n            ensure_ascii=False,\n            indent=2,\n        ),\n        encoding="utf-8",\n        newline="\\n",\n    )\n\n    cleanup_temp()\n\n    print("")\n    print(\n        "=" * 72\n    )\n    print(\n        "BATCH BENCHMARK FINISHED"\n    )\n    print(\n        "=" * 72\n    )\n    print(\n        f"Runs: {len(bundle[\'runs\'])}"\n    )\n    print(\n        f"Output: {output}"\n    )\n    print("")\n    print(\n        "Only upload this one bundle file."\n    )\n\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(\n        main()\n    )\n'
TEST_SOURCE = 'from __future__ import annotations\n\nimport importlib.util\nimport json\nfrom pathlib import Path\n\n\nRUNNER = (\n    Path(__file__).resolve().parents[3]\n    / "scripts"\n    / "dev"\n    / "run_investigation_benchmark_batch_v2.py"\n)\n\n\ndef load_runner():\n    spec = importlib.util.spec_from_file_location(\n        "batch_v2",\n        RUNNER,\n    )\n\n    assert spec is not None\n    assert spec.loader is not None\n\n    module = importlib.util.module_from_spec(\n        spec\n    )\n\n    spec.loader.exec_module(\n        module\n    )\n\n    return module\n\n\ndef test_full_mode_aggregate_counts_scenario_results_not_outer_runs(\n    tmp_path,\n    monkeypatch,\n):\n    module = load_runner()\n\n    reports = [\n        {\n            "overall_score": 80.0,\n            "outcome_accuracy": 50.0,\n            "scenarios": [\n                {\n                    "scenario_key": "a",\n                    "outcome_correct": True,\n                    "guard_rescued": False,\n                    "final_stop_reason": "sufficient_evidence",\n                    "failure_code": None,\n                    "tool_call_count": 2,\n                    "score": 100.0,\n                },\n                {\n                    "scenario_key": "b",\n                    "outcome_correct": False,\n                    "guard_rescued": False,\n                    "final_stop_reason": "reasoner_error",\n                    "failure_code": "InvestigationLLMExecutionError",\n                    "tool_call_count": 1,\n                    "score": 30.0,\n                },\n            ],\n        },\n        {\n            "overall_score": 90.0,\n            "outcome_accuracy": 100.0,\n            "scenarios": [\n                {\n                    "scenario_key": "a",\n                    "outcome_correct": True,\n                    "guard_rescued": False,\n                    "final_stop_reason": "sufficient_evidence",\n                    "failure_code": None,\n                    "tool_call_count": 3,\n                    "score": 95.0,\n                },\n                {\n                    "scenario_key": "b",\n                    "outcome_correct": True,\n                    "guard_rescued": True,\n                    "final_stop_reason": "insufficient_evidence",\n                    "failure_code": None,\n                    "tool_call_count": 4,\n                    "score": 85.0,\n                },\n            ],\n        },\n    ]\n\n    iterator = iter(\n        reports\n    )\n\n    def fake_run_once(\n        **kwargs,\n    ):\n        return {\n            "command": ["fake"],\n            "exit_code": 0,\n            "stdout": "",\n            "stderr": "",\n            "report_json": next(\n                iterator\n            ),\n            "report_text": "",\n            "error_text": None,\n        }\n\n    monkeypatch.setattr(\n        module,\n        "run_once",\n        fake_run_once,\n    )\n\n    monkeypatch.setattr(\n        module,\n        "cleanup_temp",\n        lambda: None,\n    )\n\n    fake_runner = tmp_path / "runner.py"\n    fake_runner.write_text(\n        "print(\'fake\')",\n        encoding="utf-8",\n    )\n\n    output = tmp_path / "bundle.json"\n\n    monkeypatch.setattr(\n        "sys.argv",\n        [\n            "batch",\n            "--provider",\n            "bailian",\n            "--mode",\n            "full",\n            "--repeat",\n            "2",\n            "--runner",\n            str(\n                fake_runner\n            ),\n            "--output",\n            str(\n                output\n            ),\n        ],\n    )\n\n    assert module.main() == 0\n\n    bundle = json.loads(\n        output.read_text(\n            encoding="utf-8"\n        )\n    )\n\n    aggregate = bundle[\n        "aggregate"\n    ]\n\n    assert aggregate[\n        "run_count"\n    ] == 2\n\n    assert aggregate[\n        "scenario_result_count"\n    ] == 4\n\n    assert aggregate[\n        "outcome_correct_count"\n    ] == 3\n\n    assert aggregate[\n        "outcome_accuracy"\n    ] == 75.0\n\n    assert aggregate[\n        "reasoner_error_count"\n    ] == 1\n\n    assert aggregate[\n        "execution_error_count"\n    ] == 1\n\n    assert aggregate[\n        "guard_rescued_count"\n    ] == 1\n\n    per_scenario = {\n        item[\n            "scenario_key"\n        ]: item\n        for item in aggregate[\n            "per_scenario"\n        ]\n    }\n\n    assert per_scenario[\n        "a"\n    ][\n        "outcome_accuracy"\n    ] == 100.0\n\n    assert per_scenario[\n        "b"\n    ][\n        "outcome_accuracy"\n    ] == 50.0\n'
BASELINE_FILES = ['services/agent_runtime/app/investigation/models.py', 'services/agent_runtime/app/investigation/reasoner.py', 'services/agent_runtime/app/investigation/coordinator.py', 'services/agent_runtime/app/investigation/probes.py', 'services/agent_runtime/app/investigation/factory.py', 'services/agent_runtime/app/investigation/epistemic_guard.py', 'services/agent_runtime/app/evaluation/intelligence_benchmark/engine.py', 'services/agent_runtime/app/evaluation/intelligence_benchmark/scenarios.py', 'services/agent_runtime/tests/test_investigation_models.py', 'services/agent_runtime/tests/test_investigation_coordinator.py', 'services/agent_runtime/tests/test_investigation_probes.py', 'services/agent_runtime/tests/test_investigation_intelligence_benchmark.py']


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
        "Repository root not found."
    )


def normalize_text(
    value: str,
) -> str:
    return (
        value
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def read_text(
    path: Path,
) -> str:
    return normalize_text(
        path.read_text(
            encoding="utf-8-sig",
            errors="strict",
        )
    )


def write_text(
    path: Path,
    value: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        normalize_text(
            value
        ),
        encoding="utf-8",
        newline="\n",
    )


def sha256_text(
    value: str,
) -> str:
    return hashlib.sha256(
        normalize_text(
            value
        ).encode(
            "utf-8"
        )
    ).hexdigest()


def backup_file(
    path: Path,
) -> Path:
    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup = path.with_name(
        f"{path.name}.before_{VERSION}_{stamp}.bak"
    )

    shutil.copy2(
        path,
        backup,
    )

    return backup


def run_command(
    *,
    root: Path,
    name: str,
    command: list[str],
):
    process = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    return {
        "name": name,
        "command": command,
        "returncode": process.returncode,
        "stdout": normalize_text(
            process.stdout
        ),
        "stderr": normalize_text(
            process.stderr
        ),
    }


def add_command(
    report: list[str],
    result: dict,
) -> None:
    report.extend(
        [
            "",
            "=" * 120,
            "COMMAND: "
            + result[
                "name"
            ],
            "=" * 120,
            "",
            " ".join(
                result[
                    "command"
                ]
            ),
            "",
            "ExitCode: "
            + str(
                result[
                    "returncode"
                ]
            ),
            "",
            "STDOUT",
            "-" * 120,
            result[
                "stdout"
            ].rstrip()
            or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            result[
                "stderr"
            ].rstrip()
            or "<EMPTY>",
        ]
    )


def main() -> int:
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

    batch_v1 = (
        root
        / "scripts"
        / "dev"
        / "run_investigation_benchmark_batch_v1.py"
    )

    batch_v2 = (
        root
        / "scripts"
        / "dev"
        / "run_investigation_benchmark_batch_v2.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_benchmark_batch_v2.py"
    )

    targets = [
        batch_v2,
        test_file,
    ]

    preexisting = {
        path: path.exists()
        for path in targets
    }

    backups = []

    report = [
        "Benchmark Batch v2 + Change Investigation Current Baseline",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Goals:",
        "- fix full-mode aggregate counts so they operate on scenario results, not outer Full runs",
        "- preserve the single-upload bundle workflow",
        "- capture the exact current Investigation baseline required for Change Investigation Capability #002",
        "",
        "No real LLM/Kubernetes/Prometheus request is sent.",
        "No Action/Approval/Verification behavior is changed.",
    ]

    try:
        if not batch_v1.exists():
            raise RuntimeError(
                "Current batch v1 runner is missing."
            )

        actual = sha256_text(
            read_text(
                batch_v1
            )
        )

        report.extend(
            [
                "",
                "=" * 120,
                "CURRENT BATCH V1 HASH PREFLIGHT",
                "=" * 120,
                "",
                "expected="
                + EXPECTED_BATCH_HASH,
                "actual="
                + actual,
            ]
        )

        if actual != EXPECTED_BATCH_HASH:
            raise RuntimeError(
                "Current batch v1 runner changed; refusing stale upgrade."
            )

        for path in targets:
            if path.exists():
                backup = backup_file(
                    path
                )

                backups.append(
                    (
                        path,
                        backup,
                    )
                )

        write_text(
            batch_v2,
            BATCH_SOURCE,
        )

        write_text(
            test_file,
            TEST_SOURCE,
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
                str(
                    batch_v2.relative_to(
                        root
                    )
                ),
                str(
                    test_file.relative_to(
                        root
                    )
                ),
            ],
        )

        add_command(
            report,
            syntax,
        )

        if syntax[
            "returncode"
        ] != 0:
            raise RuntimeError(
                "Batch v2 syntax failed."
            )

        focused = run_command(
            root=root,
            name="Batch v2 focused tests",
            command=[
                "uv",
                "run",
                "pytest",
                str(
                    test_file.relative_to(
                        root
                    )
                ),
                "-q",
            ],
        )

        add_command(
            report,
            focused,
        )

        if focused[
            "returncode"
        ] != 0:
            raise RuntimeError(
                "Batch v2 focused tests failed."
            )

        help_smoke = run_command(
            root=root,
            name="Batch v2 help smoke",
            command=[
                "uv",
                "run",
                "python",
                str(
                    batch_v2.relative_to(
                        root
                    )
                ),
                "--help",
            ],
        )

        add_command(
            report,
            help_smoke,
        )

        if help_smoke[
            "returncode"
        ] != 0:
            raise RuntimeError(
                "Batch v2 help smoke failed."
            )

        report.extend(
            [
                "",
                "=" * 120,
                "CHANGE INVESTIGATION #002 CURRENT BASELINE",
                "=" * 120,
                "",
                "The following complete CURRENT files are captured after the Batch v2 install.",
                "They are read-only baseline material for the next capability design.",
            ]
        )

        captured = 0

        for relative in BASELINE_FILES:
            path = (
                root
                / relative
            )

            report.extend(
                [
                    "",
                    "=" * 120,
                    relative,
                    "=" * 120,
                ]
            )

            if not path.exists():
                report.append(
                    "<MISSING>"
                )
                continue

            source = read_text(
                path
            )

            captured += 1

            report.extend(
                [
                    "sha256="
                    + sha256_text(
                        source
                    ),
                    "lines="
                    + str(
                        len(
                            source.splitlines()
                        )
                    ),
                    "",
                    source.rstrip(
                        "\n"
                    ),
                ]
            )

        status = run_command(
            root=root,
            name="Git status",
            command=[
                "git",
                "status",
                "--short",
                "--",
                str(
                    batch_v2.relative_to(
                        root
                    )
                ),
                str(
                    test_file.relative_to(
                        root
                    )
                ),
            ],
        )

        add_command(
            report,
            status,
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
                "Batch v2 is installed.",
                f"Change Investigation baseline files captured={captured}.",
                "",
                "Next:",
                "- upload only this single after file",
                "- then implement Change Investigation Capability #002 against these exact current files",
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
            "BATCH V2 + CHANGE BASELINE PASSED"
        )
        print(
            "=" * 72
        )
        print("")
        print(
            "Upload only:"
        )
        print(
            after
        )

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
                        original.relative_to(
                            root
                        )
                    )
                )
            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK FAILED "
                    + str(
                        original.relative_to(
                            root
                        )
                    )
                    + ": "
                    + f"{type(rollback_exc).__name__}: {rollback_exc}"
                )

        for path in targets:
            if (
                not preexisting[
                    path
                ]
                and path.exists()
            ):
                try:
                    path.unlink()

                    rollback.append(
                        "REMOVED "
                        + str(
                            path.relative_to(
                                root
                            )
                        )
                    )
                except Exception as rollback_exc:
                    rollback.append(
                        "ROLLBACK REMOVE FAILED "
                        + str(
                            path.relative_to(
                                root
                            )
                        )
                        + ": "
                        + f"{type(rollback_exc).__name__}: {rollback_exc}"
                    )

        write_text(
            error,
            "\n".join(
                [
                    "Benchmark Batch v2 + Change Investigation Baseline FAILED",
                    f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
                    "",
                    f"{type(exc).__name__}: {exc}",
                    "",
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
            )
            + "\n",
        )

        print(
            "=" * 72
        )
        print(
            "BATCH V2 + CHANGE BASELINE FAILED"
        )
        print(
            "=" * 72
        )
        print("")
        print(
            "Upload only:"
        )
        print(
            error
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
