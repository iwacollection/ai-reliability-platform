from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]


class DshRuntimeError(RuntimeError):
    """Base error for the isolated DeepSeek Harness runtime boundary."""


class DshRuntimeProtocolError(DshRuntimeError):
    """The DSH stdio stream violated the bounded JSON-RPC contract."""


class DshRuntimeClosedError(DshRuntimeError):
    """The DSH subprocess closed before a pending operation completed."""


@dataclass(frozen=True, slots=True)
class DshRuntimeConfig:
    launch_args: tuple[str, ...]
    cwd: str
    env: dict[str, str] = field(default_factory=dict)
    request_timeout_seconds: float = 10.0
    turn_timeout_seconds: float = 60.0
    shutdown_timeout_seconds: float = 1.0
    max_stderr_lines: int = 200

    def __post_init__(self) -> None:
        if (
            not self.launch_args
            or any(
                not isinstance(item, str) or not item.strip()
                for item in self.launch_args
            )
        ):
            raise ValueError("DSH Runtime launch arguments are invalid")
        if not isinstance(self.cwd, str) or not self.cwd.strip():
            raise ValueError("DSH Runtime cwd is invalid")
        for name, value in (
            ("request_timeout_seconds", self.request_timeout_seconds),
            ("turn_timeout_seconds", self.turn_timeout_seconds),
            ("shutdown_timeout_seconds", self.shutdown_timeout_seconds),
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value <= 0
            ):
                raise ValueError(f"DSH Runtime {name} is invalid")
        if (
            not isinstance(self.max_stderr_lines, int)
            or isinstance(self.max_stderr_lines, bool)
            or not 10 <= self.max_stderr_lines <= 2000
        ):
            raise ValueError("DSH Runtime stderr limit is invalid")


@dataclass(frozen=True, slots=True)
class DshRuntimeNotification:
    method: str
    payload: JsonObject


@dataclass(frozen=True, slots=True)
class DshRuntimeIncomingRequest:
    request_id: str | int
    method: str
    payload: JsonObject


@dataclass(frozen=True, slots=True)
class DshRunResult:
    session_id: str
    final_response: str
    finish_reason: str | None
    events: tuple[JsonObject, ...]
    notifications: tuple[DshRuntimeNotification, ...]


