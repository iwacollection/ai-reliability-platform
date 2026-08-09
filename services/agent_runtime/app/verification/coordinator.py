from collections import Counter
from typing import Any
from uuid import UUID

from services.agent_runtime.app.action.models import (
    ActionPlan,
)
from services.agent_runtime.app.incident.state import (
    IncidentState,
)
from services.agent_runtime.app.runtime.verification_runtime import (
    VerificationRuntime,
)
from services.agent_runtime.app.verification.collector import (
    VerificationEvidenceCollector,
)
from services.agent_runtime.app.verification.models import (
    VerificationCheck,
    VerificationResult,
    VerificationSource,
    VerificationStatus,
)
from services.agent_runtime.app.verification.profiles import (
    VerificationProfile,
    VerificationProfileFactory,
)


class VerificationCoordinator:
    """
    Run one evidence-backed verification attempt.

    Flow:

    ActionPlan
        -> VerificationProfile
        -> VerificationRuntime claim/create
        -> VerificationEvidenceCollector
        -> terminal decision
        -> VerificationRuntime.complete

    An action_execution_id enables the exactly-once path. Only the caller that
    creates the durable claim may start probes. Legacy or explicitly manual
    calls without an Action Execution keep the original create behavior.

    This component is intentionally independent from ActionRuntime. Creating
    it does not trigger probes or change an Incident. Only run() performs the
    workflow.
    """

    def __init__(
        self,
        profile_factory: VerificationProfileFactory,
        collector: VerificationEvidenceCollector,
        verification_runtime: VerificationRuntime,
    ) -> None:
        self.profile_factory = profile_factory
        self.collector = collector
        self.verification_runtime = (
            verification_runtime
        )

    async def run(
        self,
        *,
        incident_id: UUID | str,
        plan: ActionPlan,
        namespace: str | None = None,
        cluster: str | None = None,
        attempt: int = 1,
        context=None,
        metadata: dict[str, Any] | None = None,
        action_execution_id: UUID | str | None = None,
    ) -> tuple[
        VerificationResult,
        IncidentState,
    ]:
        """
        Execute or safely replay one verification attempt.

        The linked Incident must already be HEALING for a new claim. An
        existing claim may be replayed after the Incident reaches a terminal
        state. Unsupported actions fail before a VerificationResult is
        created.
        """

        profile = self.profile_factory.create(
            plan,
            namespace=namespace,
            cluster=cluster,
        )

        verification_metadata = dict(
            metadata or {}
        )
        resolved_execution_id = (
            self._resolve_action_execution_id(
                explicit=action_execution_id,
                metadata=verification_metadata,
            )
        )

        verification_metadata.update(
            {
                "profile": profile.name,
                "namespace": profile.namespace,
                "cluster": profile.cluster,
                "required_probes": [
                    probe.name
                    for probe in profile.probes
                    if probe.required
                ],
                "optional_probes": [
                    probe.name
                    for probe in profile.probes
                    if not probe.required
                ],
            }
        )

        if resolved_execution_id is not None:
            verification_metadata[
                "action_execution_id"
            ] = str(
                resolved_execution_id
            )

            claim = await (
                self.verification_runtime.claim(
                    action_execution_id=(
                        resolved_execution_id
                    ),
                    incident_id=incident_id,
                    action=profile.action.value,
                    target=profile.target,
                    attempt=attempt,
                    metadata=(
                        verification_metadata
                    ),
                )
            )
            verification = claim.verification

            if not claim.created:
                return await self._return_replayed(
                    verification
                )

        else:
            verification = (
                await self.verification_runtime.create(
                    incident_id=incident_id,
                    action=profile.action.value,
                    target=profile.target,
                    attempt=attempt,
                    metadata=(
                        verification_metadata
                    ),
                )
            )

        verification = (
            await self.verification_runtime.start(
                verification.id
            )
        )

        try:
            collected_checks = (
                await self.collector.collect(
                    list(profile.probes),
                    context=context,
                )
            )

        except Exception as exc:
            checks = [
                self._collection_error_check(
                    exc
                )
            ]

        else:
            checks = self._enforce_probe_integrity(
                profile=profile,
                checks=collected_checks,
            )

        status, summary = self.decide(
            checks
        )

        return await self.verification_runtime.complete(
            verification_id=verification.id,
            status=status,
            checks=checks,
            summary=summary,
        )

    async def _return_replayed(
        self,
        verification: VerificationResult,
    ) -> tuple[
        VerificationResult,
        IncidentState,
    ]:
        """Return a persisted claim without running evidence probes again."""

        if verification.is_terminal:
            return await (
                self.verification_runtime.reconcile(
                    verification.id
                )
            )

        incident = await (
            self.verification_runtime.get_incident(
                verification.incident_id
            )
        )

        if incident is None:
            raise ValueError(
                "Incident not found for replayed "
                "Verification"
            )

        return (
            verification,
            incident,
        )

    @staticmethod
    def _resolve_action_execution_id(
        *,
        explicit: UUID | str | None,
        metadata: dict[str, Any],
    ) -> UUID | None:
        metadata_value = metadata.get(
            "action_execution_id"
        )

        explicit_id = (
            VerificationCoordinator
            ._parse_action_execution_id(
                explicit,
                source="argument",
            )
            if explicit is not None
            else None
        )
        metadata_id = (
            VerificationCoordinator
            ._parse_action_execution_id(
                metadata_value,
                source="metadata",
            )
            if metadata_value is not None
            else None
        )

        if (
            explicit_id is not None
            and metadata_id is not None
            and explicit_id != metadata_id
        ):
            raise ValueError(
                "Action Execution id conflicts "
                "with verification metadata"
            )

        return explicit_id or metadata_id

    @staticmethod
    def _parse_action_execution_id(
        value: UUID | str,
        *,
        source: str,
    ) -> UUID:
        try:
            return UUID(
                str(value)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Invalid Action Execution id "
                f"in {source}"
            ) from exc

    @staticmethod
    def decide(
        checks: list[VerificationCheck],
    ) -> tuple[
        VerificationStatus,
        str,
    ]:
        """
        Convert required check states into one terminal status.

        Precedence:
        - any explicit required failure -> FAILED
        - otherwise any required unknown -> INCONCLUSIVE
        - otherwise every required check passed -> PASSED

        Optional checks are persisted but never decide terminal success.
        """

        required_checks = [
            check
            for check in checks
            if check.required
        ]

        if not required_checks:
            return (
                VerificationStatus.INCONCLUSIVE,
                "Verification has no required checks",
            )

        failed = [
            check.name
            for check in required_checks
            if check.passed is False
        ]

        if failed:
            return (
                VerificationStatus.FAILED,
                "Required checks failed: "
                + ", ".join(failed),
            )

        inconclusive = [
            check.name
            for check in required_checks
            if check.passed is None
        ]

        if inconclusive:
            return (
                VerificationStatus.INCONCLUSIVE,
                "Required checks inconclusive: "
                + ", ".join(inconclusive),
            )

        optional_failures = [
            check.name
            for check in checks
            if (
                not check.required
                and check.passed is not True
            )
        ]

        summary = (
            f"All {len(required_checks)} "
            "required checks passed"
        )

        if optional_failures:
            summary += (
                "; optional checks not passed: "
                + ", ".join(
                    optional_failures
                )
            )

        return (
            VerificationStatus.PASSED,
            summary,
        )

    @classmethod
    def _enforce_probe_integrity(
        cls,
        *,
        profile: VerificationProfile,
        checks: Any,
    ) -> list[VerificationCheck]:
        """
        Ensure collected checks match the declared profile.

        Missing, duplicate, or mutated required checks add a required
        inconclusive integrity check. Optional probe issues are recorded as an
        optional failed integrity check and cannot block required success.
        """

        critical_issues: list[str] = []
        advisory_issues: list[str] = []

        if not isinstance(
            checks,
            list,
        ):
            return [
                cls._integrity_check(
                    required=True,
                    issues=[
                        "collector result is not a list"
                    ],
                )
            ]

        valid_checks: list[
            VerificationCheck
        ] = []

        for item in checks:
            if isinstance(
                item,
                VerificationCheck,
            ):
                valid_checks.append(
                    item
                )
            else:
                critical_issues.append(
                    "collector returned an invalid check"
                )

        expected_name_counts = Counter(
            probe.name
            for probe in profile.probes
        )
        duplicate_profile_names = [
            name
            for name, count
            in expected_name_counts.items()
            if count != 1
        ]

        if duplicate_profile_names:
            critical_issues.append(
                "profile contains duplicate probe names: "
                + ", ".join(
                    duplicate_profile_names
                )
            )

        actual_by_name: dict[
            str,
            list[VerificationCheck],
        ] = {}

        for check in valid_checks:
            actual_by_name.setdefault(
                check.name,
                [],
            ).append(
                check
            )

        accepted: list[
            VerificationCheck
        ] = []

        for probe in profile.probes:
            matches = actual_by_name.get(
                probe.name,
                [],
            )
            issue_target = (
                critical_issues
                if probe.required
                else advisory_issues
            )

            if len(matches) != 1:
                issue_target.append(
                    "probe check count mismatch: "
                    f"{probe.name}={len(matches)}"
                )
                continue

            check = matches[0]

            if check.source != probe.source:
                issue_target.append(
                    "probe source mismatch: "
                    f"{probe.name}"
                )
                continue

            if check.required != probe.required:
                issue_target.append(
                    "probe required flag mismatch: "
                    f"{probe.name}"
                )
                continue

            accepted.append(
                check
            )

        expected_names = set(
            expected_name_counts
        )
        unexpected_names = sorted(
            set(actual_by_name)
            - expected_names
        )

        if unexpected_names:
            advisory_issues.append(
                "unexpected checks were ignored: "
                + ", ".join(
                    unexpected_names
                )
            )

        if critical_issues:
            accepted.append(
                cls._integrity_check(
                    required=True,
                    issues=(
                        critical_issues
                        + advisory_issues
                    ),
                )
            )

        elif advisory_issues:
            accepted.append(
                cls._integrity_check(
                    required=False,
                    issues=advisory_issues,
                )
            )

        return accepted

    @staticmethod
    def _integrity_check(
        *,
        required: bool,
        issues: list[str],
    ) -> VerificationCheck:
        return VerificationCheck(
            name=(
                "verification_probe_integrity"
            ),
            source=VerificationSource.EVENT,
            passed=(
                None
                if required
                else False
            ),
            required=required,
            expected_value=(
                "one matching check per declared probe"
            ),
            message=(
                "Verification probe integrity failed"
            ),
            metadata={
                "issues": list(
                    issues
                )
            },
        )

    @staticmethod
    def _collection_error_check(
        error: Exception,
    ) -> VerificationCheck:
        return VerificationCheck(
            name=(
                "verification_collection_error"
            ),
            source=VerificationSource.EVENT,
            passed=None,
            required=True,
            message=(
                "Verification evidence collection failed"
            ),
            metadata={
                "error_type": type(
                    error
                ).__name__,
                "error": str(
                    error
                ),
            },
        )
