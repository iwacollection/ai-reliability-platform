from __future__ import annotations

import hashlib
import traceback
from datetime import datetime
from pathlib import Path


OUTPUT_NAME = (
    "multi_cluster_router_current_code_snapshot_v1.txt"
)

ERROR_NAME = (
    "multi_cluster_router_current_code_snapshot_v1_error.txt"
)

CORE_CANDIDATES = (
    "services/agent_runtime/app/tools/registry.py",
    "services/agent_runtime/app/tools/manager.py",
    "services/agent_runtime/app/tools/kubernetes/tool.py",
    "services/agent_runtime/app/tools/kubernetes/change_tool.py",
    "services/agent_runtime/app/investigation/probes.py",
    "services/agent_runtime/app/runtime/agent_runtime.py",
    "services/agent_runtime/app/runtime/runtime.py",
    "services/agent_runtime/app/runtime.py",
    "services/agent_runtime/app/main.py",
)

DISCOVERY_TOKENS = (
    "ToolRegistry(",
    "ToolManager(",
    "KubernetesTool(",
    "KubernetesChangeTool(",
    "cluster_name=",
    "KUBERNETES_",
    "api_url=",
    "bearer_token=",
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


def sha256_bytes(
    data: bytes,
) -> str:
    return hashlib.sha256(
        data
    ).hexdigest()


def normalized_relative(
    path: Path,
    root: Path,
) -> str:
    return str(
        path.relative_to(
            root
        )
    ).replace(
        "\\",
        "/",
    )


def discover_related_files(
    root: Path,
) -> list[Path]:
    app_root = (
        root
        / "services"
        / "agent_runtime"
        / "app"
    )

    discovered = []

    for path in sorted(
        app_root.rglob(
            "*.py"
        )
    ):
        if "__pycache__" in path.parts:
            continue

        text = path.read_text(
            encoding="utf-8-sig",
            errors="strict",
        )

        if any(
            token in text
            for token in DISCOVERY_TOKENS
        ):
            discovered.append(
                path
            )

    return discovered


def line_hits(
    text: str,
) -> list[str]:
    hits = []

    for number, row in enumerate(
        text.splitlines(),
        start=1,
    ):
        matched = [
            token
            for token in DISCOVERY_TOKENS
            if token in row
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

    for path in (
        output,
        error,
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    try:
        selected = {}

        for relative in CORE_CANDIDATES:
            path = root / relative

            if path.exists():
                selected[
                    normalized_relative(
                        path,
                        root,
                    )
                ] = path

        for path in discover_related_files(
            root
        ):
            selected[
                normalized_relative(
                    path,
                    root,
                )
            ] = path

        if not selected:
            raise RuntimeError(
                "No multi-cluster routing related Python files were found."
            )

        report = [
            "Multi-Cluster Kubernetes Router Current Code Snapshot v1",
            (
                "GeneratedAt: "
                + datetime.now().astimezone().isoformat()
            ),
            "",
            "Purpose:",
            "- capture the complete current ToolRegistry / ToolManager / Kubernetes wiring before Router v1",
            "- preserve exact SHA256 values for stale-source protection",
            "- discover every current Agent Runtime file that constructs or configures Kubernetes tools",
            "- make no source-code changes",
            "",
            "Production Scope Integrity baseline expected:",
            "- scope.cluster is now propagated into Kubernetes investigation reads",
            "- a cluster-bound KubernetesTool fails closed on mismatched cluster",
            "",
            "Router v1 design questions this snapshot will answer:",
            "- where ToolRegistry ownership lives",
            "- whether ToolManager supports per-call routing or only name -> singleton tool",
            "- where KubernetesTool is constructed",
            "- where cluster credentials / API URL / cluster_name are configured",
            "- how kubernetes_change shares or depends on KubernetesTool",
            "- how to preserve current single-cluster behavior while adding explicit cluster routing",
            "",
            "Selected files:",
        ]

        for relative in sorted(
            selected
        ):
            report.append(
                f"- {relative}"
            )

        report.append(
            ""
        )

        for relative in sorted(
            selected
        ):
            path = selected[
                relative
            ]

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
                    "ROUTING / CONFIG HITS",
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
                    "This is a read-only multi-cluster routing snapshot."
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
            "MULTI-CLUSTER ROUTER CURRENT CODE SNAPSHOT V1 PASSED"
        )
        print(
            "=" * 72
        )
        print()
        print(
            f"Captured files: {len(selected)}"
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
                        "Multi-Cluster Kubernetes Router "
                        "Current Code Snapshot v1 FAILED"
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
            "MULTI-CLUSTER ROUTER CURRENT CODE SNAPSHOT V1 FAILED"
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
