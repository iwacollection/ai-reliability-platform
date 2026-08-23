"""Secret management models for MCP Federation credential isolation."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SecretAccessMode(str, Enum):
    READ = "read"
    ISSUE_SHORT_LIVED = "issue_short_lived"


@dataclass(frozen=True)
class SecretRequest:
    principal: str
    tenant_id: str
    resource: str
    purpose: str
    mode: SecretAccessMode


@dataclass(frozen=True)
class CredentialLease:
    credential_id: str
    expires_at: datetime
    tenant_id: str
    resource: str
