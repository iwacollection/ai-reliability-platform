from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import os
import time

from datetime import datetime
from pathlib import Path
from typing import Any

from lark_channel import Events, FeishuChannel, PolicyConfig, SecurityConfig

from services.agent_runtime.app.conversation.chatops import ChatOpsConversationGateway
from services.agent_runtime.app.conversation.feishu import (
    FeishuActorAttestationRegistry,
    FeishuChatOpsAdapter,
    FeishuLongConnectionTrustBoundary,
)
from services.agent_runtime.app.conversation.feishu_channel_transport import (
    FeishuOfficialChannelTransport,
)
from services.agent_runtime.app.conversation.models import (
    ConversationIntent,
    ConversationReplyMode,
)
from services.agent_runtime.app.conversation.orchestrator import ConversationOrchestrator
from services.agent_runtime.app.conversation.provider import (
    DictConversationIncidentContextProvider,
)


OUTPUT_NAME = "feishu_live_read_only_message_path_proof_v1_after.txt"
ERROR_NAME = "feishu_live_read_only_message_path_proof_v1_error.txt"

ACKNOWLEDGEMENT = "I_ACKNOWLEDGE_REAL_FEISHU_NETWORK_CONNECTION"
APP_ID_ENV = "AI_RELIABILITY_FEISHU_APP_ID"
APP_SECRET_ENV = "AI_RELIABILITY_FEISHU_APP_SECRET"
GROUP_ALLOWLIST_ENV = "AI_RELIABILITY_FEISHU_GROUP_ALLOWLIST"
ACK_ENV = "AI_RELIABILITY_FEISHU_LIVE_ACK"

PROOF_TEXT = "帮助"
CONNECT_TIMEOUT_SECONDS = 30.0
OVERALL_CONNECT_TIMEOUT_SECONDS = 45.0
MESSAGE_WAIT_SECONDS = 180.0

EXPECTED_HASHES = {
    "pyproject.toml":
        "cc2f73d19fd71c810ebf23429e5ecb4f9bd8cf6fe65ece91ba3569ce2b7e82ce",
    "uv.lock":
        "e2bef32ca96b736bc104ea3f3999316223f1793c4b2663c30175ae5f5fce5722",
    "services/agent_runtime/app/conversation/feishu.py":
        "d3869bf3fb7e6e0a7ce43934979887106a380caf90cca615414d33a7560eeea1",
    "services/agent_runtime/app/conversation/feishu_channel_transport.py":
        "17e7cb678de5b478a0ba61f650bdb6c9c004272a23096e026b7f3cba1f34bcd8",
    "services/agent_runtime/app/conversation/chatops.py":
        "3c73a9a86bc34712a77ac3ea3196e44ee355989f0b869b73500e83d791d80966",
    "services/agent_runtime/app/conversation/orchestrator.py":
        "f41d09ae583479d65c486fea4d1e4d667fe81be0330a2c66c32225208a4789d1",
}


class ProofError(RuntimeError):
    pass


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate
    raise ProofError("Repository root not found")


def raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def required_secret(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 1024
        or "\x00" in value
    ):
        raise ProofError(label + " is unavailable or invalid")
    return value


def resolve_allowlist(value: Any) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ProofError("Feishu group allowlist is unavailable")

    items: list[str] = []
    seen: set[str] = set()

    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        if (
            len(item) > 256
            or "\x00" in item
            or any(ch.isspace() for ch in item)
            or not item.startswith("oc_")
        ):
            raise ProofError("Feishu group allowlist contains an invalid chat ID")
        if item not in seen:
            seen.add(item)
            items.append(item)

    if not items:
        raise ProofError("Feishu group allowlist cannot be empty")
    return tuple(items)