class DshRuntimeAdapter:
    """
    Async newline-delimited JSON-RPC client for DeepSeek Harness.

    DSH owns only subprocess execution. The AI Reliability durable Session
    store/service remain outside this adapter and authoritative.
    """

    def __init__(self, config: DshRuntimeConfig) -> None:
        if not isinstance(config, DshRuntimeConfig):
            raise TypeError("DSH Runtime config is invalid")
        self.config = config
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._notifications: asyncio.Queue[
            DshRuntimeNotification | BaseException
        ] = asyncio.Queue()
        self._incoming_requests: asyncio.Queue[
            DshRuntimeIncomingRequest | BaseException
        ] = asyncio.Queue()
        self._stderr_lines: deque[str] = deque(
            maxlen=config.max_stderr_lines
        )
        self._write_lock = asyncio.Lock()
        self._closing = False

    async def __aenter__(self) -> "DshRuntimeAdapter":
        await self.start()
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.close()

    @property
    def running(self) -> bool:
        process = self._process
        return process is not None and process.returncode is None

    async def start(self) -> None:
        if self.running:
            return
        if self._process is not None:
            raise DshRuntimeClosedError(
                "DSH Runtime adapter cannot restart a closed process"
            )

        environment = os.environ.copy()
        environment.update(self.config.env)
        self._process = await asyncio.create_subprocess_exec(
            *self.config.launch_args,
            cwd=str(Path(self.config.cwd).resolve()),
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(
            self._reader_loop(),
            name="dsh-jsonrpc-reader",
        )
        self._stderr_task = asyncio.create_task(
            self._stderr_loop(),
            name="dsh-jsonrpc-stderr",
        )

    async def initialize(
        self,
        *,
        cwd: str,
        provider: str,
        model: str,
        max_tokens: int | None = None,
    ) -> JsonObject:
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError("DSH initialize cwd is invalid")
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError("DSH initialize provider is invalid")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("DSH initialize model is invalid")
        if (
            max_tokens is not None
            and (
                not isinstance(max_tokens, int)
                or isinstance(max_tokens, bool)
                or max_tokens <= 0
            )
        ):
            raise ValueError("DSH initialize max_tokens is invalid")

        payload: JsonObject = {
            "cwd": str(Path(cwd).resolve()),
            "provider": provider,
            "model": model,
        }
        if max_tokens is not None:
            payload["maxTokens"] = max_tokens

        result = await self.request("initialize", payload)
        if not isinstance(result, dict):
            raise DshRuntimeProtocolError(
                "DSH initialize response must be an object"
            )
        return result

    async def run_turn(
        self,
        input_text: str,
        *,
        session_id: str,
    ) -> DshRunResult:
        if not isinstance(input_text, str) or not input_text.strip():
            raise ValueError("DSH turn input is invalid")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("DSH turn session_id is invalid")

        prompt_response = await self.request(
            "session/prompt",
            {
                "sessionId": session_id,
                "contentBlocks": [
                    {"type": "text", "text": input_text}
                ],
            },
        )
        if not isinstance(prompt_response, dict):
            raise DshRuntimeProtocolError(
                "DSH session/prompt response must be an object"
            )
        message_id = prompt_response.get("messageId")
        if not isinstance(message_id, str) or not message_id:
            raise DshRuntimeProtocolError(
                "DSH session/prompt response requires messageId"
            )

        return await asyncio.wait_for(
            self._collect_turn(
                session_id=session_id,
                message_id=message_id,
            ),
            timeout=self.config.turn_timeout_seconds,
        )

    async def request(
        self,
        method: str,
        params: JsonObject | None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        if not isinstance(method, str) or not method.strip():
            raise ValueError("DSH JSON-RPC method is invalid")
        await self.start()

        request_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future

        message: JsonObject = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params

        try:
            await self._write_message(message)
        except BaseException:
            self._pending.pop(request_id, None)
            raise

        timeout = (
            self.config.request_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise TimeoutError(
                f"{method} timed out waiting for DSH Runtime"
                f"{self._diagnostic_suffix()}"
            ) from exc

    async def respond(
        self,
        request_id: str | int,
        result: Any,
    ) -> None:
        await self._write_message(
            {"jsonrpc": "2.0", "id": request_id, "result": result}
        )

    async def respond_error(
        self,
        request_id: str | int,
        *,
        code: int,
        message: str,
        data: Any = None,
    ) -> None:
        error: JsonObject = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        await self._write_message(
            {"jsonrpc": "2.0", "id": request_id, "error": error}
        )

    async def next_incoming_request(self) -> DshRuntimeIncomingRequest:
        item = await self._incoming_requests.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self) -> None:
        process = self._process
        if process is None:
            return

        self._closing = True
        if process.returncode is None:
            try:
                await self.request(
                    "shutdown",
                    None,
                    timeout_seconds=self.config.shutdown_timeout_seconds,
                )
            except Exception:
                pass

        if process.stdin is not None:
            try:
                process.stdin.close()
                await process.stdin.wait_closed()
            except Exception:
                pass

        if process.returncode is None:
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=self.config.shutdown_timeout_seconds,
                )
            except TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(
                        process.wait(),
                        timeout=self.config.shutdown_timeout_seconds,
                    )
                except TimeoutError:
                    process.kill()
                    await process.wait()

        for task in (self._reader_task, self._stderr_task):
            if task is None:
                continue
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        self._fail_waiters(DshRuntimeClosedError("DSH Runtime closed"))

    async def _collect_turn(
        self,
        *,
        session_id: str,
        message_id: str,
    ) -> DshRunResult:
        received = False
        events: list[JsonObject] = []
        notifications: list[DshRuntimeNotification] = []

        while True:
            item = await self._notifications.get()
            if isinstance(item, BaseException):
                raise item

            if not received:
                if not self._is_inbox_receipt(
                    item,
                    session_id=session_id,
                    message_id=message_id,
                ):
                    continue
                received = True

            notifications.append(item)

            if (
                item.method == "session.event"
                and item.payload.get("sessionId") == session_id
            ):
                event = item.payload.get("event")
                if isinstance(event, dict):
                    events.append(event)

            if (
                item.method == "session.status"
                and item.payload.get("sessionId") == session_id
                and item.payload.get("status") == "idle"
            ):
                break

        return DshRunResult(
            session_id=session_id,
            final_response=self.final_response(events),
            finish_reason=self.finish_reason(events),
            events=tuple(events),
            notifications=tuple(notifications),
        )

    async def _write_message(self, message: JsonObject) -> None:
        process = self._process
        if (
            process is None
            or process.stdin is None
            or process.returncode is not None
        ):
            raise DshRuntimeClosedError(
                "DSH Runtime is not running"
                f"{self._diagnostic_suffix()}"
            )

        payload = (
            json.dumps(message, separators=(",", ":")) + "\n"
        ).encode("utf-8")

        async with self._write_lock:
            process.stdin.write(payload)
            try:
                await process.stdin.drain()
            except Exception as exc:
                raise DshRuntimeClosedError(
                    "Failed to write to DSH Runtime"
                    f"{self._diagnostic_suffix()}"
                ) from exc

    async def _reader_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return

        try:
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    break
                if not raw.strip():
                    continue

                try:
                    message = json.loads(
                        raw.decode("utf-8", errors="strict")
                    )
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise DshRuntimeProtocolError(
                        "DSH stdout contained a non-JSON-RPC line"
                    ) from exc

                await self._handle_message(message)

        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._fail_waiters(exc)
            await self._notifications.put(exc)
            await self._incoming_requests.put(exc)
        finally:
            if not self._closing:
                error = DshRuntimeClosedError(
                    "DSH Runtime stdout closed"
                    f"{self._diagnostic_suffix()}"
                )
                self._fail_waiters(error)
                await self._notifications.put(error)
                await self._incoming_requests.put(error)

    async def _stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return

        while True:
            raw = await process.stderr.readline()
            if not raw:
                break
            self._stderr_lines.append(
                raw.decode("utf-8", errors="replace").rstrip()
            )

    async def _handle_message(self, message: Any) -> None:
        if not isinstance(message, dict):
            raise DshRuntimeProtocolError(
                "DSH JSON-RPC message must be an object"
            )

        message_id = message.get("id")
        method = message.get("method")

        if (
            isinstance(message_id, (str, int))
            and isinstance(method, str)
        ):
            params = message.get("params")
            await self._incoming_requests.put(
                DshRuntimeIncomingRequest(
                    request_id=message_id,
                    method=method,
                    payload=params if isinstance(params, dict) else {},
                )
            )
            return

        if isinstance(message_id, (str, int)):
            future = self._pending.pop(str(message_id), None)
            if future is None:
                return

            error = message.get("error")
            if isinstance(error, dict):
                if not future.done():
                    future.set_exception(
                        DshRuntimeProtocolError(
                            "DSH JSON-RPC error "
                            f"{error.get('code')}: "
                            f"{error.get('message', 'unknown error')}"
                        )
                    )
            elif not future.done():
                future.set_result(message.get("result"))
            return

        if isinstance(method, str):
            params = message.get("params")
            await self._notifications.put(
                DshRuntimeNotification(
                    method=method,
                    payload=params if isinstance(params, dict) else {},
                )
            )
            return

        raise DshRuntimeProtocolError(
            "DSH JSON-RPC message has no response id or method"
        )

    def _fail_waiters(self, exc: BaseException) -> None:
        waiters = list(self._pending.values())
        self._pending.clear()
        for future in waiters:
            if not future.done():
                future.set_exception(exc)

    def _diagnostic_suffix(self) -> str:
        process = self._process
        parts: list[str] = []
        if process is not None and process.returncode is not None:
            parts.append(f"exit_code={process.returncode}")
        if self._stderr_lines:
            parts.append(
                "stderr_tail=" + " | ".join(self._stderr_lines)
            )
        if not parts:
            return ""
        return " [" + "; ".join(parts) + "]"

    @staticmethod
    def _is_inbox_receipt(
        notification: DshRuntimeNotification,
        *,
        session_id: str,
        message_id: str,
    ) -> bool:
        if (
            notification.method != "session.event"
            or notification.payload.get("sessionId") != session_id
        ):
            return False

        event = notification.payload.get("event")
        if (
            not isinstance(event, dict)
            or event.get("type") != "agent/inbox/spliced"
        ):
            return False

        data = event.get("data")
        inserted = (
            data.get("inserted")
            if isinstance(data, dict)
            else None
        )
        return (
            isinstance(inserted, list)
            and any(
                isinstance(message, dict)
                and message.get("id") == message_id
                for message in inserted
            )
        )

    @staticmethod
    def final_response(events: list[JsonObject]) -> str:
        for event in reversed(events):
            if event.get("type") != "assistant/message":
                continue

            data = event.get("data")
            if not isinstance(data, dict):
                continue

            message = data.get("message")
            owner = message if isinstance(message, dict) else data
            content = owner.get("content")
            if not isinstance(content, list):
                continue

            parts: list[str] = []
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                ):
                    parts.append(str(block.get("text") or ""))
            return "".join(parts)
        return ""

    @staticmethod
    def finish_reason(events: list[JsonObject]) -> str | None:
        for event in reversed(events):
            if event.get("type") != "turn/end":
                continue

            data = event.get("data")
            reason = (
                data.get("reason")
                if isinstance(data, dict)
                else None
            )
            kind = (
                reason.get("kind")
                if isinstance(reason, dict)
                else None
            )
            if not isinstance(kind, str):
                raise DshRuntimeProtocolError(
                    "DSH turn/end requires data.reason.kind"
                )
            return kind
        return None


__all__ = [
    "DshRunResult",
    "DshRuntimeAdapter",
    "DshRuntimeClosedError",
    "DshRuntimeConfig",
    "DshRuntimeError",
    "DshRuntimeIncomingRequest",
    "DshRuntimeNotification",
    "DshRuntimeProtocolError",
]
