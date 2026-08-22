"""Approval and remediation audit trail."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class ApprovalAudit:
    incident_id: str
    action: str
    approved_by: str
    decision: str
    created_at: str

    @classmethod
    def create(cls, incident_id, action, approved_by, decision):
        return cls(
            incident_id,
            action,
            approved_by,
            decision,
            datetime.now(timezone.utc).isoformat(),
        )
