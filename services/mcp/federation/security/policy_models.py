"""Policy models for dynamic MCP authorization runtime."""

from dataclasses import dataclass, field
from enum import Enum


class PolicyDecisionType(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass
class PolicySubject:
    principal: str
    roles: list[str] = field(default_factory=list)


@dataclass
class PolicyContext:
    tenant: str
    environment: str
    cluster: str | None = None
    namespace: str | None = None
    risk_level: str = "low"


@dataclass
class PolicyRequest:
    subject: PolicySubject
    capability: str
    action: str
    context: PolicyContext


@dataclass
class PolicyDecision:
    decision: PolicyDecisionType
    reason: str
    policy_name: str | None = None
