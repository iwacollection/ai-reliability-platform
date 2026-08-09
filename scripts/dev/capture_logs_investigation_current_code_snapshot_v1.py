from __future__ import annotations

import ast
import hashlib
import subprocess
import traceback
from datetime import datetime
from pathlib import Path


OUTPUT_NAME = (
    "logs_investigation_current_code_snapshot_v1.txt"
)

ERROR_NAME = (
    "logs_investigation_current_code_snapshot_v1_error.txt"
)


CORE_FILES = (
    "services/agent_runtime/app/investigation/models.py",
    "services/agent_runtime/app/investigation/probes.py",
    "services/agent_runtime/app/investigation/factory.py",
    "services/agent_runtime/app/investigation/reasoner.py",
    "services/agent_runtime/app/investigation/coordinator.py",
    "services/agent_runtime/app/investigation/epistemic_guard.py",
    "services/agent_runtime/app/investigation/evidence_time.py",
    "services/agent_runtime/app/tools/base.py",
    "services/agent_runtime/app/tools/manager.py",
    "services/agent_runtime/app/tools/registry.py",
    "services/agent_runtime/app/tools/factory.py",
    "services/agent_runtime/app/tools/kubernetes/tool.py",
)

TEST_PATTERNS = (
    "services/agent_runtime/tests/test_investigation*.py",
    "services/agent_runtime/tests/test_kubernetes*.py",
    "services/agent_runtime/tests/test_tool*.py",
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
        "Repository root not found. "
        "Run this script from inside ai-reliability-platform."
    )


def normalize_text(
    value: str,
) -> str:
    return (
        value
        .replace(
            "\r\n",
            "\n",
        )
        .replace(
            "\r",
            "\n",
        )
    )


def read_source(
    path: Path,
) -> str:
    return normalize_text(
        path.read_text(
            encoding="utf-8-sig",
            errors="replace",
        )
    )


def sha256_text(
    value: str,
) -> str:
    return hashlib.sha256(
        value.encode(
            "utf-8",
        )
    ).hexdigest()


def line_count(
    value: str,
) -> int:
    if not value:
        return 0

    return len(
        value.splitlines()
    )


def ast_status(
    value: str,
) -> str:
    try:
        ast.parse(
            value
        )
        return "PASSED"

    except SyntaxError as exc:
        return (
            "FAILED: "
            f"{exc.msg} "
            f"(line={exc.lineno}, offset={exc.offset})"
        )


def collect_test_files(
    root: Path,
) -> list[Path]:
    found: set[Path] = set()

    for pattern in TEST_PATTERNS:
        found.update(
            root.glob(
                pattern
            )
        )

    return sorted(
        (
            path
            for path in found
            if path.is_file()
        ),
        key=lambda path: str(
            path.relative_to(
                root
            )
        ).lower(),
    )


