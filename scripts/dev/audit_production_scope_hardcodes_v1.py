from __future__ import annotations

import ast
import hashlib
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "production-scope-hardcode-audit-v1"

AFTER_NAME = (
    "production_scope_hardcode_audit_v1_after.txt"
)

ERROR_NAME = (
    "production_scope_hardcode_audit_v1_error.txt"
)

SCAN_ROOTS = (
    "services/gateway/app",
    "services/agent_runtime/app",
    "packages/common/src/common",
)

DEMO_TOKENS = (
    "payment",
    "payment-api",
    "payment_config",
    "payment-config",
    "payment_secret",
    "payment-secret",
    "orders-db",
    "benchmark-lab",
    "production-a",
)

# These paths intentionally contain deterministic fixtures/examples and are
# not production routing inputs.
ALLOWED_PATH_MARKERS = (
    "/evaluation/",
    "/providers/mock",
    "/provider/mock",
    "/mock/",
    "/mocks/",
)

# Demo entrypoints/examples deserve review but are not automatically treated
# as a production-scope blocker.
REVIEW_PATH_MARKERS = (
    "/main.py",
    "/examples/",
    "/example/",
    "/demo/",
    "/scenario",
)

SCOPE_KEYWORDS = {
    "namespace",
    "cluster",
    "resource",
    "target",
    "resource_name",
    "pod",
    "pod_name",
    "service",
    "service_name",
    "workload",
    "workload_name",
}


@dataclass(frozen=True)
class Finding:
    severity: str
    path: str
    line: int
    column: int
    token: str
    context: str
    literal: str


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


def normalize_path(
    path: Path,
    root: Path,
) -> str:
    return (
        "/"
        + str(
            path.relative_to(
                root
            )
        )
        .replace("\\", "/")
    )


def bounded(
    value: str,
    limit: int = 220,
) -> str:
    compact = " ".join(
        value.split()
    )

    if len(
        compact
    ) <= limit:
        return compact

    return (
        compact[
            : limit - 3
        ]
        + "..."
    )


def classify_path(
    normalized_path: str,
) -> str | None:
    lowered = normalized_path.lower()

    if any(
        marker in lowered
        for marker in ALLOWED_PATH_MARKERS
    ):
        return "ALLOWED_FIXTURE"

    if any(
        marker in lowered
        for marker in REVIEW_PATH_MARKERS
    ):
        return "REVIEW"

    return None


def build_parent_map(
    tree: ast.AST,
) -> dict[ast.AST, ast.AST]:
    parents = {}

    for parent in ast.walk(
        tree
    ):
        for child in ast.iter_child_nodes(
            parent
        ):
            parents[
                child
            ] = parent

    return parents


def string_context(
    node: ast.Constant,
    parents: dict[ast.AST, ast.AST],
) -> tuple[str, bool]:
    parent = parents.get(
        node
    )

    if parent is None:
        return (
            "module_literal",
            False,
        )

    if isinstance(
        parent,
        ast.keyword,
    ):
        name = (
            parent.arg
            or "<kwargs>"
        )

        return (
            f"keyword:{name}",
            name in SCOPE_KEYWORDS,
        )

    if isinstance(
        parent,
        ast.Assign,
    ):
        targets = []

        for target in parent.targets:
            if isinstance(
                target,
                ast.Name,
            ):
                targets.append(
                    target.id
                )

            elif isinstance(
                target,
                ast.Attribute,
            ):
                targets.append(
                    target.attr
                )

        joined = ",".join(
            targets
        ) or "assignment"

        return (
            f"assignment:{joined}",
            any(
                item in SCOPE_KEYWORDS
                for item in targets
            ),
        )

    if isinstance(
        parent,
        ast.AnnAssign,
    ):
        target = parent.target

        name = None

        if isinstance(
            target,
            ast.Name,
        ):
            name = target.id

        elif isinstance(
            target,
            ast.Attribute,
        ):
            name = target.attr

        return (
            f"annotated_assignment:{name or '?'}",
            (
                name in SCOPE_KEYWORDS
                if name is not None
                else False
            ),
        )

    if isinstance(
        parent,
        ast.Dict,
    ):
        # Identify whether this literal is the value paired with a scope-like key.
        for key, value in zip(
            parent.keys,
            parent.values,
        ):
            if value is not node:
                continue

            if (
                isinstance(
                    key,
                    ast.Constant,
                )
                and isinstance(
                    key.value,
                    str,
                )
            ):
                key_name = key.value

                return (
                    f"dict_value:{key_name}",
                    key_name in SCOPE_KEYWORDS,
                )

        return (
            "dict_literal",
            False,
        )

    if isinstance(
        parent,
        ast.Call,
    ):
        return (
            "call_argument",
            False,
        )

    if isinstance(
        parent,
        ast.Expr,
    ):
        return (
            "expression_or_docstring",
            False,
        )

    return (
        type(
            parent
        ).__name__,
        False,
    )


