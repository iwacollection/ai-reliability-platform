from __future__ import annotations

import hashlib
import traceback

from datetime import datetime
from pathlib import Path


OUTPUT_NAME = (
    "chatops_identity_authenticated_write_bridge_current_code_snapshot_v1.txt"
)

ERROR_NAME = (
    "chatops_identity_authenticated_write_bridge_current_code_snapshot_v1_error.txt"
)

CORE_TARGETS = (
    # Current ChatOps path
    "services/agent_runtime/app/conversation/__init__.py",
    "services/agent_runtime/app/conversation/models.py",
    "services/agent_runtime/app/conversation/store.py",
    "services/agent_runtime/app/conversation/chatops.py",
    "services/agent_runtime/app/conversation/orchestrator.py",
    "services/agent_runtime/app/conversation/runtime_provider.py",

    # Runtime ownership
    "services/agent_runtime/app/runtime/runtime.py",

    # Existing authentication / authorization source of truth
    "services/agent_runtime/app/security/__init__.py",
    "services/agent_runtime/app/security/api.py",
    "services/agent_runtime/app/security/authentication.py",
    "services/agent_runtime/app/security/factory.py",
    "services/agent_runtime/app/security/models.py",
    "services/agent_runtime/app/security/policy.py",
    "services/agent_runtime/app/security/service.py",

    # Existing authenticated API write boundary
    "services/agent_runtime/app/api/runtime.py",

    # Approval / action services called only after security succeeds
    "services/agent_runtime/app/approval/models.py",
    "services/agent_runtime/app/approval/service.py",
    "services/agent_runtime/app/approval/manager.py",
    "services/agent_runtime/app/approval/store.py",
    "services/agent_runtime/app/action/execution_service.py",
    "services/agent_runtime/app/action/runtime.py",
    "services/agent_runtime/app/runtime/action_runtime.py",

    # Known current RBAC tests
    "services/agent_runtime/tests/api_security_support.py",
    "services/agent_runtime/tests/test_api_security_matrix.py",
    "services/agent_runtime/tests/test_api_approval_rbac.py",
    "services/agent_runtime/tests/test_api_resume_rbac.py",
    "services/agent_runtime/tests/test_api_execute_rbac.py",
    "services/agent_runtime/tests/test_api_read_rbac.py",
    "services/agent_runtime/tests/test_api_action_resume.py",
    "services/agent_runtime/tests/test_api_action_verification.py",

    # Current ChatOps tests
    "services/agent_runtime/tests/test_conversation_orchestrator.py",
    "services/agent_runtime/tests/test_durable_conversation_chatops_contract.py",
    "services/agent_runtime/tests/test_incident_analysis_conversation_context.py",
)

DISCOVERY_ROOTS = (
    "services/agent_runtime/app/security",
    "services/agent_runtime/app/api",
    "services/agent_runtime/app/conversation",
    "services/agent_runtime/app/approval",
    "services/agent_runtime/app/action",
    "services/agent_runtime/app/runtime",
    "services/agent_runtime/tests",
)

