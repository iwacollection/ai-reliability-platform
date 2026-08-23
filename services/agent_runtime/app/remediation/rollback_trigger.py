from dataclasses import dataclass


@dataclass
class RollbackDecision:
    rollback: bool
    reason: str


class RollbackTrigger:
    def evaluate(self, verification_status: str) -> RollbackDecision:
        if verification_status != "passed":
            return RollbackDecision(
                rollback=True,
                reason="verification_failed",
            )

        return RollbackDecision(
            rollback=False,
            reason="verification_passed",
        )
