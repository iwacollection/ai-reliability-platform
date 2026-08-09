from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from lark_channel import Events, FeishuChannel, PolicyConfig, SecurityConfig


OUTPUT_NAME = "feishu_channel_inbound_diagnostic_v1_after.txt"
ERROR_NAME = "feishu_channel_inbound_diagnostic_v1_error.txt"

ACK = "I_ACKNOWLEDGE_REAL_FEISHU_NETWORK_CONNECTION"
APP_ID_ENV = "AI_RELIABILITY_FEISHU_APP_ID"
APP_SECRET_ENV = "AI_RELIABILITY_FEISHU_APP_SECRET"
GROUP_ALLOWLIST_ENV = "AI_RELIABILITY_FEISHU_GROUP_ALLOWLIST"
ACK_ENV = "AI_RELIABILITY_FEISHU_LIVE_ACK"

WAIT_SECONDS = 120.0


class DiagnosticError(RuntimeError):
    pass


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate
    raise DiagnosticError("Repository root not found")


def required(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DiagnosticError(label + " is missing or invalid")
    return value


def allowlist(value: Any) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise DiagnosticError("Feishu group allowlist is missing")

    items: list[str] = []
    seen: set[str] = set()

    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        if (
            not item.startswith("oc_")
            or any(ch.isspace() for ch in item)
            or len(item) > 256
        ):
            raise DiagnosticError("Feishu group allowlist contains invalid chat_id")
        if item not in seen:
            seen.add(item)
            items.append(item)

    if not items:
        raise DiagnosticError("Feishu group allowlist is empty")

    return tuple(items)


def opaque(value: str | None) -> str:
    text = value or ""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def safe_type(value: BaseException | Any) -> str:
    return (
        type(value).__module__
        + "."
        + type(value).__name__
    )[:256]


async def main_async() -> int:
    root = find_repo_root(Path.cwd().resolve())
    output = root / OUTPUT_NAME
    error = root / ERROR_NAME

    for path in (output, error):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    report = [
        "Feishu Channel Inbound Diagnostic v1",
        "GeneratedAt: " + datetime.now().astimezone().isoformat(),
        "",
        "Purpose:",
        "- diagnose event delivery before AI Reliability business logic",
        "- listen to message/reject/reconnecting/reconnected/error events",
        "- temporarily disable mention admission while retaining exact group allowlist",
        "- never send any Feishu message",
        "",
        "Safety:",
        "- group_policy=allowlist",
        "- require_mention=False only for this diagnostic",
        "- dm_policy=disabled",
        "- no AgentRuntime",
        "- no ChatOps gateway",
        "- no outbound send",
        "- no Approval/Action/Verification",
        "- no raw secret or message content persisted",
    ]

    channel = None
    connected = False
    done = asyncio.Event()
    events: list[dict[str, Any]] = []

    try:
        if os.environ.get(ACK_ENV) != ACK:
            raise DiagnosticError("Exact live acknowledgement is missing")

        app_id = required(os.environ.get(APP_ID_ENV), "Feishu App ID")
        app_secret = required(os.environ.get(APP_SECRET_ENV), "Feishu App Secret")
        groups = allowlist(os.environ.get(GROUP_ALLOWLIST_ENV))

        report += [
            "",
            "CONFIGURATION",
            "-" * 120,
            "app_id_present=True",
            "app_secret_present=True",
            "group_allowlist_count=" + str(len(groups)),
            *["group_opaque_id=" + opaque(item) for item in groups],
            "credential_values_persisted=False",
        ]

        policy = PolicyConfig(
            dm_policy="disabled",
            group_policy="allowlist",
            require_mention=False,
            respond_to_mention_all=False,
            group_allowlist=list(groups),
            sender_identity_fields=["open_id"],
        )

        security = SecurityConfig(
            mode="audit",
            allow_insecure_ws=False,
            allow_local_insecure_ws=False,
            max_ws_fragment_parts=128,
            max_ws_fragment_bytes=8 * 1024 * 1024,
            max_concurrent_ws_handlers=64,
            resource_overflow_policy="drop",
        )

        channel = FeishuChannel(
            app_id=app_id,
            app_secret=app_secret,
            transport="ws",
            policy=policy,
            security=security,
        )

        async def on_message(msg) -> None:
            record = {
                "event": "message",
                "chat": opaque(getattr(msg, "chat_id", None)),
                "sender": opaque(getattr(msg, "sender_id", None)),
                "message": opaque(getattr(msg, "message_id", None)),
                "chat_type": str(getattr(msg, "chat_type", None)),
                "sender_type": str(getattr(msg, "sender_type", None)),
                "sender_is_bot": bool(getattr(msg, "sender_is_bot", False)),
                "mentioned_bot": bool(getattr(msg, "mentioned_bot", False)),
                "mentioned_all": bool(getattr(msg, "mentioned_all", False)),
                "raw_content_type": str(getattr(msg, "raw_content_type", None)),
                "body_is_help": (
                    str(getattr(msg, "body_text", "")).strip() == "帮助"
                ),
            }
            events.append(record)
            done.set()

        async def on_reject(event) -> None:
            record = {
                "event": "reject",
                "reason": str(getattr(event, "reason", "<UNKNOWN>"))[:128],
                "chat": opaque(getattr(event, "chat_id", None)),
                "sender": opaque(getattr(event, "sender_id", None)),
            }
            events.append(record)
            done.set()

        async def on_error(exc) -> None:
            events.append(
                {
                    "event": "error",
                    "failure_code": safe_type(exc),
                }
            )

        async def on_reconnecting() -> None:
            events.append({"event": "reconnecting"})

        async def on_reconnected() -> None:
            events.append({"event": "reconnected"})

        channel.on(Events.MESSAGE, on_message)
        channel.on(Events.REJECT, on_reject)
        channel.on(Events.ERROR, on_error)
        channel.on(Events.RECONNECTING, on_reconnecting)
        channel.on(Events.RECONNECTED, on_reconnected)

        await channel.connect_until_ready(timeout=30.0)
        connected = True

        bot_identity_ready = False
        bot_open_id_hash = "<UNRESOLVED>"

        for _ in range(20):
            try:
                identity = channel.get_bot_identity()
                open_id = getattr(identity, "open_id", None)
                if isinstance(open_id, str) and open_id:
                    bot_identity_ready = True
                    bot_open_id_hash = opaque(open_id)
                    break
            except Exception:
                pass
            await asyncio.sleep(1.0)

        report += [
            "",
            "CONNECTED",
            "-" * 120,
            "connect_ready=True",
            "bot_identity_ready=" + str(bot_identity_ready),
            "bot_open_id_opaque=" + bot_open_id_hash,
            "registered_events=message,reject,error,reconnecting,reconnected",
            "require_mention=False",
            "outbound_send_enabled=False",
            "",
            "USER ACTION:",
            "在 allowlist 测试群里，用 @ 选择当前机器人，然后发送：帮助",
        ]

        print("=" * 72)
        print("FEISHU CHANNEL INBOUND DIAGNOSTIC V1")
        print("=" * 72)
        print()
        print("Channel ready.")
        print("bot_identity_ready=" + str(bot_identity_ready))
        print()
        print("现在请在 allowlist 测试群里：")
        print("  @你的机器人 帮助")
        print()
        print("本诊断不会回复任何消息，只观察 SDK 是否收到/拒绝事件。")
        print()

        try:
            await asyncio.wait_for(done.wait(), timeout=WAIT_SECONDS)
        except asyncio.TimeoutError:
            events.append({"event": "timeout_no_message_or_reject"})

        await channel.disconnect()
        connected = False

        report += [
            "",
            "OBSERVED EVENTS",
            "-" * 120,
        ]

        for index, item in enumerate(events, start=1):
            report.append("event_" + str(index) + "=" + repr(item))

        primary = next(
            (
                item
                for item in events
                if item.get("event") in {"message", "reject"}
            ),
            None,
        )

        report += [
            "",
            "DIAGNOSIS",
            "-" * 120,
        ]

        if primary is None:
            report += [
                "classification=NO_INBOUND_EVENT",
                "meaning=Channel connected but no message/reject event reached SDK within diagnostic window.",
            ]
        elif primary["event"] == "reject":
            report += [
                "classification=SDK_POLICY_REJECT",
                "reject_reason=" + str(primary.get("reason")),
            ]
        else:
            report += [
                "classification=MESSAGE_REACHED_SDK",
                "mentioned_bot=" + str(primary.get("mentioned_bot")),
                "body_is_help=" + str(primary.get("body_is_help")),
                "chat_matches_allowlist="
                + str(primary.get("chat") in {opaque(item) for item in groups}),
            ]

        report += [
            "",
            "RESULT",
            "-" * 120,
            "PASSED",
            "",
            "Diagnostic completed without sending any Feishu message.",
            "Upload only:",
            OUTPUT_NAME,
        ]

        output.write_text(
            "\n".join(report) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print()
        print("Diagnostic complete.")
        print("Upload only:")
        print(output)
        return 0

    except BaseException as exc:
        if channel is not None and connected:
            try:
                await channel.disconnect()
            except BaseException:
                pass

        error.write_text(
            "\n".join(
                [
                    "Feishu Channel Inbound Diagnostic v1 FAILED",
                    "GeneratedAt: " + datetime.now().astimezone().isoformat(),
                    "",
                    "failure_code=" + safe_type(exc),
                    "raw_exception_text_persisted=False",
                    "credential_values_persisted=False",
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
        print("FEISHU CHANNEL INBOUND DIAGNOSTIC V1 FAILED")
        print("=" * 72)
        print("failure_code=" + safe_type(exc))
        print("Upload only:")
        print(error)
        return 1


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