TOKENS = (
    "ApiSecurityAdapter",
    "AuthenticationService",
    "AuthenticationProviderRegistry",
    "BaseAuthenticationProvider",
    "OperatorIdentity",
    "OperatorRole",
    "RuntimePermission",
    "ProtectedOperation",
    "PREPARE_REMEDIATION",
    "DECIDE_APPROVAL",
    "RESUME_ACTION",
    "RECONCILE_ACTION",
    "READ_INCIDENT",
    "api_security.require",
    "authentication.authenticate",
    "security_policy.require",
    "X-Operator-ID",
    "Idempotency-Key",
    "approve_approval",
    "reject_approval",
    "resume_approved_action",
    "_apply_approval_decision",
    "prepare",
    "approval.approve",
    "approval.reject",
    "action_runtime.resume",
    "external_actor_id",
    "ChatOpsInboundMessage",
    "WRITE_ACTION_REQUIRED",
)


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
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
        path.relative_to(root)
    ).replace(
        "\\",
        "/",
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(
        data
    ).hexdigest()


def token_hits(text: str) -> list[str]:
    hits = []

    for number, line in enumerate(
        text.splitlines(),
        start=1,
    ):
        matched = [
            token
            for token in TOKENS
            if token in line
        ]

        if matched:
            hits.append(
                f"L{number}: tokens={matched} text={line}"
            )

    return hits


def discover_related_files(
    root: Path,
) -> list[Path]:
    found: dict[str, Path] = {}

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

            if any(
                token in text
                for token in TOKENS
            ):
                found[
                    normalized_relative(
                        path,
                        root,
                    )
                ] = path

    return [
        found[key]
        for key in sorted(
            found
        )
    ]


def add_file(
    report: list[str],
    *,
    root: Path,
    relative: str,
    required: bool,
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

        report.append(
            "<NOT PRESENT>"
        )
        return

    raw = path.read_bytes()

    text = raw.decode(
        "utf-8-sig",
        errors="strict",
    )

    report.extend(
        [
            "sha256_raw="
            + sha256_bytes(
                raw
            ),
            "lines="
            + str(
                len(
                    text.splitlines()
                )
            ),
            "",
            "CHATOPS / AUTH / RBAC HITS",
            "-" * 120,
        ]
    )

    hits = token_hits(
        text
    )

    report.extend(
        hits
        if hits
        else [
            "<NONE>"
        ]
    )

    report.extend(
        [
            "",
            "FULL CURRENT FILE",
            "-" * 120,
            text.rstrip(),
        ]
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

    report = [
        "ChatOps Identity + Authenticated Write Bridge Current Code Snapshot v1",
        (
            "GeneratedAt: "
            + datetime.now().astimezone().isoformat()
        ),
        "",
        "Product direction:",
        "- ChatOps-first, Evidence-driven, Human-in-the-loop AI SRE Agent",
        "- reuse existing Runtime authentication/RBAC/write boundaries",
        "- no second ChatOps permission system",
        "",
        "Review goals:",
        "1. Identify the exact current AuthenticationService contract.",
        "2. Identify how OperatorIdentity is created and audited.",
        "3. Identify ProtectedOperation -> permission/role mappings.",
        "4. Verify API security requires authorization before domain reads/writes.",
        "5. Capture approve/reject/resume request semantics and idempotency requirements.",
        "6. Determine how a verified channel actor may become an authenticated Runtime identity without trusting raw external_actor_id.",
        "7. Preserve separation of duties: approver != executor != reconciler unless Admin.",
        "8. Keep channel adapters free of ApprovalService/ActionRuntime authority.",
        "",
        "No source file is modified.",
        "No authentication, Approval, Action, LLM or network request is executed.",
    ]

    required_targets = {
        "services/agent_runtime/app/conversation/chatops.py",
        "services/agent_runtime/app/conversation/orchestrator.py",
        "services/agent_runtime/app/runtime/runtime.py",
        "services/agent_runtime/app/security/api.py",
        "services/agent_runtime/app/security/authentication.py",
        "services/agent_runtime/app/security/models.py",
        "services/agent_runtime/app/security/policy.py",
        "services/agent_runtime/app/security/service.py",
        "services/agent_runtime/app/api/runtime.py",
        "services/agent_runtime/tests/test_api_security_matrix.py",
    }

    try:
        selected: dict[str, bool] = {}

        for relative in CORE_TARGETS:
            selected[
                relative
            ] = (
                relative
                in required_targets
            )

        for path in discover_related_files(
            root
        ):
            selected.setdefault(
                normalized_relative(
                    path,
                    root,
                ),
                False,
            )

        report.extend(
            [
                "",
                "Selected files:",
            ]
        )

        for relative in sorted(
            selected
        ):
            report.append(
                "- "
                + relative
                + (
                    " [required]"
                    if selected[
                        relative
                    ]
                    else ""
                )
            )

        for relative in sorted(
            selected
        ):
            add_file(
                report,
                root=root,
                relative=relative,
                required=selected[
                    relative
                ],
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
                "Read-only ChatOps authentication/RBAC snapshot completed.",
                "No source file was modified.",
                "",
                "Upload only this one file:",
                OUTPUT_NAME,
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
            "CHATOPS IDENTITY / AUTHENTICATED WRITE BRIDGE SNAPSHOT V1 PASSED"
        )
        print(
            "=" * 72
        )
        print()
        print(
            f"Captured files: {len(selected)}"
        )
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
                    "ChatOps Identity + Authenticated Write Bridge Current Code Snapshot v1 FAILED",
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

        print(
            "=" * 72
        )
        print(
            "CHATOPS IDENTITY / AUTHENTICATED WRITE BRIDGE SNAPSHOT V1 FAILED"
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
