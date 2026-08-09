from __future__ import annotations

import importlib.util
import json
from pathlib import Path


RUNNER = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "dev"
    / "run_investigation_benchmark_batch_v2.py"
)


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "batch_v2",
        RUNNER,
    )

    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(
        spec
    )

    spec.loader.exec_module(
        module
    )

    return module


def test_full_mode_aggregate_counts_scenario_results_not_outer_runs(
    tmp_path,
    monkeypatch,
):
    module = load_runner()

    reports = [
        {
            "overall_score": 80.0,
            "outcome_accuracy": 50.0,
            "scenarios": [
                {
                    "scenario_key": "a",
                    "outcome_correct": True,
                    "guard_rescued": False,
                    "final_stop_reason": "sufficient_evidence",
                    "failure_code": None,
                    "tool_call_count": 2,
                    "score": 100.0,
                },
                {
                    "scenario_key": "b",
                    "outcome_correct": False,
                    "guard_rescued": False,
                    "final_stop_reason": "reasoner_error",
                    "failure_code": "InvestigationLLMExecutionError",
                    "tool_call_count": 1,
                    "score": 30.0,
                },
            ],
        },
        {
            "overall_score": 90.0,
            "outcome_accuracy": 100.0,
            "scenarios": [
                {
                    "scenario_key": "a",
                    "outcome_correct": True,
                    "guard_rescued": False,
                    "final_stop_reason": "sufficient_evidence",
                    "failure_code": None,
                    "tool_call_count": 3,
                    "score": 95.0,
                },
                {
                    "scenario_key": "b",
                    "outcome_correct": True,
                    "guard_rescued": True,
                    "final_stop_reason": "insufficient_evidence",
                    "failure_code": None,
                    "tool_call_count": 4,
                    "score": 85.0,
                },
            ],
        },
    ]

    iterator = iter(
        reports
    )

    def fake_run_once(
        **kwargs,
    ):
        return {
            "command": ["fake"],
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "report_json": next(
                iterator
            ),
            "report_text": "",
            "error_text": None,
        }

    monkeypatch.setattr(
        module,
        "run_once",
        fake_run_once,
    )

    monkeypatch.setattr(
        module,
        "cleanup_temp",
        lambda: None,
    )

    fake_runner = tmp_path / "runner.py"
    fake_runner.write_text(
        "print('fake')",
        encoding="utf-8",
    )

    output = tmp_path / "bundle.json"

    monkeypatch.setattr(
        "sys.argv",
        [
            "batch",
            "--provider",
            "bailian",
            "--mode",
            "full",
            "--repeat",
            "2",
            "--runner",
            str(
                fake_runner
            ),
            "--output",
            str(
                output
            ),
        ],
    )

    assert module.main() == 0

    bundle = json.loads(
        output.read_text(
            encoding="utf-8"
        )
    )

    aggregate = bundle[
        "aggregate"
    ]

    assert aggregate[
        "run_count"
    ] == 2

    assert aggregate[
        "scenario_result_count"
    ] == 4

    assert aggregate[
        "outcome_correct_count"
    ] == 3

    assert aggregate[
        "outcome_accuracy"
    ] == 75.0

    assert aggregate[
        "reasoner_error_count"
    ] == 1

    assert aggregate[
        "execution_error_count"
    ] == 1

    assert aggregate[
        "guard_rescued_count"
    ] == 1

    per_scenario = {
        item[
            "scenario_key"
        ]: item
        for item in aggregate[
            "per_scenario"
        ]
    }

    assert per_scenario[
        "a"
    ][
        "outcome_accuracy"
    ] == 100.0

    assert per_scenario[
        "b"
    ][
        "outcome_accuracy"
    ] == 50.0
