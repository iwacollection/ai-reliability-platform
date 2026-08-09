from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from datetime import datetime
from pathlib import Path
from typing import Any


OUTPUT_NAME = "feishu_group_membership_and_event_retest_v1_after.txt"
ERROR_NAME = "feishu_group_membership_and_event_retest_v1_error.txt"

ACK = "I_ACKNOWLEDGE_REAL_FEISHU_NETWORK_CONNECTION"
APP_ID_ENV = "AI_RELIABILITY_FEISHU_APP_ID"
APP_SECRET_ENV = "AI_RELIABILITY_FEISHU_APP_SECRET"
GROUP_ALLOWLIST_ENV = "AI_RELIABILITY_FEISHU_GROUP_ALLOWLIST"
ACK_ENV = "AI_RELIABILITY_FEISHU_LIVE_ACK"

TOKEN_URL = (
    "https://open.feishu.cn/open-apis/auth/v3/"
    "tenant_access_token/internal"
)
CHATS_URL = "https://open.feishu.cn/open-apis/im/v1/chats"

WAIT_SECONDS = 180.0
HEARTBEAT_SECONDS = 10.0


class RetestError(RuntimeError):
    pass


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate
    raise RetestError("Repository root not found")


def required(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
    ):
        raise RetestError(label + " is missing or invalid")
    return value


def resolve_allowlist(value: Any) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise RetestError("Feishu group allowlist is missing")

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
            raise RetestError(
                "Feishu group allowlist contains invalid chat_id"
            )

        if item not in seen:
            seen.add(item)
            items.append(item)

    if not items:
        raise RetestError("Feishu group allowlist is empty")

    return tuple(items)


def opaque(value: str | None) -> str:
    text = value or ""
    return (
        "sha256:"
        + hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest()[:16]
    )


def safe_type(exc: BaseException) -> str:
    return (
        type(exc).__module__
        + "."
        + type(exc).__name__
    )[:256]


def http_json(
    request: urllib.request.Request,
    *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            raw = response.read()

    except urllib.error.HTTPError as exc:
        raise RetestError(
            "Feishu OpenAPI returned HTTP "
            + str(exc.code)
        ) from exc

    except urllib.error.URLError as exc:
        raise RetestError(
            "Feishu OpenAPI network request failed"
        ) from exc

    try:
        value = json.loads(
            raw.decode("utf-8")
        )

    except Exception as exc:
        raise RetestError(
            "Feishu OpenAPI response is invalid JSON"
        ) from exc

    if not isinstance(value, dict):
        raise RetestError(
            "Feishu OpenAPI response must be an object"
        )

    return value


def get_token(
    app_id: str,
    app_secret: str,
) -> str:
    request = urllib.request.Request(
        TOKEN_URL,
        data=json.dumps(
            {
                "app_id": app_id,
                "app_secret": app_secret,
            },
            separators=(",", ":"),
        ).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
        },
    )

    response = http_json(request)

    if response.get("code") not in (0, None):
        raise RetestError(
            "Feishu tenant token request failed"
        )

    token = response.get(
        "tenant_access_token"
    )

    if (
        not isinstance(token, str)
        or not token
    ):
        raise RetestError(
            "Feishu tenant token is unavailable"
        )

    return token


def get_bot_chats(
    token: str,
) -> list[dict[str, Any]]:
    chats: list[dict[str, Any]] = []
    page_token: str | None = None

    for _ in range(100):
        query = {
            "page_size": "100",
        }

        if page_token:
            query["page_token"] = page_token

        request = urllib.request.Request(
            CHATS_URL
            + "?"
            + urllib.parse.urlencode(
                query
            ),
            method="GET",
            headers={
                "Authorization": (
                    "Bearer "
                    + token
                ),
            },
        )

        response = http_json(request)

        if response.get("code") != 0:
            raise RetestError(
                "Feishu bot chat list request failed"
            )

        data = response.get("data")

        if not isinstance(data, dict):
            raise RetestError(
                "Feishu bot chat list data is invalid"
            )

        items = data.get(
            "items",
            [],
        )

        if not isinstance(items, list):
            raise RetestError(
                "Feishu bot chat list items are invalid"
            )

        chats.extend(
            item
            for item in items
            if isinstance(item, dict)
        )

        if not bool(
            data.get("has_more")
        ):
            break

        next_token = data.get(
            "page_token"
        )

        if (
            not isinstance(
                next_token,
                str,
            )
            or not next_token
        ):
            raise RetestError(
                "Feishu pagination token is missing"
            )

        page_token = next_token

    return chats


