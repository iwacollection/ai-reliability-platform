from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPORT_JSON = Path("investigation_intelligence_benchmark_v1_report.json")
REPORT_TXT = Path("investigation_intelligence_benchmark_v1_report.txt")
ERROR_TXT = Path("investigation_intelligence_benchmark_v1_error.txt")

DEFAULT_OUTPUT = Path("investigation_benchmark_batch_bundle.json")


def cleanup_temp() -> None:
    for path in (
        REPORT_JSON,
        REPORT_TXT,
        ERROR_TXT,
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def run_once(
    *,
    runner: Path,
    provider: str,
    scenario: str | None,
    mode: str | None,
) -> dict:
    cleanup_temp()

    command = [
        "uv",
        "run",
        "python",
        str(runner),
        "--provider",
        provider,
    ]

    if scenario:
        command.extend(
            [
                "--scenario",
                scenario,
            ]
        )
    elif mode:
        command.extend(
            [
                "--mode",
                mode,
            ]
        )
    else:
        raise RuntimeError(
            "Either scenario or mode is required."
        )

    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    result = {
        "command": command,
        "exit_code": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
        "report_json": None,
        "report_text": None,
        "error_text": None,
    }

    if REPORT_JSON.exists():
        try:
            result["report_json"] = json.loads(
                REPORT_JSON.read_text(
                    encoding="utf-8"
                )
            )
        except Exception as exc:
            result["report_json_parse_error"] = (
                f"{type(exc).__name__}: {exc}"
            )
            result["report_json_raw"] = (
                REPORT_JSON.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            )

    if REPORT_TXT.exists():
        result["report_text"] = (
            REPORT_TXT.read_text(
                encoding="utf-8",
                errors="replace",
            )
        )

    if ERROR_TXT.exists():
        result["error_text"] = (
            ERROR_TXT.read_text(
                encoding="utf-8",
                errors="replace",
            )
        )

    cleanup_temp()

    return result


def summarize_run(
    payload: dict,
) -> dict:
    report = payload.get(
        "report_json"
    )

    if not isinstance(
        report,
        dict,
    ):
        return {
            "exit_code": payload.get(
                "exit_code"
            ),
            "status": "execution_error",
            "scenario_count": 0,
        }

    scenarios = report.get(
        "scenarios"
    ) or []

    first = (
        scenarios[0]
        if len(
            scenarios
        ) == 1
        else None
    )

    summary = {
        "exit_code": payload.get(
            "exit_code"
        ),
        "overall_score": report.get(
            "overall_score"
        ),
        "outcome_accuracy": report.get(
            "outcome_accuracy"
        ),
        "abstention_accuracy": report.get(
            "abstention_accuracy"
        ),
        "sufficient_evidence_accuracy": report.get(
            "sufficient_evidence_accuracy"
        ),
        "average_tool_calls": report.get(
            "average_tool_calls"
        ),
        "guard_rescue_count": report.get(
            "guard_rescue_count"
        ),
        "guard_rescue_rate": report.get(
            "guard_rescue_rate"
        ),
        "scenario_count": len(
            scenarios
        ),
    }

    if isinstance(
        first,
        dict,
    ):
        summary.update(
            {
                "scenario_key": first.get(
                    "scenario_key"
                ),
                "score": first.get(
                    "score"
                ),
                "final_status": first.get(
                    "final_status"
                ),
                "final_stop_reason": first.get(
                    "final_stop_reason"
                ),
                "failure_code": first.get(
                    "failure_code"
                ),
                "epistemic_guard_code": first.get(
                    "epistemic_guard_code"
                ),
                "guard_rescued": first.get(
                    "guard_rescued"
                ),
                "tool_call_count": first.get(
                    "tool_call_count"
                ),
                "outcome_correct": first.get(
                    "outcome_correct"
                ),
            }
        )

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run Investigation Intelligence Benchmark multiple times "
            "and emit exactly one merged JSON bundle."
        )
    )

    parser.add_argument(
        "--provider",
        default="bailian",
    )

    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        help=(
            "Scenario key. Repeat --scenario for multiple scenarios."
        ),
    )

    parser.add_argument(
        "--mode",
        choices=[
            "smoke",
            "full",
        ],
        default=None,
    )

    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--output",
        default=str(
            DEFAULT_OUTPUT
        ),
    )

    parser.add_argument(
        "--runner",
        default=(
            "scripts/dev/"
            "run_investigation_intelligence_benchmark_v1.py"
        ),
    )

    args = parser.parse_args()

    if args.repeat < 1:
        parser.error(
            "--repeat must be >= 1"
        )

    if bool(
        args.scenario
    ) == bool(
        args.mode
    ):
        parser.error(
            "Use either --scenario ... or --mode ..., not both."
        )

    runner = Path(
        args.runner
    )

    if not runner.exists():
        raise SystemExit(
            f"Benchmark runner not found: {runner}"
        )

    output = Path(
        args.output
    )

    targets = (
        [
            {
                "kind": "scenario",
                "value": scenario,
            }
            for scenario in args.scenario
        ]
        if args.scenario
        else [
            {
                "kind": "mode",
                "value": args.mode,
            }
        ]
    )

    bundle = {
        "schema_version": "investigation-benchmark-batch-v2",
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "provider": args.provider,
        "repeat": args.repeat,
        "targets": targets,
        "runs": [],
    }

    total = (
        len(
            targets
        )
        * args.repeat
    )

    current = 0

    for target in targets:
        for run_no in range(
            1,
            args.repeat + 1,
        ):
            current += 1

            print(
                "=" * 72
            )
            print(
                (
                    f"[{current}/{total}] "
                    f"{target['kind']}={target['value']} "
                    f"run={run_no}/{args.repeat}"
                )
            )
            print(
                "=" * 72
            )

            payload = run_once(
                runner=runner,
                provider=args.provider,
                scenario=(
                    target["value"]
                    if target[
                        "kind"
                    ] == "scenario"
                    else None
                ),
                mode=(
                    target["value"]
                    if target[
                        "kind"
                    ] == "mode"
                    else None
                ),
            )

            bundle[
                "runs"
            ].append(
                {
                    "target_kind": target[
                        "kind"
                    ],
                    "target": target[
                        "value"
                    ],
                    "run": run_no,
                    "summary": summarize_run(
                        payload
                    ),
                    "raw": payload,
                }
            )

    summaries = [
        run[
            "summary"
        ]
        for run in bundle[
            "runs"
        ]
    ]

    scenario_results = []

    for run in bundle[
        "runs"
    ]:
        report = (
            run.get(
                "raw",
                {},
            )
            .get(
                "report_json"
            )
        )

        if not isinstance(
            report,
            dict,
        ):
            continue

        scenarios = report.get(
            "scenarios"
        ) or []

        for scenario_result in scenarios:
            if not isinstance(
                scenario_result,
                dict,
            ):
                continue

            scenario_results.append(
                {
                    "target": run.get(
                        "target"
                    ),
                    "run": run.get(
                        "run"
                    ),
                    **scenario_result,
                }
            )

    execution_failure_codes = {
        "InvestigationLLMExecutionError",
        "InvestigationReasonerExecutionRetryError",
        "InvestigationLLMUnavailableError",
    }

    per_scenario = {}

    for item in scenario_results:
        key = item.get(
            "scenario_key"
        )

        if not key:
            continue

        bucket = per_scenario.setdefault(
            key,
            {
                "scenario_key": key,
                "runs": 0,
                "outcome_correct_count": 0,
                "guard_rescued_count": 0,
                "reasoner_error_count": 0,
                "execution_failure_count": 0,
                "tool_calls": [],
                "scores": [],
                "stop_reasons": {},
            },
        )

        bucket[
            "runs"
        ] += 1

        if item.get(
            "outcome_correct"
        ) is True:
            bucket[
                "outcome_correct_count"
            ] += 1

        if item.get(
            "guard_rescued"
        ) is True:
            bucket[
                "guard_rescued_count"
            ] += 1

        if (
            item.get(
                "final_stop_reason"
            )
            == "reasoner_error"
        ):
            bucket[
                "reasoner_error_count"
            ] += 1

        if item.get(
            "failure_code"
        ) in execution_failure_codes:
            bucket[
                "execution_failure_count"
            ] += 1

        tool_calls = item.get(
            "tool_call_count"
        )

        if isinstance(
            tool_calls,
            (
                int,
                float,
            ),
        ):
            bucket[
                "tool_calls"
            ].append(
                float(
                    tool_calls
                )
            )

        score = item.get(
            "score"
        )

        if isinstance(
            score,
            (
                int,
                float,
            ),
        ):
            bucket[
                "scores"
            ].append(
                float(
                    score
                )
            )

        reason = (
            item.get(
                "final_stop_reason"
            )
            or "<NONE>"
        )

        bucket[
            "stop_reasons"
        ][
            reason
        ] = (
            bucket[
                "stop_reasons"
            ].get(
                reason,
                0,
            )
            + 1
        )

    for bucket in per_scenario.values():
        runs = max(
            1,
            bucket[
                "runs"
            ],
        )

        bucket[
            "outcome_accuracy"
        ] = round(
            (
                bucket[
                    "outcome_correct_count"
                ]
                / runs
            )
            * 100.0,
            1,
        )

        bucket[
            "average_tool_calls"
        ] = (
            round(
                sum(
                    bucket[
                        "tool_calls"
                    ]
                )
                / len(
                    bucket[
                        "tool_calls"
                    ]
                ),
                3,
            )
            if bucket[
                "tool_calls"
            ]
            else None
        )

        bucket[
            "average_score"
        ] = (
            round(
                sum(
                    bucket[
                        "scores"
                    ]
                )
                / len(
                    bucket[
                        "scores"
                    ]
                ),
                3,
            )
            if bucket[
                "scores"
            ]
            else None
        )

        del bucket[
            "tool_calls"
        ]

        del bucket[
            "scores"
        ]

    scenario_count = len(
        scenario_results
    )

    outcome_correct_count = sum(
        1
        for item in scenario_results
        if item.get(
            "outcome_correct"
        )
        is True
    )

    reasoner_error_count = sum(
        1
        for item in scenario_results
        if item.get(
            "final_stop_reason"
        )
        == "reasoner_error"
    )

    execution_error_count = sum(
        1
        for item in scenario_results
        if item.get(
            "failure_code"
        )
        in execution_failure_codes
    )

    guard_rescued_count = sum(
        1
        for item in scenario_results
        if item.get(
            "guard_rescued"
        )
        is True
    )

    bundle[
        "aggregate"
    ] = {
        "run_count": len(
            summaries
        ),
        "scenario_result_count": scenario_count,
        "execution_error_count": execution_error_count,
        "reasoner_error_count": reasoner_error_count,
        "outcome_correct_count": outcome_correct_count,
        "outcome_accuracy": (
            round(
                (
                    outcome_correct_count
                    / scenario_count
                )
                * 100.0,
                1,
            )
            if scenario_count
            else None
        ),
        "guard_rescued_count": guard_rescued_count,
        "guard_rescue_rate": (
            round(
                (
                    guard_rescued_count
                    / scenario_count
                )
                * 100.0,
                1,
            )
            if scenario_count
            else None
        ),
        "per_scenario": sorted(
            per_scenario.values(),
            key=lambda item: item[
                "scenario_key"
            ],
        ),
    }

    output.write_text(
        json.dumps(
            bundle,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )

    cleanup_temp()

    print("")
    print(
        "=" * 72
    )
    print(
        "BATCH BENCHMARK FINISHED"
    )
    print(
        "=" * 72
    )
    print(
        f"Runs: {len(bundle['runs'])}"
    )
    print(
        f"Output: {output}"
    )
    print("")
    print(
        "Only upload this one bundle file."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
