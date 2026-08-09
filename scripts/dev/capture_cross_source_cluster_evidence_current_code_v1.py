from __future__ import annotations

import hashlib
import traceback
from datetime import datetime
from pathlib import Path


OUTPUT_NAME = (
    "cross_source_cluster_evidence_current_code_snapshot_v1.txt"
)

ERROR_NAME = (
    "cross_source_cluster_evidence_current_code_snapshot_v1_error.txt"
)

CORE_CANDIDATES = (
    "services/agent_runtime/app/investigation/models.py",
    "services/agent_runtime/app/investigation/probes.py",
    "services/agent_runtime/app/investigation/coordinator.py",
    "services/agent_runtime/app/investigation/reasoner.py",
    "services/agent_runtime/app/investigation/evidence.py",
    "services/agent_runtime/app/investigation/evidence_store.py",
    "services/agent_runtime/app/investigation/recorder.py",
    "services/agent_runtime/app/verification/collector.py",
    "services/agent_runtime/app/tools/kubernetes/tool.py",
    "services/agent_runtime/app/tools/kubernetes/change_tool.py",
    "services/agent_runtime/app/tools/kubernetes/router.py",
    "services/agent_runtime/app/tools/prometheus/tool.py",
    "services/agent_runtime/app/tools/prometheus/router.py",
    "services/agent_runtime/tests/test_production_scope_integrity.py",
    "services/agent_runtime/tests/test_investigation_probes.py",
    "services/agent_runtime/tests/test_investigation_evidence_consistency.py",
    "services/agent_runtime/tests/test_verification_collector.py",
    "services/agent_runtime/tests/test_multi_cluster_prometheus_router.py",
    "services/agent_runtime/tests/test_multi_cluster_kubernetes_router.py",
)

DISCOVERY_TOKENS = (
    "EvidenceItem",
    "InvestigationEvidence",
    "trusted=",
    "source=",
    "cluster",
    "tool_result",
    "result.get(",
    "production_signal",
    "_normalize_kubernetes",
    "_normalize_prometheus",
    "VerificationEvidence",
    "evidence_store",
    "supporting_evidence_ids",
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
        / "app"
        / "investigation",
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "verification",
        root
        / "services"
        / "agent_runtime"
        / "tests",
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
                "No evidence-consistency related Python files were found."
            )

        report = [
            "Cross-Source Cluster Evidence Current Code Snapshot v1",
            (
                "GeneratedAt: "
                + datetime.now().astimezone().isoformat()
            ),
            "",
            "Purpose:",
            "- capture the complete current Evidence normalization/collection path before adding cross-source cluster consistency enforcement",
            "- preserve exact SHA256 values for stale-source protection",
            "- discover current tests instead of guessing historical filenames",
            "- make no source-code changes",
            "",
            "Current multi-cluster baseline:",
            "- Kubernetes routing uses exact scope.cluster",
            "- Prometheus routing uses exact scope.cluster",
            "- both connection-config factories are disabled by default",
            "- unknown/ambiguous cluster routing fails closed",
            "",
            "Consistency Contract v1 questions:",
            "- where tool result cluster identity is currently retained or discarded",
            "- whether EvidenceItem has a cluster field today",
            "- whether normalization is the earliest common trust boundary",
            "- how Kubernetes Pod State / Logs / Change and Prometheus evidence differ",
            "- whether VerificationEvidenceCollector has an independent trust path",
            "- how to reject scope.cluster != tool_result.cluster before Reasoner sees evidence",
            "- how to handle legacy single-cluster results with no cluster field without breaking compatibility",
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
                    "EVIDENCE / CLUSTER HITS",
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
                    "This is a read-only Evidence consistency snapshot."
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
            "CROSS-SOURCE CLUSTER EVIDENCE CURRENT CODE SNAPSHOT V1 PASSED"
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
                        "Cross-Source Cluster Evidence "
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
            "CROSS-SOURCE CLUSTER EVIDENCE CURRENT CODE SNAPSHOT V1 FAILED"
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
