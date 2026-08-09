from typing import Any
from uuid import UUID

from services.agent_runtime.app.verification.models import (
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
)

from services.agent_runtime.app.verification.store import (
    VerificationClaimResult,
    VerificationStore,
)


class VerificationService:
    """
    Verification lifecycle service.

    Responsibilities:
    - create manual or legacy verification results
    - atomically claim one verification per Action Execution
    - start verification
    - complete verification
    - query persisted verification results

    Incident state transitions are intentionally
    handled by a higher-level runtime component.
    """

    def __init__(
        self,
        store: VerificationStore | None = None,
    ) -> None:
        self.store = (
            store
            or VerificationStore()
        )

    async def create_verification(
        self,
        incident_id: UUID,
        action: str | None = None,
        target: str | None = None,
        attempt: int = 1,
        metadata: dict[str, Any] | None = None,
        action_execution_id: UUID | str | None = None,
    ) -> VerificationResult:
        """
        Create a new PENDING verification result.

        Existing callers remain compatible. Automatically triggered workflows
        should use claim_verification() so request replay cannot create a
        second result for the same Action Execution.
        """

        result = VerificationResult(
            incident_id=incident_id,
            action_execution_id=(
                action_execution_id
            ),
            action=action,
            target=target,
            attempt=attempt,
            metadata=dict(
                metadata
                or {}
            ),
        )

        return await self.store.save(
            result
        )

    async def claim_verification(
        self,
        *,
        action_execution_id: UUID | str,
        incident_id: UUID,
        action: str | None = None,
        target: str | None = None,
        attempt: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> VerificationClaimResult:
        """
        Atomically claim the only automatic verification for one execution.

        Only a caller receiving created=True may start probes. A replay with
        the same immutable request returns the persisted VerificationResult.
        A different request for the same execution raises
        VerificationConflictError from the Store.
        """

        result = VerificationResult(
            incident_id=incident_id,
            action_execution_id=(
                action_execution_id
            ),
            action=action,
            target=target,
            attempt=attempt,
            metadata=dict(
                metadata
                or {}
            ),
        )

        return await self.store.claim(
            result
        )

    async def start(
        self,
        verification_id: UUID | str,
    ) -> VerificationResult:
        """
        Move verification from PENDING to RUNNING.

        A repeated start for an already RUNNING
        result is treated as idempotent.
        """

        result = await self._require(
            verification_id
        )

        if (
            result.status
            == VerificationStatus.RUNNING
        ):
            return result

        if (
            result.status
            != VerificationStatus.PENDING
        ):
            raise ValueError(
                "Only PENDING verification "
                "can be started"
            )

        result.start()

        return await self.store.update(
            result,
            expected_status=(
                VerificationStatus.PENDING
            ),
        )

    async def complete(
        self,
        verification_id: UUID | str,
        status: VerificationStatus,
        checks: list[VerificationCheck],
        summary: str = "",
    ) -> VerificationResult:
        """
        Complete a RUNNING verification.

        Valid terminal statuses:
        - PASSED
        - FAILED
        - INCONCLUSIVE
        - TIMED_OUT
        """

        result = await self._require(
            verification_id
        )

        if (
            result.status
            != VerificationStatus.RUNNING
        ):
            raise ValueError(
                "Only RUNNING verification "
                "can be completed"
            )

        result.complete(
            status=status,
            checks=checks,
            summary=summary,
        )

        return await self.store.update(
            result,
            expected_status=(
                VerificationStatus.RUNNING
            ),
        )

    async def get(
        self,
        verification_id: UUID | str,
    ) -> VerificationResult | None:
        """
        Get one verification result.
        """

        return await self.store.get(
            verification_id
        )

    async def get_by_action_execution(
        self,
        action_execution_id: UUID | str,
    ) -> VerificationResult | None:
        """Get the unique Verification linked to one Action Execution."""

        return await (
            self.store.get_by_action_execution(
                action_execution_id
            )
        )

    async def list_by_incident(
        self,
        incident_id: UUID | str,
    ) -> list[VerificationResult]:
        """
        List verification attempts for one Incident.
        """

        return await (
            self.store.list_by_incident(
                incident_id
            )
        )

    async def list_all(
        self,
    ) -> list[VerificationResult]:
        """
        List all verification results.
        """

        return await self.store.list_all()

    async def _require(
        self,
        verification_id: UUID | str,
    ) -> VerificationResult:
        result = await self.store.get(
            verification_id
        )

        if result is None:
            raise ValueError(
                "Verification result not found"
            )

        return result
