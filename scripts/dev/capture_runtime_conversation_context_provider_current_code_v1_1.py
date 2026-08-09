from __future__ import annotations

import hashlib
import traceback
from datetime import datetime
from pathlib import Path


OUTPUT_NAME = (
    "runtime_conversation_context_provider_current_code_snapshot_v1_1.txt"
)

ERROR_NAME = (
    "runtime_conversation_context_provider_current_code_snapshot_v1_1_error.txt"
)

TARGETS = (
    "services/agent_runtime/app/conversation/__init__.py",
    "services/agent_runtime/app/conversation/models.py",
    "services/agent_runtime/app/conversation/classifier.py",
    "services/agent_runtime/app/conversation/store.py",
    "services/agent_runtime/app/conversation/provider.py",
    "services/agent_runtime/app/conversation/orchestrator.py",
    "services/agent_runtime/app/runtime/runtime.py",
    "services/agent_runtime/app/pipeline/planner_pipeline.py",
    "services/agent_runtime/app/memory/base.py",
    "services/agent_runtime/app/memory/store.py",
    "services/agent_runtime/app/memory/models.py",
    "services/agent_runtime/app/incident/state.py",
    "services/agent_runtime/app/incident/store.py",
    "services/agent_runtime/app/incident/service.py",
    "services/agent_runtime/app/approval/models.py",
    "services/agent_runtime/app/approval/service.py",
    "services/agent_runtime/app/approval/manager.py",
    "services/agent_runtime/app/approval/store.py",
    "services/agent_runtime/app/action/execution_models.py",
    "services/agent_runtime/app/action/execution_service.py",
    "services/agent_runtime/app/action/execution_store.py",
    "services/agent_runtime/app/verification/models.py",
    "services/agent_runtime/app/verification/service.py",
    "services/agent_runtime/app/verification/store.py",
    "services/agent_runtime/app/investigation/models.py",
    "services/agent_runtime/app/investigation/coordinator.py",
    "services/agent_runtime/app/incident_evidence/recorder.py",
    "services/agent_runtime/app/api/runtime.py",
    "services/agent_runtime/tests/test_conversation_orchestrator.py",
    "services/agent_runtime/tests/test_runtime_action_execution_wiring.py",
    "services/agent_runtime/tests/test_verification_service.py",
    "services/agent_runtime/tests/test_api_read_rbac.py",
)

DISCOVERY_ROOTS = (
    "services/agent_runtime/app/memory",
    "services/agent_runtime/app/investigation",
    "services/agent_runtime/app/incident",
    "services/agent_runtime/app/approval",
    "services/agent_runtime/app/action",
    "services/agent_runtime/app/verification",
    "services/agent_runtime/app/workflow",
)

DISCOVERY_TOKENS = (
    "investigation_shadow",
    "InvestigationState",
    "InvestigationConclusion",
    "conclusion",
    "root_cause",
    "confidence",
    "hypotheses",
    "evidence",
    "memory",
    "MemoryStore",
    "_record_rca_memory_hit",
    "list_by_incident",
    "incident_id",
    "ApprovalStatus",
    "VerificationStatus",
    "ActionExecutionStatus",
    "ConversationIncidentContext",
    "ConversationOrchestrator",
    "SQLite",
    "sqlite",
    "CREATE TABLE",
)


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate
    raise RuntimeError("Repository root not found.")


def normalized_relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def line_hits(text: str) -> list[str]:
    hits = []
    for number, row in enumerate(text.splitlines(), start=1):
        matched = [
            token
            for token in DISCOVERY_TOKENS
            if token in row
        ]
        if matched:
            hits.append(
                f"L{number}: tokens={matched} text={row}"
            )
    return hits


def discover_related_files(root: Path) -> list[Path]:
    discovered: dict[str, Path] = {}

    for relative_root in DISCOVERY_ROOTS:
        scan_root = root / relative_root
        if not scan_root.exists():
            continue

        for path in scan_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue

            try:
                text = path.read_text(
                    encoding="utf-8-sig",
                    errors="strict",
                )
            except (OSError, UnicodeError):
                continue

            if any(token in text for token in DISCOVERY_TOKENS):
                discovered[
                    normalized_relative(path, root)
                ] = path

    return [
        discovered[key]
        for key in sorted(discovered)
    ]


