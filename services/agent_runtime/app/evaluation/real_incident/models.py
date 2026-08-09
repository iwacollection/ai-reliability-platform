from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from common.domain.event import (
    StandardEvent,
)


class RealIncidentDatasetError(
    ValueError
):
    """
    Real historical Incident dataset is structurally invalid.
    """


class RealIncidentObservation(
    BaseModel
):
    """
    One historical observation captured around an Incident.

    The model is deliberately generic enough to preserve real operational
    evidence without coupling the Dataset format to one Tool implementation.

    Examples:

    source="kubernetes"
    kind="pod_state"

    source="prometheus"
    kind="memory_working_set"

    source="change"
    kind="deployment_revision"

    source="logs"
    kind="log_excerpt"
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    observation_id: str = Field(
        min_length=1,
        max_length=256,
    )

    source: str = Field(
        min_length=1,
        max_length=64,
    )

    kind: str = Field(
        min_length=1,
        max_length=128,
    )

    observed_at: datetime

    production_signal: bool = True

    data: dict[str, Any] = Field(
        default_factory=dict
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )

    @field_validator(
        "observation_id",
        "source",
        "kind",
    )
    @classmethod
    def normalize_text(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "Incident observation text cannot be empty"
            )

        return normalized

    @field_validator(
        "observed_at",
    )
    @classmethod
    def normalize_observed_at(
        cls,
        value: datetime,
    ) -> datetime:
        if value.tzinfo is None:
            raise ValueError(
                "Incident observation time must be timezone-aware"
            )

        return value.astimezone(
            UTC
        )


class RealIncidentTimelineEntry(
    BaseModel
):
    """
    Human or system Incident timeline entry.

    Timeline entries are retained for later audit and scoring, but are NOT
    included in the v1 Agent replay source because operator notes can contain
    post-hoc root-cause knowledge.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    timeline_id: str = Field(
        min_length=1,
        max_length=256,
    )

    occurred_at: datetime

    source: str = Field(
        min_length=1,
        max_length=64,
    )

    event_type: str = Field(
        min_length=1,
        max_length=128,
    )

    summary: str = Field(
        default="",
        max_length=4000,
    )

    evidence_refs: list[str] = Field(
        default_factory=list,
        max_length=128,
    )

    @field_validator(
        "timeline_id",
        "source",
        "event_type",
    )
    @classmethod
    def normalize_text(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "Incident timeline text cannot be empty"
            )

        return normalized

    @field_validator(
        "occurred_at",
    )
    @classmethod
    def normalize_time(
        cls,
        value: datetime,
    ) -> datetime:
        if value.tzinfo is None:
            raise ValueError(
                "Incident timeline time must be timezone-aware"
            )

        return value.astimezone(
            UTC
        )

    @model_validator(
        mode="after"
    )
    def validate_evidence_refs(
        self,
    ):
        if len(
            self.evidence_refs
        ) != len(
            set(
                self.evidence_refs
            )
        ):
            raise ValueError(
                "Incident timeline evidence_refs contain duplicates"
            )

        return self


class RealIncidentGroundTruth(
    BaseModel
):
    """
    Human-verified outcome used only after Agent execution.

    Ground Truth is explicitly separated from replay input so an Agent cannot
    accidentally receive the answer it is later evaluated against.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    root_cause: str = Field(
        min_length=1,
        max_length=8000,
    )

    contributing_factors: list[str] = Field(
        default_factory=list,
        max_length=64,
    )

    evidence_refs: list[str] = Field(
        default_factory=list,
        max_length=128,
    )

    source: str = Field(
        min_length=1,
        max_length=128,
    )

    quality: Literal[
        "verified",
        "reviewed",
        "provisional",
    ] = "verified"

    reviewed_at: datetime | None = None

    resolution_summary: str | None = Field(
        default=None,
        max_length=8000,
    )

    @field_validator(
        "root_cause",
        "source",
    )
    @classmethod
    def normalize_required_text(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "Ground Truth text cannot be empty"
            )

        return normalized

    @field_validator(
        "reviewed_at",
    )
    @classmethod
    def normalize_reviewed_at(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None

        if value.tzinfo is None:
            raise ValueError(
                "Ground Truth reviewed_at must be timezone-aware"
            )

        return value.astimezone(
            UTC
        )

    @model_validator(
        mode="after"
    )
    def validate_evidence_refs(
        self,
    ):
        if len(
            self.evidence_refs
        ) != len(
            set(
                self.evidence_refs
            )
        ):
            raise ValueError(
                "Ground Truth evidence_refs contain duplicates"
            )

        return self


class RealIncidentReplaySource(
    BaseModel
):
    """
    Ground-Truth-free source owned by Historical Replay.

    Deliberately excluded:

    - ground_truth
    - human Incident timeline
    - postmortem text
    - resolution summary

    Tool adapters added in a later stage will read observations from this
    object without exposing the complete Dataset to the Agent.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: Literal[
        "v1"
    ] = "v1"

    incident_id: str = Field(
        min_length=1,
        max_length=256,
    )

    event: StandardEvent

    observations: list[
        RealIncidentObservation
    ] = Field(
        default_factory=list,
        max_length=10000,
    )


