from dataclasses import dataclass


@dataclass
class RollbackResult:
    status: str
    reason: str


class RollbackEngine:
    def rollback(self, reason: str) -> RollbackResult:
        return RollbackResult(
            status="rollback_started",
            reason=reason,
        )
