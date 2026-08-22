from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator
import asyncio


@dataclass
class AgentEvent:
    """Unified runtime event consumed by Web Console SSE layer."""

    event_type: str
    incident_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class AgentEventBus:
    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[AgentEvent]]] = {}

    def publish(self, event: AgentEvent) -> None:
        for queue in self._queues.get(event.incident_id, []):
            queue.put_nowait(event)

    async def subscribe(self, incident_id: str) -> AsyncIterator[AgentEvent]:
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._queues.setdefault(incident_id, []).append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._queues[incident_id].remove(queue)


agent_event_bus = AgentEventBus()
