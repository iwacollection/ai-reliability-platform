from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import os
import time
import traceback

from datetime import datetime
from pathlib import Path
from typing import Any

from lark_channel import (
    FeishuChannel,
    PolicyConfig,
    SecurityConfig,
)


OUTPUT_NAME = "feishu_live_read_only_connectivity_preflight_v1_after.txt"
ERROR_NAME = "feishu_live_read_only_connectivity_preflight_v1_error.txt"

ACKNOWLEDGEMENT = (
    "I_ACKNOWLEDGE_REAL_FEISHU_NETWORK_CONNECTION"
)

APP_ID_ENV = "AI_RELIABILITY_FEISHU_APP_ID"
APP_SECRET_ENV = "AI_RELIABILITY_FEISHU_APP_SECRET"
GROUP_ALLOWLIST_ENV = "AI_RELIABILITY_FEISHU_GROUP_ALLOWLIST"
ACK_ENV = "AI_RELIABILITY_FEISHU_LIVE_ACK"

CONNECT_TIMEOUT_SECONDS = 30.0
OVERALL_CONNECT_TIMEOUT_SECONDS = 45.0
STABILITY_SECONDS = 2.0

EXPECTED_HASHES = {
    "pyproject.toml":
        "cc2f73d19fd71c810ebf23429e5ecb4f9bd8cf6fe65ece91ba3569ce2b7e82ce",
    "uv.lock":
        "e2bef32ca96b736bc104ea3f3999316223f1793c4b2663c30175ae5f5fce5722",
    "services/agent_runtime/app/conversation/feishu.py":
        "d3869bf3fb7e6e0a7ce43934979887106a380caf90cca615414d33a7560eeea1",
    "services/agent_runtime/app/conversation/feishu_channel_transport.py":
        "17e7cb678de5b478a0ba61f650bdb6c9c004272a23096e026b7f3cba1f34bcd8",
    "services/agent_runtime/app/conversation/feishu_live_runtime.py":
        "a5c6e54437fa7d92b56ec0f2a6e45437260ad618fd1f17b6e4a2a6661dcc2f50",
    "services/agent_runtime/tests/test_feishu_live_runtime.py":
        "117f48c08e63c85b37351d5f243873abd57d45eea28b43a2a69a9c511b1ab043",
    "scripts/dev/run_feishu_live_channel_v1.py":
        "9791aa3802b278a07145ecb80a06de5e660781c1268040bc199dcc78dcab634c",
}


class PreflightError(RuntimeError):
    pass


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

    raise PreflightError(
        "Repository root not found"
    )


def raw_sha256(
    path: Path,
) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def required_secret(
    value: Any,
    *,
    label: str,
) -> str:
    if (
        not isinstance(
            value,
            str,
        )
        or not value
        or value != value.strip()
        or len(value) > 1024
        or "\x00" in value
    ):
        raise PreflightError(
            label
            + " is unavailable or invalid"
        )

    return value


def resolve_allowlist(
    value: Any,
) -> tuple[str, ...]:
    if not isinstance(
        value,
        str,
    ):
        raise PreflightError(
            "Feishu group allowlist is unavailable"
        )

    items: list[str] = []
    seen: set[str] = set()

    for raw in value.split(","):
        item = raw.strip()

        if not item:
            continue

        if (
            len(item) > 256
            or "\x00" in item
            or any(
                character.isspace()
                for character in item
            )
            or not item.startswith(
                "oc_"
            )
        ):
            raise PreflightError(
                "Feishu group allowlist contains an invalid chat ID"
            )

        if item in seen:
            continue

        seen.add(item)
        items.append(item)

    if not items:
        raise PreflightError(
            "Feishu group allowlist cannot be empty"
        )

    return tuple(items)


def opaque_identifier(
    value: str,
) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            value.encode(
                "utf-8"
            )
        ).hexdigest()[:16]
    )


def safe_failure_code(
    exc: BaseException,
) -> str:
    return (
        type(exc).__module__
        + "."
        + type(exc).__name__
    )[:256]