def opaque(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def failure_code(exc: BaseException) -> str:
    return (type(exc).__module__ + "." + type(exc).__name__)[:256]


async def async_main() -> int:
    root = find_repo_root(Path.cwd().resolve())
    output = root / OUTPUT_NAME
    error = root / ERROR_NAME

    for path in (output, error):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    report = [
        "Feishu Live Read-Only Message Path Proof v1",
        "GeneratedAt: " + datetime.now().astimezone().isoformat(),
        "",
        "Purpose:",
        "- prove one bounded real Feishu @bot HELP message end-to-end",
        "- official SDK typed event -> Feishu TrustBoundary",
        "- ChatOpsConversationGateway -> ConversationOrchestrator",
        "- render and send one real Feishu Card 2.0 reply",
        "",
        "Isolation:",
        "- no AgentRuntime",
        "- no durable Incident binding",
        "- no Runtime Incident read",
        "- no ChatOpsAuthenticatedWriteBridge",
        "- no card-action handler",
        "- only exact text '帮助' is processed",
        "- Approval/Action/Verification writes remain unavailable",
    ]

    stage = "baseline_preflight"
    channel = None
    connected = False

    try:
        report += ["", "=" * 120, "CURRENT BASELINE SHA256", "=" * 120, ""]
        for relative, expected in EXPECTED_HASHES.items():
            path = root / relative
            if not path.exists():
                raise ProofError("Required baseline file is missing: " + relative)
            actual = raw_sha256(path)
            report.append(relative + "=" + actual)
            if actual != expected:
                raise ProofError("Reviewed baseline changed: " + relative)

        stage = "dependency_contract"
        sdk_version = importlib.metadata.version("lark-channel-sdk")
        websockets_version = importlib.metadata.version("websockets")
        report += [
            "",
            "=" * 120,
            "DEPENDENCY CONTRACT",
            "=" * 120,
            "",
            "lark_channel_sdk_version=" + sdk_version,
            "websockets_version=" + websockets_version,
        ]
        if sdk_version != "1.2.0" or websockets_version != "15.0.1":
            raise ProofError("Unexpected Feishu Channel dependency version")

        stage = "live_configuration"
        if os.environ.get(ACK_ENV) != ACKNOWLEDGEMENT:
            raise ProofError("Exact live-network acknowledgement is missing")

        app_id = required_secret(os.environ.get(APP_ID_ENV), label="Feishu App ID")
        app_secret = required_secret(
            os.environ.get(APP_SECRET_ENV),
            label="Feishu App Secret",
        )
        group_allowlist = resolve_allowlist(os.environ.get(GROUP_ALLOWLIST_ENV))

        report += [
            "",
            "=" * 120,
            "LIVE CONFIGURATION",
            "=" * 120,
            "",
            "acknowledgement_valid=True",
            "app_id_present=True",
            "app_secret_present=True",
            "group_allowlist_count=" + str(len(group_allowlist)),
            *[
                "group_allowlist_opaque_id=" + opaque(item)
                for item in group_allowlist
            ],
            "credential_values_persisted=False",
            "raw_external_ids_persisted=False",
        ]

        policy = PolicyConfig(
            dm_policy="disabled",
            group_policy="allowlist",
            require_mention=True,
            respond_to_mention_all=False,
            group_allowlist=list(group_allowlist),
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

        # HELP is deliberately chosen because it needs no Incident/provider read.
        orchestrator = ConversationOrchestrator(
            provider=DictConversationIncidentContextProvider({})
        )
        gateway = ChatOpsConversationGateway(orchestrator=orchestrator)

        channel = FeishuChannel(
            app_id=app_id,
            app_secret=app_secret,
            transport="ws",
            policy=policy,
            security=security,
        )

        attestations = FeishuActorAttestationRegistry()
        adapter = FeishuChatOpsAdapter(
            trust_boundary=FeishuLongConnectionTrustBoundary(),
            attestations=attestations,
        )
        transport = FeishuOfficialChannelTransport(
            channel=channel,
            adapter=adapter,
            gateway=gateway,
            write_bridge=None,
        )

        finished = asyncio.Event()
        handler_failure: list[BaseException] = []
        proof: dict[str, Any] = {"ignored": 0}

        async def on_message(event) -> None:
            try:
                inbound = transport.normalize_message(event)

                # Exact probe gate: no temporary general-purpose bot behavior.
                if inbound.text != PROOF_TEXT:
                    proof["ignored"] += 1
                    return

                if inbound.conversation.conversation_id not in group_allowlist:
                    raise ProofError("Proof message escaped group allowlist")

                outbound = await gateway.handle(inbound)

                if (
                    outbound.reply.intent != ConversationIntent.HELP
                    or outbound.reply.mode != ConversationReplyMode.READ_ONLY
                ):
                    raise ProofError("Unexpected Conversation reply contract")

                rendered = adapter.render_outbound(outbound)
                card = rendered.get("card")

                if not isinstance(card, dict) or card.get("schema") != "2.0":
                    raise ProofError("Feishu Card 2.0 render failed")

                thread_id = getattr(event.conversation, "thread_id", None)
                send_started = time.perf_counter()

                result = await channel.send(
                    inbound.conversation.conversation_id,
                    {"card": card},
                    {
                        "reply_to": inbound.message_id,
                        "reply_in_thread": (
                            isinstance(thread_id, str) and bool(thread_id)
                        ),
                        "receive_id_type": "chat_id",
                        "uuid": hashlib.sha256(
                            ("feishu-live-proof-v1:" + inbound.message_id).encode(
                                "utf-8"
                            )
                        ).hexdigest(),
                    },
                )

                if getattr(result, "success", None) is False:
                    raise ProofError("Feishu Card send returned failure")

                proof.update(
                    {
                        "group": opaque(inbound.conversation.conversation_id),
                        "sender": opaque(inbound.external_actor_id or ""),
                        "message": opaque(inbound.message_id),
                        "intent": outbound.reply.intent.value,
                        "mode": outbound.reply.mode.value,
                        "card_schema": card.get("schema"),
                        "send_ms": int(
                            (time.perf_counter() - send_started) * 1000
                        ),
                    }
                )
                finished.set()

            except BaseException as exc:
                handler_failure.append(exc)
                finished.set()

        # Only MESSAGE is registered. No CARD_ACTION handler in this proof.
        channel.on(Events.MESSAGE, on_message)

        report += [
            "",
            "=" * 120,
            "MESSAGE PATH POLICY",
            "=" * 120,
            "",
            "security_mode=audit",
            "dm_policy=disabled",
            "group_policy=allowlist",
            "require_mention=True",
            "registered_event=message",
            "card_action_handler_registered=False",
            "write_bridge_attached=False",
            "agent_runtime_created=False",
            "proof_text=帮助",
            "message_wait_seconds=" + str(MESSAGE_WAIT_SECONDS),
        ]

        stage = "real_connectivity"
        connect_started = time.perf_counter()
        await asyncio.wait_for(
            channel.connect_until_ready(timeout=CONNECT_TIMEOUT_SECONDS),
            timeout=OVERALL_CONNECT_TIMEOUT_SECONDS,
        )
        connected = True

        print("=" * 72)
        print("FEISHU LIVE READ-ONLY MESSAGE PATH PROOF V1")
        print("=" * 72)
        print()
        print("Feishu Channel is ready.")
        print()
        print("请现在到允许的飞书测试群：")
        print("1. @你的机器人")
        print("2. 发送：帮助")
        print()
        print("成功后脚本会自动回复一张卡片并断开连接。")
        print()

        report += [
            "",
            "=" * 120,
            "REAL MESSAGE PATH",
            "=" * 120,
            "",
            "connect_ready=True",
            "connect_ready_elapsed_ms="
            + str(int((time.perf_counter() - connect_started) * 1000)),
            "operator_action=@机器人 帮助",
        ]

        stage = "wait_for_proof_message"
        await asyncio.wait_for(finished.wait(), timeout=MESSAGE_WAIT_SECONDS)

        if handler_failure:
            raise handler_failure[0]

        required = {"group", "sender", "message", "intent", "mode", "card_schema", "send_ms"}
        if not required.issubset(proof):
            raise ProofError("Proof completion data is incomplete")

        report += [
            "proof_message_received=True",
            "ignored_non_probe_messages=" + str(proof["ignored"]),
            "group_opaque_id=" + proof["group"],
            "sender_opaque_id=" + proof["sender"],
            "message_opaque_id=" + proof["message"],
            "conversation_intent=" + proof["intent"],
            "conversation_reply_mode=" + proof["mode"],
            "card_schema=" + proof["card_schema"],
            "card_send_elapsed_ms=" + str(proof["send_ms"]),
            "card_reply_sent=True",
            "raw_message_text_persisted=False",
            "raw_external_ids_persisted=False",
        ]

        stage = "disconnect"
        await channel.disconnect()
        connected = False

        report += [
            "disconnect_completed=True",
            "",
            "=" * 120,
            "RESULT",
            "=" * 120,
            "",
            "PASSED",
            "",
            "A real Feishu @bot HELP message crossed the read-only ChatOps path.",
            "ConversationOrchestrator returned HELP in READ_ONLY mode.",
            "A real Feishu Card 2.0 reply was sent.",
            "No AgentRuntime or authenticated write bridge was created.",
            "",
            "Next stage after review:",
            "- Feishu Live Runtime Incident Read Path v1",
            "- authenticated live write remains disabled",
            "",
            "Upload only:",
            OUTPUT_NAME,
        ]

        output.write_text(
            "\n".join(report) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print()
        print("=" * 72)
        print("FEISHU LIVE READ-ONLY MESSAGE PATH PROOF V1 PASSED")
        print("=" * 72)
        print()
        print("Real @bot message path: PASSED")
        print("Real Card 2.0 reply: PASSED")
        print("Authenticated write bridge: DISABLED")
        print()
        print("Upload only:")
        print(output)
        return 0

    except BaseException as exc:
        if channel is not None and connected:
            try:
                await channel.disconnect()
            except BaseException:
                pass

        report += [
            "",
            "=" * 120,
            "FAILURE",
            "=" * 120,
            "",
            "stage=" + stage,
            "failure_code=" + failure_code(exc),
            "raw_exception_text_persisted=False",
            "credential_values_persisted=False",
            "raw_external_ids_persisted=False",
        ]

        error.write_text(
            "\n".join(
                [
                    "Feishu Live Read-Only Message Path Proof v1 FAILED",
                    "GeneratedAt: " + datetime.now().astimezone().isoformat(),
                    "",
                    "stage=" + stage,
                    "failure_code=" + failure_code(exc),
                    "",
                    "Raw exception text is intentionally not persisted.",
                    "Credential and raw external identifier values are not persisted.",
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

        print()
        print("=" * 72)
        print("FEISHU LIVE READ-ONLY MESSAGE PATH PROOF V1 FAILED")
        print("=" * 72)
        print()
        print("stage=" + stage)
        print("failure_code=" + failure_code(exc))
        print()
        print("Upload only:")
        print(error)
        return 1


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