def scan_file(
    *,
    path: Path,
    root: Path,
) -> list[Finding]:
    text = path.read_text(
        encoding="utf-8-sig",
        errors="strict",
    )

    try:
        tree = ast.parse(
            text,
            filename=str(
                path
            ),
        )
    except SyntaxError as exc:
        raise RuntimeError(
            f"Cannot parse {path}: {exc}"
        ) from exc

    parents = build_parent_map(
        tree
    )

    normalized_path = normalize_path(
        path,
        root,
    )

    path_class = classify_path(
        normalized_path
    )

    findings = []

    for node in ast.walk(
        tree
    ):
        if not (
            isinstance(
                node,
                ast.Constant,
            )
            and isinstance(
                node.value,
                str,
            )
        ):
            continue

        lowered = node.value.lower()

        matched_tokens = [
            token
            for token in DEMO_TOKENS
            if token.lower()
            in lowered
        ]

        if not matched_tokens:
            continue

        context, scope_sensitive = (
            string_context(
                node,
                parents,
            )
        )

        if path_class == "ALLOWED_FIXTURE":
            severity = "ALLOWED_FIXTURE"

        elif (
            path_class == "REVIEW"
            or context
            == "expression_or_docstring"
        ):
            severity = "REVIEW"

        elif scope_sensitive:
            severity = (
                "BLOCKING_CANDIDATE"
            )

        else:
            severity = "REVIEW"

        for token in matched_tokens:
            findings.append(
                Finding(
                    severity=severity,
                    path=normalized_path[
                        1:
                    ],
                    line=getattr(
                        node,
                        "lineno",
                        0,
                    ),
                    column=getattr(
                        node,
                        "col_offset",
                        0,
                    ),
                    token=token,
                    context=context,
                    literal=bounded(
                        node.value
                    ),
                )
            )

    return findings