def safe_name(value: Any) -> str:
    if not isinstance(value, str):
        return "<UNNAMED>"

    value = " ".join(
        value.split()
    )

    return (
        value[:120]
        if value
        else "<UNNAMED>"
    )


def append_record(
    path: Path,
    record: dict[str, Any],
) -> None:
    with path.open(
        "a",
        encoding="utf-8",
        newline="\n",
    ) as handle:
        handle.write(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )


def child_main(
    event_file: Path,
) -> int:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        P2ImMessageReceiveV1,
    )

    app_id = required(
        os.environ.get(
            APP_ID_ENV
        ),
        "Feishu App ID",
    )

    app_secret = required(
        os.environ.get(
            APP_SECRET_ENV
        ),
        "Feishu App Secret",
    )

    allowed = set(
        resolve_allowlist(
            os.environ.get(
                GROUP_ALLOWLIST_ENV
            )
        )
    )

    def on_message(
        data: P2ImMessageReceiveV1,
    ) -> None:
        try:
            event = getattr(
                data,
                "event",
                None,
            )

            message = getattr(
                event,
                "message",
                None,
            )

            sender = getattr(
                event,
                "sender",
                None,
            )

            sender_id = getattr(
                sender,
                "sender_id",
                None,
            )

            chat_id = getattr(
                message,
                "chat_id",
                None,
            )

            chat_type = str(
                getattr(
                    message,
                    "chat_type",
                    None,
                )
            )

            message_id = getattr(
                message,
                "message_id",
                None,
            )

            message_type = str(
                getattr(
                    message,
                    "message_type",
                    None,
                )
            )

            open_id = getattr(
                sender_id,
                "open_id",
                None,
            )

            matches = (
                isinstance(
                    chat_id,
                    str,
                )
                and chat_id
                in allowed
            )

            append_record(
                event_file,
                {
                    "received": True,
                    "chat_opaque": opaque(
                        chat_id
                        if isinstance(
                            chat_id,
                            str,
                        )
                        else None
                    ),
                    "sender_opaque": opaque(
                        open_id
                        if isinstance(
                            open_id,
                            str,
                        )
                        else None
                    ),
                    "message_opaque": opaque(
                        message_id
                        if isinstance(
                            message_id,
                            str,
                        )
                        else None
                    ),
                    "chat_type": chat_type,
                    "message_type": (
                        message_type
                    ),
                    "chat_matches_allowlist": (
                        matches
                    ),
                    "target_group_match": (
                        matches
                        and chat_type
                        in {
                            "group",
                            "topic",
                        }
                    ),
                    "raw_content_persisted": False,
                    "raw_external_ids_persisted": False,
                },
            )

        except BaseException as exc:
            append_record(
                event_file,
                {
                    "received": False,
                    "handler_failure_code": (
                        safe_type(exc)
                    ),
                },
            )

    dispatcher = (
        lark.EventDispatcherHandler
        .builder("", "")
        .register_p2_im_message_receive_v1(
            on_message
        )
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


def read_records(
    path: Path,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    result: list[
        dict[str, Any]
    ] = []

    for line in path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines():
        if not line.strip():
            continue

        try:
            value = json.loads(
                line
            )
        except json.JSONDecodeError:
            continue

        if isinstance(
            value,
            dict,
        ):
            result.append(
                value
            )

    return result


def parent_main() -> int:
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
        "Feishu Group Membership + Event Retest v1",
        (
            "GeneratedAt: "
            + datetime.now()
            .astimezone()
            .isoformat()
        ),
        "",
        "Purpose:",
        "- re-test after bot/agent was added to the target group",
        "- verify application bot group membership first",
        "- only if membership matches, verify target group im.message.receive_v1",
        "",
        "Safety:",
        "- read-only group listing",
        "- no project dependency modification",
        "- no AgentRuntime / ChatOps gateway",
        "- no outbound message",
        "- no Approval/Action/Verification",
        "- raw App Secret and raw external IDs are not persisted",
    ]

    process: subprocess.Popen[
        str
    ] | None = None

    event_path: Path | None = None

    try:
        if os.environ.get(
            ACK_ENV
        ) != ACK:
            raise RetestError(
                "Exact live acknowledgement is missing"
            )

        app_id = required(
            os.environ.get(
                APP_ID_ENV
            ),
            "Feishu App ID",
        )

        app_secret = required(
            os.environ.get(
                APP_SECRET_ENV
            ),
            "Feishu App Secret",
        )

        configured = resolve_allowlist(
            os.environ.get(
                GROUP_ALLOWLIST_ENV
            )
        )

        token = get_token(
            app_id,
            app_secret,
        )

        chats = get_bot_chats(
            token
        )

        visible_ids = {
            item.get("chat_id")
            for item in chats
            if isinstance(
                item.get(
                    "chat_id"
                ),
                str,
            )
        }

        matched = [
            item
            for item in configured
            if item in visible_ids
        ]

        report += [
            "",
            "GROUP MEMBERSHIP",
            "-" * 120,
            (
                "bot_visible_group_count="
                + str(
                    len(visible_ids)
                )
            ),
            (
                "configured_allowlist_count="
                + str(
                    len(configured)
                )
            ),
            (
                "configured_group_match_count="
                + str(
                    len(matched)
                )
            ),
            *[
                (
                    "configured_group_opaque_id="
                    + opaque(item)
                )
                for item
                in configured
            ],
        ]

        print("=" * 72)
        print(
            "FEISHU GROUP MEMBERSHIP + EVENT RETEST V1"
        )
        print("=" * 72)
        print()
        print(
            "Bot-visible groups:"
        )

        for item in chats:
            chat_id = item.get(
                "chat_id"
            )

            if isinstance(
                chat_id,
                str,
            ):
                print(
                    "  "
                    + safe_name(
                        item.get(
                            "name"
                        )
                    )
                    + " -> "
                    + chat_id
                )

        print()

        if len(matched) != len(
            configured
        ):
            classification = (
                "GROUP_MEMBERSHIP_NOT_MATCHED"
            )

            report += [
                (
                    "classification="
                    + classification
                ),
                (
                    "meaning=Configured allowlist group is not visible to this application bot."
                ),
                (
                    "event_test_started=False"
                ),
            ]

            output.write_text(
                "\n".join(report)
                + "\n",
                encoding="utf-8",
                newline="\n",
            )

            print(
                "classification="
                + classification
            )
            print(
                "事件监听未启动；先修正群成员关系或 allowlist。"
            )
            print()
            print(
                "Upload only:"
            )
            print(output)

            return 0

        print(
            "Group membership: MATCHED"
        )
        print()
        print(
            "准备启动标准 OAPI WebSocket 群消息监听..."
        )

        with tempfile.NamedTemporaryFile(
            prefix="feishu_group_retest_",
            suffix=".jsonl",
            delete=False,
        ) as handle:
            event_path = Path(
                handle.name
            )

        try:
            event_path.unlink()
        except FileNotFoundError:
            pass

        process = subprocess.Popen(
            [
                sys.executable,
                str(
                    Path(
                        __file__
                    ).resolve()
                ),
                "--child",
                str(
                    event_path
                ),
            ],
            cwd=root,
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        report += [
            "",
            "EVENT RETEST",
            "-" * 120,
            "sdk=lark-oapi",
            "event=im.message.receive_v1",
            (
                "wait_seconds="
                + str(
                    WAIT_SECONDS
                )
            ),
        ]

        print()
        print(
            "标准 OAPI WebSocket 监听已启动。"
        )
        print()
        print(
            "现在请在刚才匹配成功的测试群里："
        )
        print(
            "  @你的机器人 帮助"
        )
        print()
        print(
            "本测试不会回复消息。"
        )
        print()

        deadline = (
            time.monotonic()
            + WAIT_SECONDS
        )

        next_heartbeat = (
            time.monotonic()
            + HEARTBEAT_SECONDS
        )

        seen = 0
        target: dict[
            str,
            Any
        ] | None = None

        while (
            time.monotonic()
            < deadline
        ):
            records = read_records(
                event_path
            )

            for record in records[
                seen:
            ]:
                if record.get(
                    "received"
                ) is True:
                    print(
                        "Observed event: chat_type="
                        + str(
                            record.get(
                                "chat_type"
                            )
                        )
                        + " allowlist_match="
                        + str(
                            record.get(
                                "chat_matches_allowlist"
                            )
                        )
                    )

                if record.get(
                    "target_group_match"
                ) is True:
                    target = record
                    break

            seen = len(
                records
            )

            if target is not None:
                break

            if (
                process.poll()
                is not None
            ):
                break

            now = time.monotonic()

            if (
                now
                >= next_heartbeat
            ):
                elapsed = int(
                    WAIT_SECONDS
                    - max(
                        0.0,
                        deadline - now,
                    )
                )

                print(
                    "listener_alive=True elapsed="
                    + str(
                        elapsed
                    )
                    + "s/"
                    + str(
                        int(
                            WAIT_SECONDS
                        )
                    )
                    + "s events_seen="
                    + str(
                        seen
                    )
                )

                next_heartbeat = (
                    now
                    + HEARTBEAT_SECONDS
                )

            time.sleep(
                0.5
            )

        records = read_records(
            event_path
        )

        if (
            process.poll()
            is None
        ):
            process.terminate()

            try:
                process.wait(
                    timeout=5
                )
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(
                    timeout=5
                )

        report += [
            "",
            "EVENT OBSERVATION",
            "-" * 120,
            (
                "total_event_records="
                + str(
                    len(records)
                )
            ),
        ]

        if target is not None:
            classification = (
                "GROUP_EVENT_RECEIVED"
            )

            report += [
                (
                    "classification="
                    + classification
                ),
                (
                    "chat_type="
                    + str(
                        target.get(
                            "chat_type"
                        )
                    )
                ),
                (
                    "message_type="
                    + str(
                        target.get(
                            "message_type"
                        )
                    )
                ),
                (
                    "chat_matches_allowlist=True"
                ),
                (
                    "meaning=The configured group event now reaches the official OAPI WebSocket."
                ),
                (
                    "next_decision=Proceed to repair AI Reliability Feishu inbound transport using standard OAPI WS."
                ),
            ]

        else:
            classification = (
                "GROUP_EVENT_STILL_NOT_RECEIVED"
            )

            report += [
                (
                    "classification="
                    + classification
                ),
                (
                    "meaning=Bot group membership matches, but no target group event reached OAPI WS during the test window."
                ),
                (
                    "next_decision=Inspect Feishu group event scope/application availability rather than AI Reliability business code."
                ),
            ]

        report += [
            "",
            "RESULT",
            "-" * 120,
            "PASSED",
            "",
            (
                "classification="
                + classification
            ),
            "",
            "Upload only:",
            OUTPUT_NAME,
        ]

        output.write_text(
            "\n".join(
                report
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print()
        print(
            "classification="
            + classification
        )
        print()
        print(
            "Upload only:"
        )
        print(output)

        return 0

    except BaseException as exc:
        if (
            process is not None
            and process.poll()
            is None
        ):
            try:
                process.terminate()
                process.wait(
                    timeout=5
                )
            except BaseException:
                try:
                    process.kill()
                except BaseException:
                    pass

        error.write_text(
            "\n".join(
                [
                    "Feishu Group Membership + Event Retest v1 FAILED",
                    (
                        "GeneratedAt: "
                        + datetime.now()
                        .astimezone()
                        .isoformat()
                    ),
                    "",
                    (
                        "failure_code="
                        + safe_type(
                            exc
                        )
                    ),
                    (
                        "raw_exception_text_persisted=False"
                    ),
                    (
                        "credential_values_persisted=False"
                    ),
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
            "FEISHU GROUP MEMBERSHIP + EVENT RETEST V1 FAILED"
        )
        print("=" * 72)
        print(
            "failure_code="
            + safe_type(
                exc
            )
        )
        print(
            "Upload only:"
        )
        print(error)

        return 1

    finally:
        if event_path is not None:
            try:
                event_path.unlink()
            except FileNotFoundError:
                pass


def main() -> int:
    if (
        len(
            sys.argv
        )
        >= 3
        and sys.argv[
            1
        ]
        == "--child"
    ):
        return child_main(
            Path(
                sys.argv[
                    2
                ]
            )
        )

    return parent_main()


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