def run_git(
    root: Path,
    args: list[str],
) -> tuple[int, str, str]:
    process = subprocess.run(
        [
            "git",
            *args,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    return (
        process.returncode,
        normalize_text(
            process.stdout
        ),
        normalize_text(
            process.stderr
        ),
    )


def class_index(
    source: str,
) -> list[str]:
    try:
        tree = ast.parse(
            source
        )

    except SyntaxError:
        return []

    result = []

    for node in tree.body:
        if isinstance(
            node,
            (
                ast.ClassDef,
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        ):
            result.append(
                (
                    f"{type(node).__name__}: "
                    f"{node.name} "
                    f"(line {getattr(node, 'lineno', '?')})"
                )
            )

    return result


def symbol_reference_summary(
    root: Path,
) -> list[str]:
    terms = (
        "InvestigationProbe",
        "KUBERNETES_POD_STATE",
        "ReadOnlyInvestigationProbeExecutor",
        "KubernetesTool",
        'tools.call(',
        'action="describe"',
        "epistemic_guard_code",
        "missing_evidence",
    )

    search_roots = (
        root
        / "services"
        / "agent_runtime"
        / "app",
        root
        / "services"
        / "agent_runtime"
        / "tests",
    )

    lines: list[str] = []

    for term in terms:
        lines.append(
            f"TERM: {term}"
        )

        matches = []

        for search_root in search_roots:
            if not search_root.exists():
                continue

            for path in search_root.rglob(
                "*.py"
            ):
                try:
                    source = read_source(
                        path
                    )
                except OSError:
                    continue

                for number, line in enumerate(
                    source.splitlines(),
                    start=1,
                ):
                    if term in line:
                        matches.append(
                            (
                                str(
                                    path.relative_to(
                                        root
                                    )
                                )
                                + ":"
                                + str(
                                    number
                                )
                                + ": "
                                + line.strip()
                            )
                        )

        if matches:
            lines.extend(
                matches
            )
        else:
            lines.append(
                "<NO MATCH>"
            )

        lines.append(
            ""
        )

    return lines


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    output = (
        root
        / OUTPUT_NAME
    )

    error = (
        root
        / ERROR_NAME
    )

    for path in (
        output,
        error,
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    try:
        core_paths = [
            root
            / relative
            for relative in CORE_FILES
        ]

        test_paths = (
            collect_test_files(
                root
            )
        )

        paths: list[Path] = []

        for path in (
            *core_paths,
            *test_paths,
        ):
            if (
                path.exists()
                and path.is_file()
                and path not in paths
            ):
                paths.append(
                    path
                )

        missing = [
            relative
            for relative in CORE_FILES
            if not (
                root
                / relative
            ).exists()
        ]

        report = [
            "=" * 120,
            "LOGS INVESTIGATION CURRENT CODE SNAPSHOT v1",
            "=" * 120,
            "",
            (
                "GeneratedAt: "
                + datetime.now()
                .astimezone()
                .isoformat()
            ),
            (
                "RepositoryRoot: "
                + str(
                    root
                )
            ),
            "",
            "Purpose:",
            "- capture the complete CURRENT files before Logs Investigation v1 modifies anything",
            "- preserve SHA256 + line counts so later patches can be tied to the exact source reviewed",
            "- include Investigation, ToolManager, Kubernetes Tool and directly related tests",
            "- perform read-only structural inspection only",
            "",
            "Safety:",
            "- does not modify repository source files",
            "- does not read .env",
            "- does not read process environment values",
            "- does not call LLM",
            "- does not call Kubernetes",
            "- does not call Prometheus",
            "- does not run kubectl",
            "- does not create Actions or Approvals",
            "",
            "FILES_SELECTED",
            "-" * 120,
        ]

        for path in paths:
            report.append(
                str(
                    path.relative_to(
                        root
                    )
                )
            )

        report.extend(
            [
                "",
                "MISSING_OPTIONAL_OR_EXPECTED_FILES",
                "-" * 120,
            ]
        )

        if missing:
            report.extend(
                missing
            )
        else:
            report.append(
                "<NONE>"
            )

        report.extend(
            [
                "",
                "STRUCTURAL INDEX",
                "-" * 120,
            ]
        )

        for path in paths:
            relative = str(
                path.relative_to(
                    root
                )
            )

            source = read_source(
                path
            )

            report.extend(
                [
                    "",
                    relative,
                    (
                        "sha256="
                        + sha256_text(
                            source
                        )
                    ),
                    (
                        "lines="
                        + str(
                            line_count(
                                source
                            )
                        )
                    ),
                    (
                        "bytes_utf8="
                        + str(
                            len(
                                source.encode(
                                    "utf-8"
                                )
                            )
                        )
                    ),
                    (
                        "ast="
                        + ast_status(
                            source
                        )
                    ),
                ]
            )

            symbols = class_index(
                source
            )

            if symbols:
                report.extend(
                    [
                        "symbols:",
                        *[
                            "  "
                            + item
                            for item
                            in symbols
                        ],
                    ]
                )

        report.extend(
            [
                "",
                "SYMBOL REFERENCE SUMMARY",
                "=" * 120,
                "",
                *symbol_reference_summary(
                    root
                ),
            ]
        )

        rel_args = [
            str(
                path.relative_to(
                    root
                )
            )
            for path in paths
        ]

        status_code, status_out, status_err = (
            run_git(
                root,
                [
                    "status",
                    "--short",
                    "--",
                    *rel_args,
                ],
            )
        )

        report.extend(
            [
                "",
                "GIT STATUS FOR SNAPSHOT FILES",
                "=" * 120,
                "",
                (
                    "ExitCode: "
                    + str(
                        status_code
                    )
                ),
                status_out.rstrip()
                or "<EMPTY>",
                "",
                "STDERR:",
                status_err.rstrip()
                or "<EMPTY>",
            ]
        )

        report.extend(
            [
                "",
                "FULL CURRENT FILE CONTENT",
                "=" * 120,
                "",
            ]
        )

        for path in paths:
            relative = str(
                path.relative_to(
                    root
                )
            )

            source = read_source(
                path
            )

            report.extend(
                [
                    "",
                    "=" * 120,
                    relative,
                    "=" * 120,
                    (
                        "sha256="
                        + sha256_text(
                            source
                        )
                    ),
                    (
                        "lines="
                        + str(
                            line_count(
                                source
                            )
                        )
                    ),
                    "",
                    source.rstrip(
                        "\n"
                    ),
                    "",
                ]
            )

        report.extend(
            [
                "",
                "=" * 120,
                "RESULT",
                "=" * 120,
                "",
                "SNAPSHOT=COMPLETED",
                (
                    "files_captured="
                    + str(
                        len(
                            paths
                        )
                    )
                ),
                (
                    "related_tests_captured="
                    + str(
                        len(
                            test_paths
                        )
                    )
                ),
                "",
                "No repository source file was modified.",
                "",
            ]
        )

        output.write_text(
            "\n".join(
                report
            ),
            encoding="utf-8",
            newline="\n",
        )

        print(
            "=" * 72
        )
        print(
            "LOGS INVESTIGATION CURRENT CODE SNAPSHOT V1 COMPLETED"
        )
        print(
            "=" * 72
        )
        print("")
        print(
            f"Files captured: {len(paths)}"
        )
        print(
            f"Related tests captured: {len(test_paths)}"
        )
        print("")
        print(
            "Upload:"
        )
        print(
            output
        )

        return 0

    except Exception as exc:
        error.write_text(
            (
                "Logs Investigation Current Code Snapshot v1 FAILED\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                + traceback.format_exc()
            ),
            encoding="utf-8",
            newline="\n",
        )

        print(
            "=" * 72
        )
        print(
            "LOGS INVESTIGATION CURRENT CODE SNAPSHOT V1 FAILED"
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
