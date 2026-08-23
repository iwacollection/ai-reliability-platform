"""MCP federation circuit breaker.

Protects Agent Runtime from repeatedly calling unhealthy MCP providers.
"""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class CircuitState:
    provider_id: str
    state: str = "closed"
    failures: int = 0
    updated_at: str = ""


class MCPCircuitBreaker:
    def __init__(self, failure_threshold: int = 3):
        self.failure_threshold = failure_threshold
        self.states: dict[str, CircuitState] = {}

    def record_success(self, provider_id: str):
        self.states[provider_id] = CircuitState(
            provider_id=provider_id,
            state="closed",
            failures=0,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def record_failure(self, provider_id: str):
        current = self.states.get(provider_id, CircuitState(provider_id))
        current.failures += 1
        current.state = "open" if current.failures >= self.failure_threshold else "closed"
        current.updated_at = datetime.now(timezone.utc).isoformat()
        self.states[provider_id] = current

    def allow(self, provider_id: str) -> bool:
        return self.states.get(provider_id, CircuitState(provider_id)).state != "open"
