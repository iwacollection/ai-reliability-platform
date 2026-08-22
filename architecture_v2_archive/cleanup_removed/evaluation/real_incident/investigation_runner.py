from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
)

from services.agent_runtime.app.evaluation.real_incident.historical_replay import (
    HistoricalReplayClockError,
    create_historical_replay_environment,
)
from services.agent_runtime.app.evaluation.real_incident.loader import (
    RealIncidentDatasetLoader,
)
from services.agent_runtime.app.evaluation.real_incident.models import (
    RealIncidentDataset,
)
from services.agent_runtime.app.investigation.coordinator import (
    EvidenceDrivenInvestigationCoordinator,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationState,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)
from services.agent_runtime.app.investigation.settings import (
    InvestigationSettings,
)
from services.agent_runtime.app.model.context import (
    AgentContext,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)


class HistoricalIncidentInvestigationRunnerError(
    RuntimeError
):
    """
    Historical Incident cannot be investigated through the bounded
    Investigation Runtime contract.
    """


class HistoricalIncidentInvestigationResult(
    BaseModel
):
    """
    Ground-Truth-free result of one point-in-time historical investigation.

    This object contains the Agent's own Investigation state only.

    Human evaluation labels remain outside the replay execution path.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: Literal[
        "v1"
    ] = "v1"

    replay_mode: Literal[
        "point_in_time"
    ] = "point_in_time"

    incident_id: str

    incident_time: datetime

    replay_at: datetime

    shadow_mode: Literal[
        True
    ] = True

    read_only: Literal[
        True
    ] = True

    decision_influence: Literal[
        False
    ] = False

    investigation: InvestigationState

    @field_validator(
        "incident_time",
        "replay_at",
    )
    @classmethod
    def normalize_time(
        cls,
        value: datetime,
    ) -> datetime:

        if value.tzinfo is None:
            raise ValueError(
                "Historical Investigation result time "
                "must be timezone-aware"
            )

        return value.astimezone(
            UTC
        )


class HistoricalIncidentInvestigationRunner:
    """
    Run the existing On-call SRE Investigation brain against one real
    historical Incident at one explicit causal time cutoff.

    Reused from the enabled AgentRuntime:

    - exact Investigation Reasoner
    - exact Investigation limits

    Replaced only for historical evaluation:

    - live Tool backend -> Historical Replay Tool backend
    - live Probe time policy -> causal Historical Replay policy

    Deliberately NOT executed:

    - PlannerPipeline
    - RCAAgent
    - HealingAgent
    - ActionRuntime
    - Approval
    - VerificationRuntime

    The Reasoner remains responsible for:

    - hypotheses
    - next Probe selection
    - investigation direction
    - stopping
    - conclusion
    """

    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        loader: RealIncidentDatasetLoader | None = None,
    ) -> None:

        if not isinstance(
            runtime,
            AgentRuntime,
        ):
            raise TypeError(
                "Historical Investigation requires AgentRuntime"
            )

        settings = getattr(
            runtime,
            "investigation_settings",
            None,
        )

        if not isinstance(
            settings,
            InvestigationSettings,
        ):
            raise HistoricalIncidentInvestigationRunnerError(
                "AgentRuntime Investigation settings are unavailable"
            )

        if not settings.enabled:
            raise HistoricalIncidentInvestigationRunnerError(
                "Historical Investigation requires enabled Investigation"
            )

        runtime_coordinator = getattr(
            runtime,
            "investigation_coordinator",
            None,
        )

        if not isinstance(
            runtime_coordinator,
            EvidenceDrivenInvestigationCoordinator,
        ):
            raise HistoricalIncidentInvestigationRunnerError(
                "AgentRuntime Investigation coordinator is unavailable"
            )

        reasoner = runtime_coordinator.reasoner

        if not isinstance(
            reasoner,
            BaseInvestigationReasoner,
        ):
            raise HistoricalIncidentInvestigationRunnerError(
                "AgentRuntime Investigation reasoner is unavailable"
            )

        resolved_loader = (
            loader
            if loader is not None
            else RealIncidentDatasetLoader()
        )

        if not isinstance(
            resolved_loader,
            RealIncidentDatasetLoader,
        ):
            raise TypeError(
                "Historical Investigation loader is invalid"
            )

        self.runtime = runtime

        self.settings = settings

        self.reasoner = reasoner

        self.loader = resolved_loader

    async def run_file(
        self,
        path: str | Path,
        *,
        replay_at: datetime | None = None,
    ) -> HistoricalIncidentInvestigationResult:
        """
        Load one validated real Incident JSON and investigate it.
        """

        dataset = self.loader.load(
            path
        )

        return await self.run(
            dataset,
            replay_at=replay_at,
        )

    async def run(
        self,
        dataset: RealIncidentDataset,
        *,
        replay_at: datetime | None = None,
    ) -> HistoricalIncidentInvestigationResult:
        """
        Investigate one real historical Incident at one causal cutoff.

        No automatic Replay Clock advancement is performed in v1.

        This is intentional: evaluation must not invent Agent/SRE timing.
        A caller may run the same Incident at T, T+30s, T+1m, etc. to
        measure when the Agent becomes capable of reaching a diagnosis.
        """

        if not isinstance(
            dataset,
            RealIncidentDataset,
        ):
            raise TypeError(
                "Historical Investigation dataset is invalid"
            )

        # This is the structural answer-isolation boundary.
        #
        # The resulting object contains only:
        # - event
        # - historical observations
        #
        # Human evaluation labels and human timeline never enter context.
        source = dataset.to_replay_source()

        incident_time = self._aware_utc(
            source.event.header.occurred_at,
            name="Incident occurred_at",
        )

        resolved_replay_at = (
            incident_time
            if replay_at is None
            else self._aware_utc(
                replay_at,
                name="replay_at",
            )
        )

        if resolved_replay_at < incident_time:
            raise HistoricalReplayClockError(
                "Historical Investigation replay_at "
                "cannot be before the Incident"
            )

        environment = (
            create_historical_replay_environment(
                source,
                start_at=resolved_replay_at,
            )
        )

        # A fresh coordinator is required because the historical Tool backend
        # is deliberately different from Runtime production tools.
        #
        # The Agent brain and limits are NOT replaced.
        coordinator = (
            EvidenceDrivenInvestigationCoordinator(
                reasoner=self.reasoner,
                probe_executor=(
                    environment.probe_executor
                ),
                limits=self.settings.limits,

                # Coordinator-generated timestamps, including failed Probe
                # evidence, must remain inside historical causal time rather
                # than leaking the wall-clock date of the evaluation run.
                utc_clock=(
                    lambda: (
                        environment.clock.current_time
                    )
                ),
            )
        )

        context = AgentContext(
            request_id=(
                "historical-investigation:"
                f"{source.incident_id}"
            ),
            event=source.event.model_copy(
                deep=True
            ),
            tools=environment.tools,
            metadata={},
        )

        state = await coordinator.investigate(
            context
        )

        snapshot = context.metadata.get(
            "investigation_shadow"
        )

        if not isinstance(
            snapshot,
            dict,
        ):
            raise HistoricalIncidentInvestigationRunnerError(
                "Historical Investigation snapshot is unavailable"
            )

        if (
            snapshot.get(
                "shadow_mode"
            )
            is not True
        ):
            raise HistoricalIncidentInvestigationRunnerError(
                "Historical Investigation snapshot is not Shadow"
            )

        if (
            snapshot.get(
                "read_only"
            )
            is not True
        ):
            raise HistoricalIncidentInvestigationRunnerError(
                "Historical Investigation snapshot is not read-only"
            )

        # Re-validate the published boundary instead of trusting metadata.
        published_state = (
            InvestigationState.model_validate(
                snapshot
            )
        )

        if (
            published_state.investigation_id
            != state.investigation_id
        ):
            raise HistoricalIncidentInvestigationRunnerError(
                "Historical Investigation state identity mismatch"
            )

        return HistoricalIncidentInvestigationResult(
            incident_id=source.incident_id,
            incident_time=incident_time,
            replay_at=(
                environment.clock.current_time
            ),
            investigation=(
                published_state.model_copy(
                    deep=True
                )
            ),
        )

    @staticmethod
    def _aware_utc(
        value: datetime,
        *,
        name: str,
    ) -> datetime:

        if (
            not isinstance(
                value,
                datetime,
            )
            or value.tzinfo is None
        ):
            raise HistoricalIncidentInvestigationRunnerError(
                f"{name} must be timezone-aware"
            )

        return value.astimezone(
            UTC
        )


__all__ = [
    "HistoricalIncidentInvestigationResult",
    "HistoricalIncidentInvestigationRunner",
    "HistoricalIncidentInvestigationRunnerError",
]
