from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from datetime import datetime
from pathlib import Path
from typing import Any


OUTPUT_NAME = "feishu_bot_group_membership_diagnostic_v1_after.txt"
ERROR_NAME = "feishu_bot_group_membership_diagnostic_v1_error.txt"

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


class DiagnosticError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        stage: str,
        http_status: int | None = None,
        feishu_code: Any = None,
        feishu_msg: Any = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.http_status = http_status
        self.feishu_code = feishu_code
        self.feishu_msg = feishu_msg


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate

    raise DiagnosticError(
        "Repository root not found",
        stage="repository_root",
    )


def required(
    value: Any,
    label: str,
    *,
    stage: str,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
    ):
        raise DiagnosticError(
            label + " is missing or invalid",
            stage=stage,
        )

    return value


def resolve_allowlist(
    value: Any,
) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise DiagnosticError(
            "Feishu group allowlist is missing",
            stage="configuration",
        )

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
            raise DiagnosticError(
                "Feishu group allowlist contains invalid chat_id",
                stage="configuration",
            )

        if item not in seen:
            seen.add(item)
            items.append(item)

    if not items:
        raise DiagnosticError(
            "Feishu group allowlist is empty",
            stage="configuration",
        )

    return tuple(items)


def opaque(
    value: str | None,
) -> str:
    text = value or ""

    return (
        "sha256:"
        + hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest()[:16]
    )


def safe_text(
    value: Any,
    *,
    max_length: int = 256,
) -> str:
    if not isinstance(value, str):
        return "<NONE>"

    value = " ".join(
        value.split()
    )

    return value[:max_length]


def decode_json_bytes(
    raw: bytes,
) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8")
        )
    except Exception:
        return {}

    return (
        value
        if isinstance(value, dict)
        else {}
    )


def http_json(
    request: urllib.request.Request,
    *,
    stage: str,
    timeout: float = 20.0,
) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            raw = response.read()

            return (
                int(
                    getattr(
                        response,
                        "status",
                        200,
                    )
                ),
                decode_json_bytes(raw),
            )

    except urllib.error.HTTPError as exc:
        raw = b""

        try:
            raw = exc.read()
        except Exception:
            pass

        body = decode_json_bytes(raw)

        raise DiagnosticError(
            "Feishu OpenAPI HTTP error",
            stage=stage,
            http_status=exc.code,
            feishu_code=body.get("code"),
            feishu_msg=body.get("msg"),
        ) from exc

    except urllib.error.URLError as exc:
        raise DiagnosticError(
            "Feishu OpenAPI network error",
            stage=stage,
        ) from exc