def git_status(
    root: Path,
) -> str:
    process = subprocess.run(
        [
            "git",
            "status",
            "--short",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    return (
        process.stdout.rstrip()
        or "<CLEAN_OR_EMPTY>"
    )


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after = root / AFTER_NAME
    error = root / ERROR_NAME

    for output in (
        after,
        error,
    ):
        try:
            output.unlink()
        except FileNotFoundError:
            pass

    try:
        all_files = []

        for relative in SCAN_ROOTS:
            scan_root = root / relative

            if not scan_root.exists():
                raise RuntimeError(
                    f"Scan root does not exist: {relative}"
                )

            all_files.extend(
                sorted(
                    path
                    for path
                    in scan_root.rglob(
                        "*.py"
                    )
                    if "__pycache__"
                    not in path.parts
                )
            )

        findings = []

        for path in all_files:
            findings.extend(
                scan_file(
                    path=path,
                    root=root,
                )
            )

        severity_order = {
            "BLOCKING_CANDIDATE": 0,
            "REVIEW": 1,
            "ALLOWED_FIXTURE": 2,
        }

        findings.sort(
            key=lambda item: (
                severity_order[
                    item.severity
                ],
                item.path,
                item.line,
                item.token,
            )
        )

        counts = {
            severity: sum(
                1
                for item in findings
                if item.severity
                == severity
            )
            for severity
            in severity_order
        }

        unique_blocking_files = sorted(
            {
                item.path
                for item in findings
                if item.severity
                == "BLOCKING_CANDIDATE"
            }
        )

        unique_review_files = sorted(
            {
                item.path
                for item in findings
                if item.severity
                == "REVIEW"
            }
        )

        unique_allowed_files = sorted(
            {
                item.path
                for item in findings
                if item.severity
                == "ALLOWED_FIXTURE"
            }
        )

        lines = [
            "Production Scope Hardcode Audit v1",
            f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
            "",
            "Purpose:",
            "- detect Demo/Benchmark scope literals leaking into production-capable runtime paths",
            "- distinguish allowed deterministic fixtures from production review/blocking candidates",
            "- make no code changes",
            "",
            "Scan roots:",
            *[
                f"- {item}"
                for item in SCAN_ROOTS
            ],
            "",
            "Demo tokens:",
            "- "
            + ", ".join(
                DEMO_TOKENS
            ),
            "",
            "Classification:",
            "- BLOCKING_CANDIDATE: demo literal is used directly in a scope-like assignment/keyword/dict field in a production-capable path",
            "- REVIEW: demo literal appears in a production-capable path but is not proven to control runtime scope",
            "- ALLOWED_FIXTURE: deterministic evaluation/mock fixture path",
            "",
            f"Python files scanned: {len(all_files)}",
            f"Total findings: {len(findings)}",
            f"BLOCKING_CANDIDATE: {counts['BLOCKING_CANDIDATE']}",
            f"REVIEW: {counts['REVIEW']}",
            f"ALLOWED_FIXTURE: {counts['ALLOWED_FIXTURE']}",
            "",
            "Blocking candidate files:",
        ]

        if unique_blocking_files:
            lines.extend(
                f"- {item}"
                for item
                in unique_blocking_files
            )
        else:
            lines.append(
                "- <NONE>"
            )

        lines.extend(
            [
                "",
                "Review candidate files:",
            ]
        )

        if unique_review_files:
            lines.extend(
                f"- {item}"
                for item
                in unique_review_files
            )
        else:
            lines.append(
                "- <NONE>"
            )

        lines.extend(
            [
                "",
                "Allowed fixture files:",
            ]
        )

        if unique_allowed_files:
            lines.extend(
                f"- {item}"
                for item
                in unique_allowed_files
            )
        else:
            lines.append(
                "- <NONE>"
            )

        for severity in (
            "BLOCKING_CANDIDATE",
            "REVIEW",
            "ALLOWED_FIXTURE",
        ):
            lines.extend(
                [
                    "",
                    "=" * 120,
                    severity,
                    "=" * 120,
                    "",
                ]
            )

            items = [
                item
                for item in findings
                if item.severity
                == severity
            ]

            if not items:
                lines.append(
                    "<NONE>"
                )
                continue

            for item in items:
                lines.extend(
                    [
                        (
                            f"{item.path}:"
                            f"{item.line}:"
                            f"{item.column}"
                        ),
                        f"token={item.token}",
                        f"context={item.context}",
                        f"literal={item.literal}",
                        "",
                    ]
                )

        lines.extend(
            [
                "",
                "=" * 120,
                "GIT STATUS (READ-ONLY)",
                "=" * 120,
                "",
                git_status(
                    root
                ),
                "",
                "=" * 120,
                "RESULT",
                "=" * 120,
                "",
            ]
        )

        if counts[
            "BLOCKING_CANDIDATE"
        ] == 0:
            lines.extend(
                [
                    "NO DIRECT PRODUCTION-SCOPE BLOCKER DETECTED BY THIS STATIC AUDIT.",
                    "",
                    "Any REVIEW findings still require human inspection because a string literal can be an example, prompt text, fallback, or real runtime default.",
                ]
            )
        else:
            lines.extend(
                [
                    "PRODUCTION-SCOPE BLOCKING CANDIDATES DETECTED.",
                    "",
                    "Do not treat the platform as production-scope-clean until each blocking candidate is reviewed or removed.",
                ]
            )

        after.write_text(
            "\n".join(
                lines
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print(
            "PRODUCTION SCOPE HARDCODE AUDIT V1 FINISHED"
        )
        print("=" * 72)
        print("")
        print(
            f"BLOCKING_CANDIDATE={counts['BLOCKING_CANDIDATE']}"
        )
        print(
            f"REVIEW={counts['REVIEW']}"
        )
        print(
            f"ALLOWED_FIXTURE={counts['ALLOWED_FIXTURE']}"
        )
        print("")
        print("Upload only:")
        print(after)

        return 0

    except Exception as exc:
        error.write_text(
            "\n".join(
                [
                    "Production Scope Hardcode Audit v1 FAILED",
                    f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
                    "",
                    f"{type(exc).__name__}: {exc}",
                    "",
                    traceback.format_exc(),
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print(
            "PRODUCTION SCOPE HARDCODE AUDIT V1 FAILED"
        )
        print("=" * 72)
        print("")
        print("Upload only:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
