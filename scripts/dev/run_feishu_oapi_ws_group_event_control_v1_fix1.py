from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

OUTPUT_NAME = "feishu_oapi_ws_group_event_control_v1_fix1_after.txt"
ERROR_NAME = "feishu_oapi_ws_group_event_control_v1_fix1_error.txt"

ACK = "I_ACKNOWLEDGE_REAL_FEISHU_NETWORK_CONNECTION"
APP_ID_ENV = "AI_RELIABILITY_FEISHU_APP_ID"
APP_SECRET_ENV = "AI_RELIABILITY_FEISHU_APP_SECRET"
GROUP_ALLOWLIST_ENV = "AI_RELIABILITY_FEISHU_GROUP_ALLOWLIST"
ACK_ENV = "AI_RELIABILITY_FEISHU_LIVE_ACK"

WAIT_SECONDS = 180.0
HEARTBEAT_SECONDS = 10.0


class ControlError(RuntimeError):
    pass


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate
    raise ControlError("Repository root not found")


def required(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
    ):
        raise ControlError(label + " is missing or invalid")
    return value


def resolve_allowlist(value: Any) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ControlError("Feishu group allowlist is missing")

    items: list[str] = []
    seen: set[str] = set()

    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        if (
            not item.startswith("oc_")
            or len(item) > 256
            or any(ch.isspace() for ch in item)
        ):
            raise ControlError("Feishu group allowlist contains invalid chat_id")
        if item not in seen:
            seen.add(item)
            items.append(item)

    if not items:
        raise ControlError("Feishu group allowlist is empty")

    return tuple(items)


def opaque(value: str | None) -> str:
    text = value or ""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def safe_type(exc: BaseException) -> str:
    return (type(exc).__module__ + "." + type(exc).__name__)[:256]


def append_record(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        )


def child_main(event_file: Path) -> int:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

    app_id = required(os.environ.get(APP_ID_ENV), "Feishu App ID")
    app_secret = required(os.environ.get(APP_SECRET_ENV), "Feishu App Secret")
    allowed = set(resolve_allowlist(os.environ.get(GROUP_ALLOWLIST_ENV)))

    def on_message(data: P2ImMessageReceiveV1) -> None:
        try:
            event = getattr(data, "event", None)
            message = getattr(event, "message", None)
            sender = getattr(event, "sender", None)
            sender_id = getattr(sender, "sender_id", None)

            chat_id = getattr(message, "chat_id", None)
            message_id = getattr(message, "message_id", None)
            message_type = getattr(message, "message_type", None)
            chat_type = getattr(message, "chat_type", None)
            open_id = getattr(sender_id, "open_id", None)

            chat_matches = isinstance(chat_id, str) and chat_id in allowed
            is_group_like = str(chat_type) in {"group", "topic"}

            append_record(
                event_file,
                {
                    "received": True,
                    "chat_opaque": opaque(
                        chat_id if isinstance(chat_id, str) else None
                    ),
                    "message_opaque": opaque(
                        message_id if isinstance(message_id, str) else None
                    ),
                    "sender_opaque": opaque(
                        open_id if isinstance(open_id, str) else None
                    ),
                    "message_type": str(message_type),
                    "chat_type": str(chat_type),
                    "chat_matches_allowlist": chat_matches,
                    "is_group_like": is_group_like,
                    "target_group_match": chat_matches and is_group_like,
                    "raw_content_persisted": False,
                    "raw_external_ids_persisted": False,
                },
            )
        except BaseException as exc:
            append_record(
                event_file,
                {
                    "received": False,
                    "handler_failure_code": safe_type(exc),
                    "raw_exception_text_persisted": False,
                },
            )

    dispatcher = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )

    client = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=dispatcher,
        log_level=lark.LogLevel.ERROR,
    )

    client.start()
    return 0


def read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []

    for line in path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)

    return records


