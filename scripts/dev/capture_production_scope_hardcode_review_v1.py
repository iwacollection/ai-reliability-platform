from __future__ import annotations

import hashlib
import traceback
from datetime import datetime
from pathlib import Path


OUTPUT_NAME = (
    "production_scope_hardcode_review_snapshot_v1.txt"
)

ERROR_NAME = (
    "production_scope_hardcode_review_snapshot_v1_error.txt"
)

TARGETS = (
    "services/gateway/app/api/webhook.py",
    (
        "services/agent_runtime/app/"
        "investigation/evaluation_fixture_runtime.py"
    ),
    "services/agent_runtime/app/main.py",
    "services/agent_runtime/app/change/argocd.py",
    "services/agent_runtime/app/action/planner.py",
)

TOKENS = (
    "payment",
    "payment-api",
    "benchmark-lab",
    "production-a",
    "orders-db",
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
        ):
            return candidate

    raise RuntimeError(
        "Repository root not found."
    )


def sha256_bytes(
    data: bytes,
) -> str:
    return hashlib.sha256(
        data
    ).hexdigest()


def line_hits(
    text: str,
) -> list[str]:
    rows = text.splitlines()
    hits = []

    for number, row in enumerate(
        rows,
        start=1,
    ):
        lowered = row.lower()

        matched = [
            token
            for token in TOKENS
            if token.lower()
            in lowered
        ]

        if not matched:
            continue

        hits.append(
            (
                f"L{number}: "
                f"tokens={matched} "
                f"text={row}"
            )
        )

    return hits


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    output = root / OUTPUT_NAME
    error = root / ERROR_NAME

    for item in (
        output,
        error,
    ):
        try:
            item.unlink()
        except FileNotFoundError:
            pass

    report = [
        "Production Scope Hardcode Review Snapshot v1",
        (
            "GeneratedAt: "
            + datetime.now().astimezone().isoformat()
        ),
        "",
        "Purpose:",
        "- capture complete current files before any hardcode fix",
        "- preserve SHA256 preflight for safe installer generation",
        "- keep fixture/demo and production-path decisions separate",
        "",
    ]

    try:
        for relative in TARGETS:
            path = root / relative

            if not path.exists():
                raise RuntimeError(
                    f"Required file missing: {relative}"
                )

            raw = path.read_bytes()
            text = raw.decode(
                "utf-8-sig",
                errors="strict",
            )

            report.extend(
                [
                    "=" * 120,
                    relative,
                    "=" * 120,
                    (
                        "sha256="
                        + sha256_bytes(
                            raw
                        )
                    ),
                    (
                        "lines="
                        + str(
                            len(
                                text.splitlines()
                            )
                        )
                    ),
                    "",
                    "HARDCODE TOKEN HITS",
                    "-" * 120,
                ]
            )

            hits = line_hits(
                text
            )

            if hits:
                report.extend(
                    hits
                )
            else:
                report.append(
                    "<NONE>"
                )

            report.extend(
                [
                    "",
                    "FULL CURRENT FILE",
                    "-" * 120,
                    text.rstrip(),
                    "",
                ]
            )

        report.extend(
            [
                "=" * 120,
                "RESULT",
                "=" * 120,
                "",
                "PASSED",
                "",
                (
                    "No source file was modified. "
                    "This is a read-only snapshot."
                ),
            ]
        )

        output.write_text(
            "\n".join(
                report
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print(
            "=" * 72
        )
        print(
            "PRODUCTION SCOPE HARDCODE REVIEW SNAPSHOT V1 PASSED"
        )
        print(
            "=" * 72
        )
        print()
        print(
            "No source file was modified."
        )
        print()
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
                    (
                        "Production Scope Hardcode "
                        "Review Snapshot v1 FAILED"
                    ),
                    (
                        "GeneratedAt: "
                        + datetime.now().astimezone().isoformat()
                    ),
                    "",
                    (
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "",
                    traceback.format_exc(),
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print(
            "=" * 72
        )
        print(
            "PRODUCTION SCOPE HARDCODE REVIEW SNAPSHOT V1 FAILED"
        )
        print(
            "=" * 72
        )
        print()
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