async def run_preflight(
    *,
    root: Path,
    report: list[str],
) -> None:
    acknowledgement = os.environ.get(
        ACK_ENV
    )

    if acknowledgement != ACKNOWLEDGEMENT:
        raise PreflightError(
            "Exact live-network acknowledgement is missing"
        )

    # Secret values are resolved only after acknowledgement.
    app_id = required_secret(
        os.environ.get(
            APP_ID_ENV
        ),
        label="Feishu App ID",
    )

    app_secret = required_secret(
        os.environ.get(
            APP_SECRET_ENV
        ),
        label="Feishu App Secret",
    )

    group_allowlist = resolve_allowlist(
        os.environ.get(
            GROUP_ALLOWLIST_ENV
        )
    )

    report.extend(
        [
            "",
            "=" * 120,
            "LIVE CONFIGURATION PRESENCE",
            "=" * 120,
            "",
            "acknowledgement_valid=True",
            "app_id_present=True",
            "app_secret_present=True",
            (
                "group_allowlist_count="
                + str(
                    len(group_allowlist)
                )
            ),
            "group_allowlist_opaque_ids=",
            *[
                "- "
                + opaque_identifier(
                    item
                )
                for item in group_allowlist
            ],
            "",
            "credential_values_persisted=False",
            "raw_group_chat_ids_persisted=False",
        ]
    )

    policy = PolicyConfig(
        dm_policy="disabled",
        group_policy="allowlist",
        require_mention=True,
        respond_to_mention_all=False,
        group_allowlist=list(
            group_allowlist
        ),
        sender_identity_fields=[
            "open_id",
        ],
    )

    security = SecurityConfig(
        mode="audit",
        allow_insecure_ws=False,
        allow_local_insecure_ws=False,
        max_ws_fragment_parts=128,
        max_ws_fragment_bytes=(
            8 * 1024 * 1024
        ),
        max_concurrent_ws_handlers=64,
        resource_overflow_policy="drop",
    )

    report.extend(
        [
            "",
            "=" * 120,
            "LIVE SECURITY POLICY",
            "=" * 120,
            "",
            "transport=ws",
            "security_mode=audit",
            "allow_insecure_ws=False",
            "allow_local_insecure_ws=False",
            "dm_policy=disabled",
            "group_policy=allowlist",
            "require_mention=True",
            "respond_to_mention_all=False",
            "business_handlers_registered=False",
            "chatops_gateway_created=False",
            "agent_runtime_created=False",
            "authenticated_write_bridge_created=False",
        ]
    )

    # No on(...) handlers are registered in this preflight.
    # It validates credentials + official WebSocket readiness only.
    channel = FeishuChannel(
        app_id=app_id,
        app_secret=app_secret,
        transport="ws",
        policy=policy,
        security=security,
    )

    connected = False
    started = time.perf_counter()

    report.extend(
        [
            "",
            "=" * 120,
            "REAL FEISHU CONNECTIVITY",
            "=" * 120,
            "",
            (
                "connect_timeout_seconds="
                + str(
                    CONNECT_TIMEOUT_SECONDS
                )
            ),
            (
                "overall_timeout_seconds="
                + str(
                    OVERALL_CONNECT_TIMEOUT_SECONDS
                )
            ),
        ]
    )

    try:
        await asyncio.wait_for(
            channel.connect_until_ready(
                timeout=(
                    CONNECT_TIMEOUT_SECONDS
                )
            ),
            timeout=(
                OVERALL_CONNECT_TIMEOUT_SECONDS
            ),
        )

        connected = True

        ready_elapsed_ms = int(
            (
                time.perf_counter()
                - started
            )
            * 1000
        )

        report.extend(
            [
                "connect_ready=True",
                (
                    "connect_ready_elapsed_ms="
                    + str(
                        ready_elapsed_ms
                    )
                ),
                (
                    "stability_window_seconds="
                    + str(
                        STABILITY_SECONDS
                    )
                ),
            ]
        )

        await asyncio.sleep(
            STABILITY_SECONDS
        )

        report.append(
            "stability_window_completed=True"
        )

    finally:
        if connected:
            disconnect_started = (
                time.perf_counter()
            )

            await channel.disconnect()

            disconnect_elapsed_ms = int(
                (
                    time.perf_counter()
                    - disconnect_started
                )
                * 1000
            )

            report.extend(
                [
                    "disconnect_completed=True",
                    (
                        "disconnect_elapsed_ms="
                        + str(
                            disconnect_elapsed_ms
                        )
                    ),
                ]
            )