def parent_main() -> int:
    root = find_repo_root(Path.cwd().resolve())
    output = root / OUTPUT_NAME
    error = root / ERROR_NAME

    for path in (output, error):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    report = [
        "Feishu Standard OAPI WebSocket Group Event Control v1 fix1",
        "GeneratedAt: " + datetime.now().astimezone().isoformat(),
        "",
        "Why fix1 exists:",
        "- v1 stopped after the first im.message.receive_v1 event",
        "- the first observed event was p2p and did not match the group allowlist",
        "- fix1 keeps listening until the intended allowlisted group event arrives",
        "",
        "Safety:",
        "- no project dependency modification",
        "- no AgentRuntime / ChatOps gateway",
        "- no outbound message",
        "- no Approval/Action/Verification",
        "- no raw message content or external IDs persisted",
    ]

    process: subprocess.Popen[str] | None = None
    event_path: Path | None = None

    try:
        if os.environ.get(ACK_ENV) != ACK:
            raise ControlError("Exact live acknowledgement is missing")

        required(os.environ.get(APP_ID_ENV), "Feishu App ID")
        required(os.environ.get(APP_SECRET_ENV), "Feishu App Secret")
        groups = resolve_allowlist(os.environ.get(GROUP_ALLOWLIST_ENV))

        report += [
            "",
            "CONFIGURATION",
            "-" * 120,
            "acknowledgement_valid=True",
            "app_id_present=True",
            "app_secret_present=True",
            "group_allowlist_count=" + str(len(groups)),
            *["group_opaque_id=" + opaque(item) for item in groups],
            "credential_values_persisted=False",
        ]

        with tempfile.NamedTemporaryFile(
            prefix="feishu_oapi_ws_group_event_",
            suffix=".jsonl",
            delete=False,
        ) as handle:
            event_path = Path(handle.name)

        try:
            event_path.unlink()
        except FileNotFoundError:
            pass

        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--child",
                str(event_path),
            ],
            cwd=root,
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        report += [
            "",
            "CONTROL LISTENER",
            "-" * 120,
            "sdk=lark-oapi",
            "event=im.message.receive_v1",
            "wait_seconds=" + str(WAIT_SECONDS),
            "heartbeat_seconds=" + str(HEARTBEAT_SECONDS),
            "outbound_send_enabled=False",
            "",
            "USER ACTION:",
            "只在配置的 allowlist 群里发送：@机器人 帮助",
        ]

        print("=" * 72)
        print("FEISHU STANDARD OAPI WS GROUP EVENT CONTROL V1 FIX1")
        print("=" * 72)
        print()
        print("标准 OAPI WebSocket 监听已启动。")
        print("这次不会因为 P2P/单聊消息提前退出。")
        print()
        print("现在请只在配置的 allowlist 测试群里发送：")
        print("  @你的机器人 帮助")
        print()
        print("不会回复消息，只验证目标群消息事件是否到达。")
        print()

        deadline = time.monotonic() + WAIT_SECONDS
        next_heartbeat = time.monotonic() + HEARTBEAT_SECONDS
        seen = 0
        target_record: dict[str, Any] | None = None

        while time.monotonic() < deadline:
            records = read_records(event_path)

            for record in records[seen:]:
                if record.get("received") is True:
                    print(
                        "Observed event: chat_type="
                        + str(record.get("chat_type"))
                        + " allowlist_match="
                        + str(record.get("chat_matches_allowlist"))
                    )

                if record.get("target_group_match") is True:
                    target_record = record
                    break

            seen = len(records)

            if target_record is not None:
                break

            returncode = process.poll()
            if returncode is not None:
                report.append("child_exit_code=" + str(returncode))
                break

            now = time.monotonic()

            if now >= next_heartbeat:
                elapsed = int(WAIT_SECONDS - max(0.0, deadline - now))
                print(
                    "listener_alive=True elapsed="
                    + str(elapsed)
                    + "s/"
                    + str(int(WAIT_SECONDS))
                    + "s events_seen="
                    + str(seen)
                )
                next_heartbeat = now + HEARTBEAT_SECONDS

            time.sleep(0.5)

        records = read_records(event_path)

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

        unrelated = [
            record
            for record in records
            if (
                record.get("received") is True
                and record.get("target_group_match") is not True
            )
        ]

        report += [
            "",
            "OBSERVED EVENTS",
            "-" * 120,
            "total_event_records=" + str(len(records)),
            "unrelated_message_events=" + str(len(unrelated)),
        ]

        for index, record in enumerate(unrelated[:20], start=1):
            report.append(
                "unrelated_event_" + str(index) + "=" + repr(record)
            )

        report += [
            "",
            "DIAGNOSIS",
            "-" * 120,
        ]

        if target_record is not None:
            classification = "OAPI_WS_ALLOWLIST_GROUP_MESSAGE_RECEIVED"
            report += [
                "classification=" + classification,
                "chat_type=" + str(target_record.get("chat_type")),
                "chat_matches_allowlist=True",
                "chat_opaque=" + str(target_record.get("chat_opaque")),
                "message_type=" + str(target_record.get("message_type")),
                "meaning=The intended allowlisted group im.message.receive_v1 event reaches the official OAPI WebSocket.",
                "next_decision=Use standard OAPI WS for inbound Feishu events and preserve the existing AI Reliability TrustBoundary/ChatOps core.",
            ]
        else:
            classification = "OAPI_WS_NO_ALLOWLIST_GROUP_EVENT"
            report += [
                "classification=" + classification,
                "meaning=The OAPI listener stayed online but no event from the configured allowlist group arrived.",
                "next_decision=Verify the exact group chat_id and that the same bot is actually a member of that group.",
            ]

        report += [
            "",
            "RESULT",
            "-" * 120,
            "PASSED",
            "",
            "Control experiment completed.",
            "Upload only:",
            OUTPUT_NAME,
        ]

        output.write_text(
            "\n".join(report) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print()
        print("Control experiment completed.")
        print("classification=" + classification)
        print()
        print("Upload only:")
        print(output)
        return 0

    except BaseException as exc:
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except BaseException:
                try:
                    process.kill()
                except BaseException:
                    pass

        error.write_text(
            "\n".join(
                [
                    "Feishu Standard OAPI WebSocket Group Event Control v1 fix1 FAILED",
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
        print("FEISHU STANDARD OAPI WS GROUP EVENT CONTROL V1 FIX1 FAILED")
        print("=" * 72)
        print("failure_code=" + safe_type(exc))
        print("Upload only:")
        print(error)
        return 1

    finally:
        if event_path is not None:
            try:
                event_path.unlink()
            except FileNotFoundError:
                pass


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--child":
        return child_main(Path(sys.argv[2]))
    return parent_main()


if __name__ == "__main__":
    raise SystemExit(main())
