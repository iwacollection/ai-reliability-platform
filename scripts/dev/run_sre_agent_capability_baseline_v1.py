from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path


TEXT_REPORT = (
    "sre_agent_capability_baseline_v1_report.txt"
)

JSON_REPORT = (
    "sre_agent_capability_baseline_v1_report.json"
)

ERROR_REPORT = (
    "sre_agent_capability_baseline_v1_error.txt"
)


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


def install_import_paths(
    root: Path,
) -> None:
    for candidate in reversed(
        [
            root,
            root / "packages" / "common" / "src",
        ]
    ):
        value = str(
            candidate
        )

        if value not in sys.path:
            sys.path.insert(
                0,
                value,
            )


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    install_import_paths(
        root
    )

    text_path = (
        root
        / TEXT_REPORT
    )

    json_path = (
        root
        / JSON_REPORT
    )

    error_path = (
        root
        / ERROR_REPORT
    )

    for path in (
        text_path,
        json_path,
        error_path,
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    try:
        from services.agent_runtime.app.evaluation.capability import (
            build_report,
            render_text_report,
        )

        report = build_report(
            root
        )

        text_path.write_text(
            render_text_report(
                report
            ),
            encoding="utf-8",
            newline="\n",
        )

        json_path.write_text(
            json.dumps(
                report.model_dump(
                    mode="json"
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        passed_exams = sum(
            1
            for item in report.behavioral_exams
            if item.passed
        )

        print("=" * 72)
        print(
            "SRE AGENT CAPABILITY BASELINE V1 COMPLETED"
        )
        print("=" * 72)
        print("")
        print(
            f"Overall: {report.overall_score:.1f}/100"
        )
        print(
            f"Level: {report.overall_level}"
        )
        print(
            "Behavioral exams: "
            f"{passed_exams}/{len(report.behavioral_exams)} passed"
        )
        print("")
        print("Upload BOTH:")
        print(text_path)
        print(json_path)

        return 0

    except Exception as exc:
        error_path.write_text(
            (
                "SRE Agent Capability Baseline v1 FAILED\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                + traceback.format_exc()
            ),
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print(
            "SRE AGENT CAPABILITY BASELINE V1 FAILED"
        )
        print("=" * 72)
        print("")
        print("Upload:")
        print(error_path)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
