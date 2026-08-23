"""
Phase 6.2.1.8
MCP Federation Multi Cluster Failover + Disaster Recovery Routing.

Provides a control-plane abstraction for selecting backup MCP providers
when the primary execution environment becomes unavailable.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RecoveryTarget:
    provider_id: str
    cluster_id: str
    region: str
    priority: int = 100
    capabilities: List[str] = field(default_factory=list)


@dataclass
class FailoverDecision:
    failed_provider: str
    selected_provider: Optional[str]
    reason: str
    recovery_mode: str


class DisasterRecoveryRouter:
    """Selects alternative MCP execution targets during failures."""

    def __init__(self, recovery_targets: List[RecoveryTarget]):
        self.recovery_targets = recovery_targets

    def select_backup(
        self,
        capability: str,
        excluded_provider: str,
    ) -> Optional[RecoveryTarget]:
        candidates = [
            target
            for target in self.recovery_targets
            if target.provider_id != excluded_provider
            and capability in target.capabilities
        ]

        if not candidates:
            return None

        return sorted(
            candidates,
            key=lambda item: item.priority,
        )[0]

    def failover(
        self,
        failed_provider: str,
        capability: str,
    ) -> FailoverDecision:
        target = self.select_backup(capability, failed_provider)

        if target is None:
            return FailoverDecision(
                failed_provider=failed_provider,
                selected_provider=None,
                reason="no recovery target available",
                recovery_mode="blocked",
            )

        return FailoverDecision(
            failed_provider=failed_provider,
            selected_provider=target.provider_id,
            reason=f"route switched to {target.cluster_id}",
            recovery_mode="cross-cluster-failover",
        )
