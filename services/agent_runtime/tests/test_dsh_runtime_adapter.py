from __future__ import annotations

import sys
from pathlib import Path

import pytest

from services.agent_runtime.app.investigation.dsh_runtime_adapter import (
    DshRuntimeAdapter,
    DshRuntimeConfig,
    DshRuntimeProtocolError,
)


def _write_fake_runtime(
    path: Path,
    *,
    invalid_turn_end: bool = False,
    ignore_initialize: bool = False,
) -> None:
    source = """import json
import sys

INVALID_TURN_END = __INVALID__
IGNORE_INITIALIZE = __IGNORE__

def send(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\\n")
    sys.stdout.flush()

for raw in sys.stdin:
    if not raw.strip():
        continue

    message = json.loads(raw)
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        if IGNORE_INITIALIZE:
            continue
        send({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"protocolVersion": "test-v1"},
        })
        continue

    if method == "session/prompt":
        session_id = params["sessionId"]
        message_id = "message-test-1"

        send({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"messageId": message_id},
        })
        send({
            "jsonrpc": "2.0",
            "method": "session.event",
            "params": {
                "sessionId": session_id,
                "event": {
                    "type": "agent/inbox/spliced",
                    "data": {"inserted": [{"id": message_id}]},
                },
            },
        })
        send({
            "jsonrpc": "2.0",
            "method": "session.event",
            "params": {
                "sessionId": session_id,
                "event": {
                    "type": "assistant/message",
                    "data": {
                        "message": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": "read-only DSH result",
                                }
                            ]
                        }
                    },
                },
            },
        })
        reason = {} if INVALID_TURN_END else {"kind": "completed"}
        send({
            "jsonrpc": "2.0",
            "method": "session.event",
            "params": {
                "sessionId": session_id,
                "event": {
                    "type": "turn/end",
                    "data": {"reason": reason},
                },
            },
        })
        send({
            "jsonrpc": "2.0",
            "method": "session.status",
            "params": {
                "sessionId": session_id,
                "status": "idle",
            },
        })
        continue

    if method == "shutdown":
        send({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {},
        })
        break
"""
    source = source.replace(
        "__INVALID__",
        repr(invalid_turn_end),
    ).replace(
        "__IGNORE__",
        repr(ignore_initialize),
    )
    path.write_text(source, encoding="utf-8")


@pytest.mark.asyncio
async def test_dsh_runtime_adapter_round_trip_protocol(
    tmp_path: Path,
):
    runtime = tmp_path / "fake_dsh_runtime.py"
    _write_fake_runtime(runtime)

    adapter = DshRuntimeAdapter(
        DshRuntimeConfig(
            launch_args=(sys.executable, str(runtime)),
            cwd=str(tmp_path),
            request_timeout_seconds=2.0,
            turn_timeout_seconds=2.0,
        )
    )

    async with adapter:
        initialized = await adapter.initialize(
            cwd=str(tmp_path),
            provider="test-provider",
            model="test-model",
            max_tokens=1024,
        )
        assert initialized == {"protocolVersion": "test-v1"}

        result = await adapter.run_turn(
            "investigate read-only evidence",
            session_id="session-test-1",
        )

        assert result.session_id == "session-test-1"
        assert result.final_response == "read-only DSH result"
        assert result.finish_reason == "completed"
        assert [event["type"] for event in result.events] == [
            "agent/inbox/spliced",
            "assistant/message",
            "turn/end",
        ]
        assert any(
            item.method == "session.status"
            for item in result.notifications
        )

    assert adapter.running is False


@pytest.mark.asyncio
async def test_dsh_runtime_adapter_rejects_invalid_turn_end(
    tmp_path: Path,
):
    runtime = tmp_path / "fake_invalid_dsh_runtime.py"
    _write_fake_runtime(runtime, invalid_turn_end=True)

    adapter = DshRuntimeAdapter(
        DshRuntimeConfig(
            launch_args=(sys.executable, str(runtime)),
            cwd=str(tmp_path),
            request_timeout_seconds=2.0,
            turn_timeout_seconds=2.0,
        )
    )

    async with adapter:
        await adapter.initialize(
            cwd=str(tmp_path),
            provider="test-provider",
            model="test-model",
        )
        with pytest.raises(
            DshRuntimeProtocolError,
            match="turn/end",
        ):
            await adapter.run_turn(
                "invalid protocol",
                session_id="session-test-invalid",
            )


@pytest.mark.asyncio
async def test_dsh_runtime_adapter_request_timeout_is_bounded(
    tmp_path: Path,
):
    runtime = tmp_path / "fake_timeout_dsh_runtime.py"
    _write_fake_runtime(runtime, ignore_initialize=True)

    adapter = DshRuntimeAdapter(
        DshRuntimeConfig(
            launch_args=(sys.executable, str(runtime)),
            cwd=str(tmp_path),
            request_timeout_seconds=0.2,
            turn_timeout_seconds=1.0,
            shutdown_timeout_seconds=0.2,
        )
    )

    async with adapter:
        with pytest.raises(TimeoutError, match="initialize timed out"):
            await adapter.initialize(
                cwd=str(tmp_path),
                provider="test-provider",
                model="test-model",
            )


def test_dsh_runtime_config_is_fail_closed():
    with pytest.raises(ValueError, match="launch arguments"):
        DshRuntimeConfig(
            launch_args=(),
            cwd=".",
        )

    with pytest.raises(ValueError, match="turn_timeout_seconds"):
        DshRuntimeConfig(
            launch_args=("fake-runtime",),
            cwd=".",
            turn_timeout_seconds=0,
        )