class RealIncidentDataset(
    BaseModel
):
    """
    Canonical v1 contract for one real historical Incident.

    This is a Dataset model, not the Runtime Incident lifecycle object.

    It contains both:
    - replayable historical input;
    - evaluation-only Ground Truth.

    The two are separated by to_replay_source().
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: Literal[
        "v1"
    ] = "v1"

    incident_id: str = Field(
        min_length=1,
        max_length=256,
    )

    event: StandardEvent

    observations: list[
        RealIncidentObservation
    ] = Field(
        default_factory=list,
        max_length=10000,
    )

    timeline: list[
        RealIncidentTimelineEntry
    ] = Field(
        default_factory=list,
        max_length=10000,
    )

    ground_truth: RealIncidentGroundTruth

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )

    @field_validator(
        "incident_id",
    )
    @classmethod
    def normalize_incident_id(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "Real Incident incident_id cannot be empty"
            )

        return normalized

    @model_validator(
        mode="after"
    )
    def validate_dataset_integrity(
        self,
    ):
        occurred_at = getattr(
            self.event.header,
            "occurred_at",
            None,
        )

        if (
            not isinstance(
                occurred_at,
                datetime,
            )
            or occurred_at.tzinfo is None
        ):
            raise ValueError(
                "Real Incident event occurred_at must be timezone-aware"
            )

        observation_ids = [
            item.observation_id
            for item
            in self.observations
        ]

        if len(
            observation_ids
        ) != len(
            set(
                observation_ids
            )
        ):
            raise ValueError(
                "Real Incident observation_id values must be unique"
            )

        timeline_ids = [
            item.timeline_id
            for item
            in self.timeline
        ]

        if len(
            timeline_ids
        ) != len(
            set(
                timeline_ids
            )
        ):
            raise ValueError(
                "Real Incident timeline_id values must be unique"
            )

        known_evidence_ids = set(
            observation_ids
        )

        for entry in self.timeline:
            unknown = (
                set(
                    entry.evidence_refs
                )
                - known_evidence_ids
            )

            if unknown:
                raise ValueError(
                    "Incident timeline references unknown evidence"
                )

        unknown_ground_truth_refs = (
            set(
                self.ground_truth.evidence_refs
            )
            - known_evidence_ids
        )

        if unknown_ground_truth_refs:
            raise ValueError(
                "Ground Truth references unknown evidence"
            )

        timeline_times = [
            entry.occurred_at
            for entry
            in self.timeline
        ]

        if timeline_times != sorted(
            timeline_times
        ):
            raise ValueError(
                "Real Incident timeline must be chronological"
            )

        reviewed_at = (
            self.ground_truth.reviewed_at
        )

        if (
            reviewed_at is not None
            and reviewed_at
            < occurred_at.astimezone(
                UTC
            )
        ):
            raise ValueError(
                "Ground Truth cannot be reviewed before the Incident"
            )

        return self

    def to_replay_source(
        self,
    ) -> RealIncidentReplaySource:
        """
        Produce the only v1 object allowed to enter Historical Replay.

        Ground Truth and human timeline are intentionally absent.
        """

        return RealIncidentReplaySource(
            incident_id=self.incident_id,
            event=self.event.model_copy(
                deep=True
            ),
            observations=[
                observation.model_copy(
                    deep=True
                )
                for observation
                in self.observations
            ],
        )


__all__ = [
    "RealIncidentDataset",
    "RealIncidentDatasetError",
    "RealIncidentGroundTruth",
    "RealIncidentObservation",
    "RealIncidentReplaySource",
    "RealIncidentTimelineEntry",
]
