from __future__ import annotations

import hashlib
import traceback
from datetime import datetime
from pathlib import Path


OUTPUT_NAME = (
    "multi_cluster_prometheus_current_code_snapshot_v1.txt"
)

ERROR_NAME = (
    "multi_cluster_prometheus_current_code_snapshot_v1_error.txt"
)

CORE_CANDIDATES = (
    "packages/common/src/common/config/settings.py",
    "services/agent_runtime/app/tools/factory.py",
    "services/agent_runtime/app/tools/manager.py",
    "services/agent_runtime/app/tools/registry.py",
    "services/agent_runtime/app/tools/prometheus.py",
    "services/agent_runtime/app/tools/prometheus/tool.py",
    "services/agent_runtime/app/tools/prometheus/__init__.py",
    "services/agent_runtime/app/investigation/probes.py",
    "services/agent_runtime/app/runtime/runtime.py",
    "services/agent_runtime/app/tools/kubernetes/router.py",
    "services/agent_runtime/app/tools/kubernetes/connection_factory.py",
)

DISCOVERY_TOKENS = (
    "PrometheusTool(",
    "prometheus",
    "PROMETHEUS_",
    "base_url",
    "api_url",
    "timeout_seconds",
    "cluster=scope.cluster",
    "PROMETHEUS_MEMORY_WORKING_SET",
    "PROMETHEUS_MEMORY_LIMIT",
    "PROMETHEUS_RESTART_COUNT",
    "create_tool_manager(",
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


def sha256_bytes(
    data: bytes,
) -> str:
    return hashlib.sha256(
        data
    ).hexdigest()


def discover_related_files(
    root: Path,
) -> list[Path]:
    roots = (
        root
        / "services"
        / "agent_runtime"
        / "app",
        root
        / "services"
        / "agent_runtime"
        / "tests",
        root
        / "packages"
        / "common"
        / "src"
        / "common",
    )

    discovered = []

    for scan_root in roots:
        if not scan_root.exists():
            continue

        for path in sorted(
            scan_root.rglob(
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
                "No Prometheus routing/config related Python files were found."
            )

        report = [
            "Multi-Cluster Prometheus Read Router Current Code Snapshot v1",
            (
                "GeneratedAt: "
                + datetime.now().astimezone().isoformat()
            ),
            "",
            "Purpose:",
            "- capture the complete current Prometheus read path before adding cluster routing",
            "- preserve exact SHA256 values for stale-source protection",
            "- discover real current Prometheus tests instead of guessing historical filenames",
            "- make no source-code changes",
            "",
            "Current Kubernetes multi-cluster baseline:",
            "- scope.cluster is preserved",
            "- Kubernetes read routing is exact/fail-closed",
            "- Kubernetes connection config can build a registry from credential references",
            "",
            "Prometheus Router v1 questions:",
            "- whether PrometheusTool is singleton and where it is constructed",
            "- whether PrometheusTool currently receives cluster as an argument or only query text",
            "- whether cluster identity is encoded only in PromQL labels today",
            "- whether one endpoint serves all clusters or endpoints are per-cluster",
            "- how timeout/TLS/auth are currently configured",
            "- where to add disabled-default prometheus_read connection descriptors",
            "- how to guarantee Kubernetes and Prometheus evidence use the same Incident cluster",
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
                    "PROMETHEUS / ROUTING HITS",
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
                    "This is a read-only Prometheus routing snapshot."
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
            "MULTI-CLUSTER PROMETHEUS CURRENT CODE SNAPSHOT V1 PASSED"
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
                        "Multi-Cluster Prometheus Read Router "
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
            "MULTI-CLUSTER PROMETHEUS CURRENT CODE SNAPSHOT V1 FAILED"
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
