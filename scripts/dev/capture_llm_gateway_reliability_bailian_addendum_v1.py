from __future__ import annotations

import ast
import hashlib
import os
import subprocess
import traceback
from datetime import datetime
from pathlib import Path


OUTPUT_NAME = (
    "llm_gateway_reliability_bailian_addendum_v1.txt"
)

ERROR_NAME = (
    "llm_gateway_reliability_bailian_addendum_v1_error.txt"
)

FILES = (
    "services/agent_runtime/app/llm/providers/bailian_compatible.py",
    "services/agent_runtime/app/llm/factory.py",
    "services/agent_runtime/app/llm/provider_factory.py",
    "services/agent_runtime/tests/test_bailian_provider.py",
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


def sha256_text(
    value: str,
) -> str:
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


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
            f"line={exc.lineno}"
        )


def run_command(
    root: Path,
    command: list[str],
) -> tuple[int, str, str]:
    process = subprocess.run(
        command,
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


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    output = root / OUTPUT_NAME
    error = root / ERROR_NAME

    for path in (
        output,
        error,
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    try:
        paths = []

        for relative in FILES:
            path = root / relative

            if not path.exists():
                raise RuntimeError(
                    f"Required file missing: {relative}"
                )

            paths.append(
                path
            )

        report = [
            "=" * 120,
            "LLM GATEWAY RELIABILITY - BAILIAN CURRENT ADDENDUM v1",
            "=" * 120,
            "",
            "GeneratedAt: "
            + datetime.now().astimezone().isoformat(),
            "RepositoryRoot: "
            + str(root),
            "",
            "Purpose:",
            "- complete the current Gateway snapshot with the actual Bailian provider path used by live benchmarks",
            "- capture provider registry/factory composition and Bailian no-network tests",
            "- enable Transport Reliability changes without stale-code patching",
            "",
            "Safety:",
            "- read-only",
            "- no external LLM request",
            "- no API key value is printed",
            "- environment values are never printed",
            "",
            "ENVIRONMENT PRESENCE ONLY",
            "-" * 120,
            (
                "DASHSCOPE_API_KEY_PRESENT="
                + str(
                    bool(
                        os.getenv(
                            "DASHSCOPE_API_KEY"
                        )
                    )
                )
            ),
            (
                "BAILIAN_BASE_URL_PRESENT="
                + str(
                    bool(
                        os.getenv(
                            "BAILIAN_BASE_URL"
                        )
                    )
                )
            ),
            (
                "BAILIAN_MODEL_PRESENT="
                + str(
                    bool(
                        os.getenv(
                            "BAILIAN_MODEL"
                        )
                    )
                )
            ),
            "",
            "STRUCTURAL INDEX",
            "=" * 120,
        ]

        for path in paths:
            relative = str(
                path.relative_to(
                    root
                )
            ).replace(
                "\\",
                "/",
            )

            source = read_text(
                path
            )

            report.extend(
                [
                    "",
                    relative,
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
                    "ast="
                    + ast_status(
                        source
                    ),
                ]
            )

        status_code, status_out, status_err = (
            run_command(
                root,
                [
                    "git",
                    "status",
                    "--short",
                    "--",
                    *[
                        str(
                            path.relative_to(
                                root
                            )
                        )
                        for path in paths
                    ],
                ],
            )
        )

        report.extend(
            [
                "",
                "GIT STATUS",
                "=" * 120,
                f"ExitCode: {status_code}",
                status_out.rstrip()
                or "<EMPTY>",
                "",
                "STDERR:",
                status_err.rstrip()
                or "<EMPTY>",
                "",
                "FULL CURRENT FILE CONTENT",
                "=" * 120,
            ]
        )

        for path in paths:
            relative = str(
                path.relative_to(
                    root
                )
            ).replace(
                "\\",
                "/",
            )

            source = read_text(
                path
            )

            report.extend(
                [
                    "",
                    "=" * 120,
                    relative,
                    "=" * 120,
                    "sha256="
                    + sha256_text(
                        source
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
                f"files_captured={len(paths)}",
                "",
                "No repository source file was modified.",
                "No external request was sent.",
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
            "BAILIAN CURRENT ADDENDUM V1 COMPLETED"
        )
        print(
            "=" * 72
        )
        print("")
        print(
            "Upload only:"
        )
        print(
            output
        )

        return 0

    except Exception as exc:
        error.write_text(
            "\n".join(
                [
                    "Bailian Current Addendum v1 FAILED",
                    "",
                    f"{type(exc).__name__}: {exc}",
                    "",
                    traceback.format_exc(),
                ]
            ),
            encoding="utf-8",
            newline="\n",
        )

        print(
            "=" * 72
        )
        print(
            "BAILIAN CURRENT ADDENDUM V1 FAILED"
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
