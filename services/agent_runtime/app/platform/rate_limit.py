from dataclasses import dataclass
from time import time


@dataclass
class RateLimitPolicy:
    requests_per_minute: int = 60


class AgentRateLimiter:
    def __init__(self, policy: RateLimitPolicy | None = None):
        self.policy = policy or RateLimitPolicy()
        self.requests: dict[str, list[float]] = {}

    def allow(self, identity: str) -> bool:
        now = time()
        history = [t for t in self.requests.get(identity, []) if now - t < 60]
        if len(history) >= self.policy.requests_per_minute:
            return False
        history.append(now)
        self.requests[identity] = history
        return True
