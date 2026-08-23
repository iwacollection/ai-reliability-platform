from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class VerificationEvent:
    incident_id: str
    check: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


VERIFICATION_STARTED = "verification_started"
VERIFICATION_CHECKING = "verification_checking"
VERIFICATION_PASSED = "verification_passed"
VERIFICATION_FAILED = "verification_failed"
