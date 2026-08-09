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


OUTPUT_NAME = "feishu_oapi_ws_event_control_v1_after.txt"
ERROR_NAME = "feishu_oapi_ws_event_control_v1_error.txt"

ACK = "I_ACKNOWLEDGE_REAL_FEISHU_NETWORK_CONNECTION"
APP_ID_ENV = "AI_RELIABILITY_FEISHU_APP_ID"
APP_SECRET_ENV = "AI_RELIABILITY_FEISHU_APP_SECRET"
GROUP_ALLOWLIST_ENV = "AI_RELIABILITY_FEISHU_GROUP_ALLOWLIST"
ACK_ENV = "AI_RELIABILITY_FEISHU_LIVE_ACK"

WAIT_SECONDS = 120.0


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

    result: list[str] = []
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
            result.append(item)

    if not result:
        raise ControlError("Feishu group allowlist is empty")

    return tuple(result)


def opaque(value: str | None) -> str:
    text = value or ""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def safe_type(exc: BaseException) -> str:
    return (type(exc).__module__ + "." + type(exc).__name__)[:256]


def child_main(event_file: Path) -> int:
    # Imported only in child mode. Parent is run with:
    # uv run --with lark-oapi==1.7.1 ...
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

            record = {
                "received": True,
                "chat_opaque": opaque(chat_id if isinstance(chat_id, str) else None),
                "message_opaque": opaque(
                    message_id if isinstance(message_id, str) else None
                ),
                "sender_opaque": opaque(open_id if isinstance(open_id, str) else None),
                "message_type": str(message_type),
                "chat_type": str(chat_type),
                "chat_matches_allowlist": (
                    isinstance(chat_id, str) and chat_id in allowed
                ),
                "raw_content_persisted": False,
                "raw_external_ids_persisted": False,
            }

            event_file.write_text(
                json.dumps(record, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
        except BaseException as exc:
            event_file.write_text(
                json.dumps(
                    {
                        "received": False,
                        "handler_failure_code": safe_type(exc),
                        "raw_exception_text_persisted": False,
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
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

    # Official SDK start() is intentionally blocking.
    client.start()
    return 0


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
        "Feishu Standard OAPI WebSocket Event Control v1",
        "GeneratedAt: " + datetime.now().astimezone().isoformat(),
        "",
        "Purpose:",
        "- independent control for standard im.message.receive_v1 delivery",
        "- use official lark-oapi WebSocket event dispatcher",
        "- compare against prior lark-channel-sdk NO_INBOUND_EVENT result",
        "",
        "Isolation:",
        "- no project dependency modification",
        "- no pyproject.toml / uv.lock change",
        "- no AgentRuntime / ChatOps gateway",
        "- no outbound Feishu message",
        "- no Approval/Action/Verification",
        "- no raw message content persisted",
        "- no raw App ID/App Secret/group/sender/message ID persisted",
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
            prefix="feishu_oapi_ws_event_",
            suffix=".json",
            delete=False,
        ) as handle:
            event_path = Path(handle.name)

        try:
            event_path.unlink()
        except FileNotFoundError:
            pass

        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child",
            str(event_path),
        ]

        process = subprocess.Popen(
            command,
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
            "outbound_send_enabled=False",
            "wait_seconds=" + str(WAIT_SECONDS),
            "",
            "USER ACTION:",
            "在同一个 allowlist 测试群里，发送：@机器人 帮助",
        ]

        print("=" * 72)
        print("FEISHU STANDARD OAPI WS EVENT CONTROL V1")
        print("=" * 72)
        print()
        print("标准 OAPI WebSocket 监听已启动。")
        print()
        print("现在请在同一个 allowlist 测试群里发送：")
        print("  @你的机器人 帮助")
        print()
        print("本对照实验不会回复消息，只验证 im.message.receive_v1 是否到达。")
        print()

        deadline = time.monotonic() + WAIT_SECONDS
        classification = "OAPI_WS_NO_EVENT"
        event_record: dict[str, Any] | None = None

        while time.monotonic() < deadline:
            if event_path.exists():
                try:
                    event_record = json.loads(
                        event_path.read_text(encoding="utf-8")
                    )
                except Exception as exc:
                    raise ControlError("Control event result is unreadable") from exc

                if event_record.get("received") is True:
                    classification = "OAPI_WS_MESSAGE_RECEIVED"
                else:
                    classification = "OAPI_WS_HANDLER_FAILED"
                break

            returncode = process.poll()
            if returncode is not None:
                classification = "OAPI_WS_CHILD_EXITED"
                report.append("child_exit_code=" + str(returncode))
                break

            time.sleep(0.5)

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

        report += [
            "",
            "OBSERVATION",
            "-" * 120,
            "classification=" + classification,
        ]

        if event_record:
            for key in sorted(event_record):
                report.append(key + "=" + str(event_record[key]))

        report += [
            "",
            "INTERPRETATION",
            "-" * 120,
        ]

        if classification == "OAPI_WS_MESSAGE_RECEIVED":
            report += [
                "meaning=Standard Feishu OpenAPI WebSocket event delivery works.",
                "next_decision=Inbound transport should move from lark-channel-sdk message listening to standard lark-oapi event WebSocket, while preserving our existing ChatOps normalization/security boundaries.",
            ]
        elif classification == "OAPI_WS_NO_EVENT":
            report += [
                "meaning=Neither high-level Channel SDK nor standard OAPI WS received im.message.receive_v1.",
                "next_decision=Investigate Feishu application event-delivery configuration/tenant/chat scope; do not modify AI Reliability business code.",
            ]
        elif classification == "OAPI_WS_CHILD_EXITED":
            report += [
                "meaning=Standard OAPI WS listener exited before receiving an event.",
                "next_decision=Inspect SDK/runtime compatibility with a sanitized child diagnostic before changing business code.",
            ]
        else:
            report += [
                "meaning=Event arrived but the control handler failed to normalize its typed envelope.",
                "next_decision=Inspect current lark-oapi typed event shape.",
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
                    "Feishu Standard OAPI WebSocket Event Control v1 FAILED",
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
        print("FEISHU STANDARD OAPI WS EVENT CONTROL V1 FAILED")
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