async def async_main() -> int:
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
        "Feishu Live Read-Only Connectivity Preflight v1",
        (
            "GeneratedAt: "
            + datetime.now()
            .astimezone()
            .isoformat()
        ),
        "",
        "Purpose:",
        "- perform the first bounded real Feishu network readiness proof",
        "- validate App ID/App Secret through official FeishuChannel WebSocket readiness",
        "- validate explicit audit security and group allowlist configuration",
        "- disconnect immediately after a short stability window",
        "",
        "Critical isolation:",
        "- no AgentRuntime construction",
        "- no Conversation Gateway",
        "- no message/card business handler registration",
        "- no outbound send()",
        "- no Approval/Action/Verification authority",
        "- no LLM/Kubernetes/Prometheus call",
        "- no source modification",
        "",
        "Secrets:",
        "- App ID/App Secret are read only after exact acknowledgement",
        "- secret values are never written to this report",
        "- group chat IDs are persisted only as short SHA256 fingerprints",
    ]

    stage = "baseline_preflight"

    try:
        report.extend(
            [
                "",
                "=" * 120,
                "CURRENT BASELINE SHA256",
                "=" * 120,
                "",
            ]
        )

        for relative, expected in (
            EXPECTED_HASHES.items()
        ):
            path = root / relative

            if not path.exists():
                raise PreflightError(
                    "Required baseline file is missing: "
                    + relative
                )

            actual = raw_sha256(
                path
            )

            report.append(
                relative
                + "="
                + actual
            )

            if actual != expected:
                raise PreflightError(
                    "Reviewed baseline changed: "
                    + relative
                )

        stage = "dependency_contract"

        sdk_version = (
            importlib.metadata.version(
                "lark-channel-sdk"
            )
        )
        websockets_version = (
            importlib.metadata.version(
                "websockets"
            )
        )

        report.extend(
            [
                "",
                "=" * 120,
                "DEPENDENCY CONTRACT",
                "=" * 120,
                "",
                (
                    "lark_channel_sdk_version="
                    + sdk_version
                ),
                (
                    "websockets_version="
                    + websockets_version
                ),
            ]
        )

        if sdk_version != "1.2.0":
            raise PreflightError(
                "Unexpected lark-channel-sdk version"
            )

        if websockets_version != "15.0.1":
            raise PreflightError(
                "Unexpected websockets version"
            )

        stage = "real_connectivity"

        await run_preflight(
            root=root,
            report=report,
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
                "Real Feishu WebSocket readiness was proven.",
                "The connection was disconnected cleanly.",
                "No ChatOps business handler was registered.",
                "No message was sent.",
                "No AgentRuntime was created.",
                "",
                "Next stage after review:",
                "- Feishu Live Read-Only Message Path Proof v1",
                "- only after that: authenticated live write enablement",
                "",
                "Upload only:",
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

        print("=" * 72)
        print(
            "FEISHU LIVE READ-ONLY CONNECTIVITY PREFLIGHT V1 PASSED"
        )
        print("=" * 72)
        print()
        print(
            "Real Feishu WebSocket readiness: PASSED"
        )
        print(
            "Business handlers registered: False"
        )
        print(
            "Messages sent: 0"
        )
        print()
        print(
            "Upload only:"
        )
        print(output)

        return 0

    except Exception as exc:
        # Never serialize raw exception text from a live credential/network path.
        report.extend(
            [
                "",
                "=" * 120,
                "FAILURE",
                "=" * 120,
                "",
                "stage="
                + stage,
                "failure_code="
                + safe_failure_code(
                    exc
                ),
                "raw_exception_text_persisted=False",
                "credential_values_persisted=False",
            ]
        )

        error.write_text(
            "\n".join(
                [
                    "Feishu Live Read-Only Connectivity Preflight v1 FAILED",
                    (
                        "GeneratedAt: "
                        + datetime.now()
                        .astimezone()
                        .isoformat()
                    ),
                    "",
                    "stage="
                    + stage,
                    "failure_code="
                    + safe_failure_code(
                        exc
                    ),
                    "",
                    "Raw exception text is intentionally not persisted.",
                    "No credential value is persisted.",
                    "",
                    "PARTIAL SANITIZED REPORT",
                    "=" * 120,
                    *report,
                    "",
                    "Upload only:",
                    ERROR_NAME,
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print(
            "FEISHU LIVE READ-ONLY CONNECTIVITY PREFLIGHT V1 FAILED"
        )
        print("=" * 72)
        print()
        print(
            "stage="
            + stage
        )
        print(
            "failure_code="
            + safe_failure_code(
                exc
            )
        )
        print()
        print(
            "Credential values were not persisted."
        )
        print()
        print(
            "Upload only:"
        )
        print(error)

        return 1


def main() -> int:
    return asyncio.run(
        async_main()
    )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
