from typing import Any
from uuid import UUID

from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)

from services.agent_runtime.app.incident.state import (
    IncidentState,
)

from services.agent_runtime.app.incident.store import (
    IncidentStore,
)

from services.agent_runtime.app.verification.models import (
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
)

from services.agent_runtime.app.verification.service import (
    VerificationService,
)

from services.agent_runtime.app.verification.store import (
    VerificationClaimResult,
)


class VerificationIncidentSyncError(
    RuntimeError
):
    """
    Verification was persisted, but the linked
    Incident could not be synchronized.

    The caller can retry through reconcile().
    """

    def __init__(
        self,
        message: str,
        verification_id: UUID,
        incident_id: UUID,
    ) -> None:
        super().__init__(
            message
        )

        self.verification_id = (
            verification_id
        )

        self.incident_id = incident_id


class VerificationRuntime:
    """
    Coordinate Verification and Incident state.

    Safety order:

    1. Persist verification claim and evidence.
    2. Update the linked Incident.
    3. Reconcile later if Incident update fails.

    Incident must never become RESOLVED before
    PASSED verification evidence is persisted.
    """

    def __init__(
        self,
        verification_service: (
            VerificationService | None
        ) = None,
        incident_store: (
            IncidentStore | None
        ) = None,
    ) -> None:
        self.verification_service = (
            verification_service
            or VerificationService()
        )

        self.incident_store = (
            incident_store
            or IncidentStore()
        )

    async def create(
        self,
        incident_id: UUID | str,
        action: str | None = None,
        target: str | None = None,
        attempt: int = 1,
        metadata: dict[str, Any] | None = None,
        action_execution_id: UUID | str | None = None,
    ) -> VerificationResult:
        """
        Create verification for a HEALING Incident.

        This method remains available for legacy and explicitly manual
        verification. Automatically triggered workflows must use claim().
        """

        incident = await self._require_incident(
            incident_id
        )

        if (
            incident.status
            != IncidentStatus.HEALING
        ):
            raise ValueError(
                "Verification can only be created "
                "for a HEALING Incident"
            )

        return await (
            self.verification_service
            .create_verification(
                incident_id=incident.id,
                action=action,
                target=target,
                attempt=attempt,
                metadata=metadata,
                action_execution_id=(
                    action_execution_id
                ),
            )
        )

    async def claim(
        self,
        *,
        action_execution_id: UUID | str,
        incident_id: UUID | str,
        action: str | None = None,
        target: str | None = None,
        attempt: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> VerificationClaimResult:
        """
        Atomically claim the automatic verification for one Action Execution.

        Only the caller receiving created=True may start evidence probes. A
        replay receives the persisted VerificationResult without changing its
        state or running probes again.
        """

        existing = await (
            self.verification_service
            .get_by_action_execution(
                action_execution_id
            )
        )

        if existing is not None:
            return await (
                self.verification_service
                .claim_verification(
                    action_execution_id=(
                        action_execution_id
                    ),
                    incident_id=incident_id,
                    action=action,
                    target=target,
                    attempt=attempt,
                    metadata=metadata,
                )
            )

        incident = await self._require_incident(
            incident_id
        )

        if (
            incident.status
            != IncidentStatus.HEALING
        ):
            raise ValueError(
                "Verification can only be claimed "
                "for a HEALING Incident"
            )

        return await (
            self.verification_service
            .claim_verification(
                action_execution_id=(
                    action_execution_id
                ),
                incident_id=incident.id,
                action=action,
                target=target,
                attempt=attempt,
                metadata=metadata,
            )
        )

    async def start(
        self,
        verification_id: UUID | str,
    ) -> VerificationResult:
        """
        Start verification while the linked
        Incident is still HEALING.
        """

        verification = (
            await self._require_verification(
                verification_id
            )
        )

        incident = await self._require_incident(
            verification.incident_id
        )

        if (
            incident.status
            != IncidentStatus.HEALING
        ):
            raise ValueError(
                "Verification can only run while "
                "the Incident is HEALING"
            )

        return await (
            self.verification_service.start(
                verification.id
            )
        )

    async def complete(
        self,
        verification_id: UUID | str,
        status: VerificationStatus,
        checks: list[VerificationCheck],
        summary: str = "",
    ) -> tuple[
        VerificationResult,
        IncidentState,
    ]:
        """
        Persist a terminal Verification result,
        then synchronize the Incident.

        If Incident synchronization fails, the
        persisted Verification result remains
        available for reconcile().
        """

        verification = await (
            self.verification_service.complete(
                verification_id=(
                    verification_id
                ),
                status=status,
                checks=checks,
                summary=summary,
            )
        )

        incident = await (
            self._synchronize_incident(
                verification
            )
        )

        return (
            verification,
            incident,
        )

    async def reconcile(
        self,
        verification_id: UUID | str,
    ) -> tuple[
        VerificationResult,
        IncidentState,
    ]:
        """
        Retry Incident synchronization from an
        already persisted terminal Verification.
        """

        verification = (
            await self._require_verification(
                verification_id
            )
        )

        if not verification.is_terminal:
            raise ValueError(
                "Only terminal Verification "
                "can be reconciled"
            )

        incident = await (
            self._synchronize_incident(
                verification
            )
        )

        return (
            verification,
            incident,
        )

    async def get(
        self,
        verification_id: UUID | str,
    ) -> VerificationResult | None:
        return await (
            self.verification_service.get(
                verification_id
            )
        )

    async def get_by_action_execution(
        self,
        action_execution_id: UUID | str,
    ) -> VerificationResult | None:
        return await (
            self.verification_service
            .get_by_action_execution(
                action_execution_id
            )
        )

    async def get_incident(
        self,
        incident_id: UUID | str,
    ) -> IncidentState | None:
        """Read the Incident linked to a replayed Verification claim."""

        return await self.incident_store.get(
            incident_id
        )

    async def _synchronize_incident(
        self,
        verification: VerificationResult,
    ) -> IncidentState:
        target_status, reason = (
            self._incident_transition(
                verification
            )
        )

        incident = await self._require_incident(
            verification.incident_id
        )

        if (
            incident.status
            == target_status
            and incident.reason == reason
        ):
            return incident

        if (
            target_status
            != IncidentStatus.HEALING
            and incident.status
            == target_status
        ):
            return incident

        if (
            incident.status
            != IncidentStatus.HEALING
        ):
            raise VerificationIncidentSyncError(
                message=(
                    "Verification is persisted, "
                    "but Incident is no longer "
                    "in HEALING state"
                ),
                verification_id=verification.id,
                incident_id=incident.id,
            )

        incident.update(
            status=target_status,
            reason=reason,
        )

        try:
            return await self.incident_store.update(
                incident,
                expected_status=(
                    IncidentStatus.HEALING
                ),
            )

        except Exception as error:
            raise VerificationIncidentSyncError(
                message=(
                    "Verification is persisted, "
                    "but Incident synchronization "
                    "failed; call reconcile()"
                ),
                verification_id=verification.id,
                incident_id=incident.id,
            ) from error

    @staticmethod
    def _incident_transition(
        verification: VerificationResult,
    ) -> tuple[
        IncidentStatus,
        str,
    ]:
        summary = (
            verification.summary.strip()
        )

        if (
            verification.status
            == VerificationStatus.PASSED
        ):
            reason = (
                "Verification succeeded"
            )

            if summary:
                reason = (
                    f"{reason}: {summary}"
                )

            return (
                IncidentStatus.RESOLVED,
                reason,
            )

        if (
            verification.status
            == VerificationStatus.FAILED
        ):
            reason = "Verification failed"

            if summary:
                reason = (
                    f"{reason}: {summary}"
                )

            return (
                IncidentStatus.FAILED,
                reason,
            )

        if (
            verification.status
            == VerificationStatus.TIMED_OUT
        ):
            reason = "Verification timed out"

            if summary:
                reason = (
                    f"{reason}: {summary}"
                )

            return (
                IncidentStatus.FAILED,
                reason,
            )

        if (
            verification.status
            == VerificationStatus.INCONCLUSIVE
        ):
            reason = (
                "Verification inconclusive; "
                "awaiting more evidence"
            )

            if summary:
                reason = (
                    f"{reason}: {summary}"
                )

            return (
                IncidentStatus.HEALING,
                reason,
            )

        raise ValueError(
            "Incident synchronization requires "
            "a terminal Verification status"
        )

    async def _require_verification(
        self,
        verification_id: UUID | str,
    ) -> VerificationResult:
        verification = await (
            self.verification_service.get(
                verification_id
            )
        )

        if verification is None:
            raise ValueError(
                "Verification result not found"
            )

        return verification

    async def _require_incident(
        self,
        incident_id: UUID | str,
    ) -> IncidentState:
        incident = await self.incident_store.get(
            incident_id
        )

        if incident is None:
            raise ValueError(
                "Incident not found"
            )

        return incident
