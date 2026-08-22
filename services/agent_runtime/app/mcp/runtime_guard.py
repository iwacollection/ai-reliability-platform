import time
from dataclasses import dataclass


@dataclass
class RuntimeLimits:
    timeout_seconds: int = 30
    max_calls: int = 10


class ToolRuntimeGuard:
    def __init__(self, limits: RuntimeLimits | None = None):
        self.limits = limits or RuntimeLimits()
        self.calls = 0

    def execute(self, handler, *args, **kwargs):
        if self.calls >= self.limits.max_calls:
            raise RuntimeError("tool call limit exceeded")

        self.calls += 1
        start = time.time()
        result = handler(*args, **kwargs)

        if time.time() - start > self.limits.timeout_seconds:
            raise TimeoutError("tool runtime timeout")

        return result