def add_file(
    report: list[str],
    *,
    root: Path,
    relative: str,
    required: bool = False,
) -> None:
    path = root / relative

    report.extend(
        [
            "",
            "=" * 120,
            relative,
            "=" * 120,
            "",
        ]
    )

    if not path.exists():
        if required:
            raise RuntimeError(
                f"Required file missing: {relative}"
            )
        report.append("<NOT PRESENT>")
        return

    raw = path.read_bytes()
    text = raw.decode(
        "utf-8-sig",
        errors="strict",
    )

    report.extend(
        [
            "sha256_raw=" + sha256_bytes(raw),
            "lines=" + str(len(text.splitlines())),
            "",
            "CONTEXT / PERSISTENCE HITS",
            "-" * 120,
        ]
    )

    hits = line_hits(text)
    report.extend(hits if hits else ["<NONE>"])

    report.extend(
        [
            "",
            "FULL CURRENT FILE",
            "-" * 120,
            text.rstrip(),
        ]
    )


def main() -> int:
    root = find_repo_root(Path.cwd().resolve())

    output = root / OUTPUT_NAME
    error = root / ERROR_NAME

    for path in (output, error):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    report = [
        "Runtime Conversation Context Provider Current Code Snapshot v1.1",
        "GeneratedAt: "
        + datetime.now().astimezone().isoformat(),
        "",
        "Product direction:",
        "- ChatOps-first AI SRE Agent",
        "- reuse existing authoritative state",
        "- no second source of truth",
        "",
        "Purpose:",
        "- review installed Conversation Orchestrator v1",
        "- review Incident / Approval / Action Execution / Verification persistence",
        "- review Investigation Shadow RCA/hypothesis/evidence lifetime",
        "- review existing RCA MemoryStore behavior and PlannerPipeline memory writes",
        "- determine whether MemoryStore is process-local or durable",
        "- decide whether ChatOps requires durable Investigation result persistence",
        "",
        "No source file is modified.",
    ]

    try:
        selected: dict[str, bool] = {}
        required_targets = {
            "services/agent_runtime/app/conversation/models.py",
            "services/agent_runtime/app/conversation/provider.py",
            "services/agent_runtime/app/conversation/orchestrator.py",
            "services/agent_runtime/app/runtime/runtime.py",
            "services/agent_runtime/app/investigation/models.py",
            "services/agent_runtime/app/investigation/coordinator.py",
        }

        for relative in TARGETS:
            selected[relative] = relative in required_targets

        for path in discover_related_files(root):
            selected.setdefault(
                normalized_relative(path, root),
                False,
            )

        report.extend(["", "Selected files:"])
        for relative in sorted(selected):
            report.append(
                "- "
                + relative
                + (
                    " [required]"
                    if selected[relative]
                    else ""
                )
            )

        for relative in sorted(selected):
            add_file(
                report,
                root=root,
                relative=relative,
                required=selected[relative],
            )

        report.extend(
            [
                "",
                "=" * 120,
                "RESULT",
                "=" * 120,
                "",
                "PASSED",
                "",
                "Read-only snapshot completed.",
                "No source file was modified.",
                "",
                "Upload only this one file:",
                OUTPUT_NAME,
            ]
        )

        output.write_text(
            "\n".join(report) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print(
            "RUNTIME CONVERSATION CONTEXT PROVIDER SNAPSHOT V1.1 PASSED"
        )
        print("=" * 72)
        print()
        print(f"Captured files: {len(selected)}")
        print("No source file was modified.")
        print()
        print("Upload only:")
        print(output)
        return 0

    except Exception as exc:
        error.write_text(
            "\n".join(
                [
                    "Runtime Conversation Context Provider Current Code Snapshot v1.1 FAILED",
                    "GeneratedAt: "
                    + datetime.now().astimezone().isoformat(),
                    "",
                    f"{type(exc).__name__}: {exc}",
                    "",
                    traceback.format_exc(),
                    "",
                    "PARTIAL REPORT",
                    "=" * 120,
                    *report,
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print(
            "RUNTIME CONVERSATION CONTEXT PROVIDER SNAPSHOT V1.1 FAILED"
        )
        print("=" * 72)
        print()
        print("Upload only:")
        print(error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