def get_token(
    *,
    app_id: str,
    app_secret: str,
) -> tuple[str, int, dict[str, Any]]:
    request = urllib.request.Request(
        TOKEN_URL,
        data=json.dumps(
            {
                "app_id": app_id,
                "app_secret": app_secret,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
        },
    )

    status, response = http_json(
        request,
        stage="tenant_access_token",
    )

    code = response.get("code")

    if code not in (0, None):
        raise DiagnosticError(
            "Feishu tenant access token request failed",
            stage="tenant_access_token",
            http_status=status,
            feishu_code=code,
            feishu_msg=response.get("msg"),
        )

    token = response.get(
        "tenant_access_token"
    )

    if (
        not isinstance(token, str)
        or not token
    ):
        raise DiagnosticError(
            "Feishu tenant access token missing",
            stage="tenant_access_token",
            http_status=status,
            feishu_code=code,
            feishu_msg=response.get("msg"),
        )

    return token, status, response


def get_bot_chats(
    *,
    token: str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    chats: list[
        dict[str, Any]
    ] = []

    pages: list[
        dict[str, Any]
    ] = []

    page_token: str | None = None

    for page_number in range(
        1,
        101,
    ):
        query = {
            "page_size": "100",
        }

        if page_token:
            query[
                "page_token"
            ] = page_token

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
                "Content-Type": (
                    "application/json; charset=utf-8"
                ),
            },
        )

        status, response = http_json(
            request,
            stage="bot_chat_list",
        )

        code = response.get(
            "code"
        )

        pages.append(
            {
                "page": page_number,
                "http_status": status,
                "feishu_code": code,
                "feishu_msg": safe_text(
                    response.get(
                        "msg"
                    )
                ),
            }
        )

        if code != 0:
            raise DiagnosticError(
                "Feishu bot chat list request failed",
                stage="bot_chat_list",
                http_status=status,
                feishu_code=code,
                feishu_msg=response.get(
                    "msg"
                ),
            )

        data = response.get(
            "data"
        )

        if not isinstance(
            data,
            dict,
        ):
            raise DiagnosticError(
                "Feishu bot chat list data invalid",
                stage="bot_chat_list",
                http_status=status,
                feishu_code=code,
                feishu_msg=response.get(
                    "msg"
                ),
            )

        items = data.get(
            "items",
            [],
        )

        if not isinstance(
            items,
            list,
        ):
            raise DiagnosticError(
                "Feishu bot chat list items invalid",
                stage="bot_chat_list",
                http_status=status,
                feishu_code=code,
                feishu_msg=response.get(
                    "msg"
                ),
            )

        for item in items:
            if isinstance(
                item,
                dict,
            ):
                chats.append(
                    item
                )

        if not bool(
            data.get(
                "has_more"
            )
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
            raise DiagnosticError(
                "Feishu bot chat pagination token missing",
                stage="bot_chat_list",
            )

        page_token = next_token

    return chats, pages


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
        "Feishu Bot Group Membership Diagnostic v1",
        (
            "GeneratedAt: "
            + datetime.now()
            .astimezone()
            .isoformat()
        ),
        "",
        "Purpose:",
        "- locate the exact failure stage from the previous membership retest",
        "- validate tenant token acquisition",
        "- validate official GET /open-apis/im/v1/chats",
        "- compare configured allowlist against bot-visible groups when API succeeds",
        "",
        "Safety:",
        "- read-only OpenAPI requests",
        "- no project modification",
        "- no message send",
        "- no AgentRuntime / ChatOps / Approval / Action / Verification",
        "- App ID/App Secret/access token are never persisted",
        "- raw chat IDs are not persisted to the upload report",
        "- Feishu code/msg are persisted because they are diagnostic API metadata, not credentials",
    ]

    try:
        if os.environ.get(
            ACK_ENV
        ) != ACK:
            raise DiagnosticError(
                "Exact live acknowledgement is missing",
                stage="configuration",
            )

        app_id = required(
            os.environ.get(
                APP_ID_ENV
            ),
            "Feishu App ID",
            stage="configuration",
        )

        app_secret = required(
            os.environ.get(
                APP_SECRET_ENV
            ),
            "Feishu App Secret",
            stage="configuration",
        )

        configured = resolve_allowlist(
            os.environ.get(
                GROUP_ALLOWLIST_ENV
            )
        )

        report += [
            "",
            "CONFIGURATION",
            "-" * 120,
            "acknowledgement_valid=True",
            "app_id_present=True",
            "app_secret_present=True",
            (
                "configured_allowlist_count="
                + str(
                    len(configured)
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
            "credential_values_persisted=False",
        ]

        token, token_status, token_response = get_token(
            app_id=app_id,
            app_secret=app_secret,
        )

        report += [
            "",
            "TENANT ACCESS TOKEN",
            "-" * 120,
            "stage=tenant_access_token",
            "http_status="
            + str(
                token_status
            ),
            "feishu_code="
            + str(
                token_response.get(
                    "code"
                )
            ),
            "feishu_msg="
            + safe_text(
                token_response.get(
                    "msg"
                )
            ),
            "token_present=True",
            "token_persisted=False",
        ]

        chats, pages = get_bot_chats(
            token=token
        )

        report += [
            "",
            "BOT CHAT LIST API",
            "-" * 120,
            "stage=bot_chat_list",
            (
                "pages="
                + str(
                    len(pages)
                )
            ),
        ]

        for page in pages:
            report += [
                (
                    "page_"
                    + str(
                        page[
                            "page"
                        ]
                    )
                    + "_http_status="
                    + str(
                        page[
                            "http_status"
                        ]
                    )
                ),
                (
                    "page_"
                    + str(
                        page[
                            "page"
                        ]
                    )
                    + "_feishu_code="
                    + str(
                        page[
                            "feishu_code"
                        ]
                    )
                ),
                (
                    "page_"
                    + str(
                        page[
                            "page"
                        ]
                    )
                    + "_feishu_msg="
                    + str(
                        page[
                            "feishu_msg"
                        ]
                    )
                ),
            ]

        visible: dict[
            str,
            str,
        ] = {}

        for item in chats:
            chat_id = item.get(
                "chat_id"
            )

            if not isinstance(
                chat_id,
                str,
            ):
                continue

            name = safe_text(
                item.get(
                    "name"
                ),
                max_length=120,
            )

            visible[
                chat_id
            ] = name

        matched = [
            item
            for item in configured
            if item in visible
        ]

        report += [
            "",
            "GROUP MEMBERSHIP RESULT",
            "-" * 120,
            (
                "bot_visible_group_count="
                + str(
                    len(visible)
                )
            ),
            (
                "configured_group_match_count="
                + str(
                    len(matched)
                )
            ),
        ]

        for index, (
            chat_id,
            name,
        ) in enumerate(
            visible.items(),
            start=1,
        ):
            report += [
                (
                    "group_"
                    + str(index)
                    + "_name="
                    + name
                ),
                (
                    "group_"
                    + str(index)
                    + "_opaque_id="
                    + opaque(
                        chat_id
                    )
                ),
            ]

        if len(
            matched
        ) == len(
            configured
        ):
            classification = (
                "ALLOWLIST_GROUP_VISIBLE_TO_APP_BOT"
            )

            meaning = (
                "Configured allowlist group is visible to this App ID's official application bot."
            )

        else:
            classification = (
                "ALLOWLIST_GROUP_NOT_VISIBLE_TO_APP_BOT"
            )

            meaning = (
                "Configured allowlist group is not visible to this App ID's official application bot."
            )

        report += [
            "",
            "DIAGNOSIS",
            "-" * 120,
            (
                "classification="
                + classification
            ),
            (
                "meaning="
                + meaning
            ),
            "",
            "RESULT",
            "-" * 120,
            "PASSED",
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

        print("=" * 72)
        print(
            "FEISHU BOT GROUP MEMBERSHIP DIAGNOSTIC V1"
        )
        print("=" * 72)
        print()
        print(
            "classification="
            + classification
        )
        print(
            "bot_visible_group_count="
            + str(
                len(visible)
            )
        )
        print(
            "configured_group_match_count="
            + str(
                len(matched)
            )
        )
        print()
        print(
            "Bot-visible groups (local only):"
        )

        for chat_id, name in (
            visible.items()
        ):
            print(
                "  "
                + name
                + " -> "
                + chat_id
            )

        print()
        print(
            "Upload only:"
        )
        print(output)

        return 0

    except DiagnosticError as exc:
        error_report = [
            "Feishu Bot Group Membership Diagnostic v1 FAILED",
            (
                "GeneratedAt: "
                + datetime.now()
                .astimezone()
                .isoformat()
            ),
            "",
            (
                "stage="
                + exc.stage
            ),
            (
                "failure_code="
                + (
                    type(exc).__module__
                    + "."
                    + type(exc).__name__
                )
            ),
            (
                "http_status="
                + str(
                    exc.http_status
                )
            ),
            (
                "feishu_code="
                + str(
                    exc.feishu_code
                )
            ),
            (
                "feishu_msg="
                + safe_text(
                    exc.feishu_msg
                )
            ),
            "raw_exception_text_persisted=False",
            "credential_values_persisted=False",
            "",
            "Interpretation hint:",
            "- stage=tenant_access_token => App ID/App Secret/token path",
            "- stage=bot_chat_list with permission-related Feishu msg => enable a supported group information permission",
            "- stage=bot_chat_list with code=0 should not fail; inspect response-shape compatibility",
            "",
            "Upload only:",
            ERROR_NAME,
        ]

        error.write_text(
            "\n".join(
                error_report
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print(
            "FEISHU BOT GROUP MEMBERSHIP DIAGNOSTIC V1 FAILED"
        )
        print("=" * 72)
        print()
        print(
            "stage="
            + exc.stage
        )
        print(
            "http_status="
            + str(
                exc.http_status
            )
        )
        print(
            "feishu_code="
            + str(
                exc.feishu_code
            )
        )
        print(
            "feishu_msg="
            + safe_text(
                exc.feishu_msg
            )
        )
        print()
        print(
            "Upload only:"
        )
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
