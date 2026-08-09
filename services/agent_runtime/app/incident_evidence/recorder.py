from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from services.agent_runtime.app.evaluation.real_incident.models import (
    RealIncidentObservation,
    RealIncidentReplaySource,
)
from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    InvestigationProbe,
    InvestigationScope,
)
from services.agent_runtime.app.investigation.probes import (
    InvestigationProbeError,
    ReadOnlyInvestigationProbeExecutor,
)


class ProductionIncidentEvidenceRecorderError(
    RuntimeError
):
    """
    Production Incident evidence cannot be preserved safely.
    """


class ProductionIncidentEvidenceScopeError(
    ProductionIncidentEvidenceRecorderError
):
    """
    StandardEvent cannot produce one unambiguous Pod/resource scope.
    """


class ProductionIncidentEvidenceUnavailableError(
    ProductionIncidentEvidenceRecorderError
):
    """
    No trusted production observation could be captured.
    """


@dataclass(
    frozen=True,
    slots=True,
)
class ProductionIncidentEvidenceRecordResult:
    """
    Result of one idempotent evidence-preservation attempt.
    """

    incident_id: str
    path: Path
    created: bool
    observation_count: int
    collected_probes: tuple[
        InvestigationProbe,
        ...,
    ]
    failed_probes: tuple[
        InvestigationProbe,
        ...,
    ]


class ProductionIncidentEvidenceRecorder:
    """
    Preserve the first bounded production evidence snapshot for one event.

    The recorder deliberately reuses the existing Investigation trust
    boundary instead of calling Kubernetes or Prometheus directly.

    Flow:

        StandardEvent / AgentContext
            -> trusted InvestigationScope
            -> ReadOnlyInvestigationProbeExecutor
            -> trusted EvidenceItem only
            -> RealIncidentObservation
            -> RealIncidentReplaySource JSON

    Deliberately absent:

    - LLM requests
    - PlannerPipeline
    - Healing
    - Action
    - Approval
    - Verification
    - Ground Truth
    - human Timeline

    Idempotency:

        capture ID is derived from event_id.

    A duplicate delivery of the same event returns the existing capture
    without probing production systems again.
    """

    DEFAULT_PROBES: tuple[
        InvestigationProbe,
        ...,
    ] = (
        InvestigationProbe.KUBERNETES_POD_STATE,
        InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
        InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,
        InvestigationProbe.PROMETHEUS_RESTART_COUNT,
    )

    def __init__(
        self,
        output_dir: str | Path,
        *,
        probe_executor: (
            ReadOnlyInvestigationProbeExecutor
            | None
        ) = None,
        probes: (
            tuple[
                InvestigationProbe,
                ...,
            ]
            | None
        ) = None,
    ) -> None:
        self.output_dir = Path(
            output_dir
        )

        self.probe_executor = (
            probe_executor
            if probe_executor is not None
            else ReadOnlyInvestigationProbeExecutor()
        )

        if not isinstance(
            self.probe_executor,
            ReadOnlyInvestigationProbeExecutor,
        ):
            raise TypeError(
                "Production Incident recorder probe executor is invalid"
            )

        resolved_probes = (
            probes
            if probes is not None
            else self.DEFAULT_PROBES
        )

        if (
            not isinstance(
                resolved_probes,
                tuple,
            )
            or not resolved_probes
            or any(
                not isinstance(
                    item,
                    InvestigationProbe,
                )
                for item in resolved_probes
            )
        ):
            raise TypeError(
                "Production Incident recorder probes are invalid"
            )

        if len(
            resolved_probes
        ) != len(
            set(
                resolved_probes
            )
        ):
            raise ValueError(
                "Production Incident recorder probes contain duplicates"
            )

        self.probes = (
            resolved_probes
        )

    async def record(
        self,
        context,
    ) -> ProductionIncidentEvidenceRecordResult:
        event = getattr(
            context,
            "event",
            None,
        )

        if event is None:
            raise ProductionIncidentEvidenceScopeError(
                "Production Incident recorder event is unavailable"
            )

        incident_id = (
            self._incident_id(
                event
            )
        )

        path = (
            self.output_dir
            / f"{incident_id}.replay.json"
        )

        existing = (
            self._load_existing(
                path,
                expected_incident_id=incident_id,
            )
        )

        if existing is not None:
            return ProductionIncidentEvidenceRecordResult(
                incident_id=incident_id,
                path=path,
                created=False,
                observation_count=len(
                    existing.observations
                ),
                collected_probes=tuple(
                    self._probe_from_observation(
                        item
                    )
                    for item
                    in existing.observations
                ),
                failed_probes=(),
            )

        scope = (
            self._scope_from_event(
                event
            )
        )

        observations: list[
            RealIncidentObservation
        ] = []

        collected_probes: list[
            InvestigationProbe
        ] = []

        failed_probes: list[
            InvestigationProbe
        ] = []

        for probe in self.probes:
            try:
                evidence = await (
                    self.probe_executor.collect(
                        context,
                        scope,
                        probe,
                    )
                )

                observation = (
                    self._observation_from_evidence(
                        scope=scope,
                        evidence=evidence,
                    )
                )

            except (
                InvestigationProbeError,
                ProductionIncidentEvidenceRecorderError,
                ValidationError,
                ValueError,
                TypeError,
            ):
                failed_probes.append(
                    probe
                )

                continue

            observations.append(
                observation
            )

            collected_probes.append(
                probe
            )

        if not observations:
            raise ProductionIncidentEvidenceUnavailableError(
                "No trusted production evidence was captured"
            )

        source = (
            RealIncidentReplaySource(
                incident_id=incident_id,
                event=event.model_copy(
                    deep=True
                ),
                observations=observations,
            )
        )

        self._write_new_capture(
            path,
            source,
        )

        return ProductionIncidentEvidenceRecordResult(
            incident_id=incident_id,
            path=path,
            created=True,
            observation_count=len(
                observations
            ),
            collected_probes=tuple(
                collected_probes
            ),
            failed_probes=tuple(
                failed_probes
            ),
        )

    @staticmethod
    def _incident_id(
        event,
    ) -> str:
        event_id = getattr(
            getattr(
                event,
                "header",
                None,
            ),
            "event_id",
            None,
        )

        if isinstance(
            event_id,
            UUID,
        ):
            normalized = str(
                event_id
            )

        elif isinstance(
            event_id,
            str,
        ):
            try:
                normalized = str(
                    UUID(
                        event_id
                    )
                )

            except ValueError as exc:
                raise ProductionIncidentEvidenceScopeError(
                    "Production Incident event_id is invalid"
                ) from exc

        else:
            raise ProductionIncidentEvidenceScopeError(
                "Production Incident event_id is unavailable"
            )

        return (
            f"capture-{normalized}"
        )

    @staticmethod
    def _scope_from_event(
        event,
    ) -> InvestigationScope:
        signal = getattr(
            event,
            "signal",
            None,
        )

        resources = list(
            getattr(
                event,
                "resources",
                [],
            )
            or []
        )

        if len(
            resources
        ) != 1:
            raise ProductionIncidentEvidenceScopeError(
                "Production Incident recorder requires exactly one resource"
            )

        resource = (
            resources[0]
        )

        name = getattr(
            resource,
            "name",
            None,
        )

        namespace = (
            getattr(
                resource,
                "namespace",
                None,
            )
            or "default"
        )

        cluster = getattr(
            resource,
            "cluster",
            None,
        )

        occurred_at = getattr(
            getattr(
                event,
                "header",
                None,
            ),
            "occurred_at",
            None,
        )

        alert_name = getattr(
            signal,
            "name",
            None,
        )

        alert_message = (
            getattr(
                signal,
                "message",
                "",
            )
            or ""
        )

        try:
            return InvestigationScope(
                alert_name=alert_name,
                alert_message=(
                    alert_message
                ),
                event_occurred_at=(
                    occurred_at
                ),
                resource=name,
                namespace=namespace,
                cluster=cluster,
            )

        except ValidationError as exc:
            raise ProductionIncidentEvidenceScopeError(
                "Production Incident recorder scope is invalid"
            ) from exc

    @staticmethod
    def _observation_from_evidence(
        *,
        scope: InvestigationScope,
        evidence: EvidenceItem,
    ) -> RealIncidentObservation:
        if not isinstance(
            evidence,
            EvidenceItem,
        ):
            raise ProductionIncidentEvidenceRecorderError(
                "Recorder evidence type is invalid"
            )

        if (
            evidence.success
            is not True
            or evidence.trusted
            is not True
            or evidence.production_signal
            is not True
        ):
            raise ProductionIncidentEvidenceRecorderError(
                "Recorder accepts trusted production evidence only"
            )

        metadata: dict[
            str,
            Any,
        ] = {
            "resource": (
                scope.resource
            ),
            "namespace": (
                scope.namespace
            ),
            "cluster": (
                scope.cluster
            ),
            "capture_source": (
                "production_incident_evidence_recorder_v1"
            ),
            "reliability": (
                evidence.reliability
            ),
        }

        if (
            evidence.probe
            == InvestigationProbe.KUBERNETES_POD_STATE
        ):
            data = (
                ProductionIncidentEvidenceRecorder
                ._kubernetes_data(
                    evidence
                )
            )

            source = "kubernetes"
            kind = "pod_state"

        elif (
            evidence.probe
            == InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET
        ):
            data = (
                ProductionIncidentEvidenceRecorder
                ._metric_data(
                    evidence
                )
            )

            source = "prometheus"
            kind = "memory_working_set"

        elif (
            evidence.probe
            == InvestigationProbe.PROMETHEUS_MEMORY_LIMIT
        ):
            data = (
                ProductionIncidentEvidenceRecorder
                ._metric_data(
                    evidence
                )
            )

            source = "prometheus"
            kind = "memory_limit"

        elif (
            evidence.probe
            == InvestigationProbe.PROMETHEUS_RESTART_COUNT
        ):
            data = (
                ProductionIncidentEvidenceRecorder
                ._metric_data(
                    evidence
                )
            )

            source = "prometheus"
            kind = "restart_count"

        else:
            raise ProductionIncidentEvidenceRecorderError(
                "Recorder received an unsupported Probe"
            )

        return RealIncidentObservation(
            observation_id=(
                evidence.evidence_id
            ),
            source=source,
            kind=kind,
            observed_at=(
                evidence.observed_at
            ),
            production_signal=True,
            data=data,
            metadata=metadata,
        )

    @staticmethod
    def _kubernetes_data(
        evidence: EvidenceItem,
    ) -> dict[str, Any]:
        facts = evidence.facts

        container: dict[
            str,
            Any,
        ] = {}

        restart_count = facts.get(
            "max_restart_count"
        )

        if isinstance(
            restart_count,
            int,
        ):
            container[
                "restart_count"
            ] = restart_count

        state_reasons = facts.get(
            "state_reasons"
        )

        if isinstance(
            state_reasons,
            str,
        ) and state_reasons:
            container[
                "state_reason"
            ] = state_reasons

        termination_reasons = facts.get(
            "last_termination_reasons"
        )

        if isinstance(
            termination_reasons,
            str,
        ) and termination_reasons:
            container[
                "last_termination_reason"
            ] = (
                termination_reasons
            )

        return {
            "phase": facts.get(
                "phase"
            ),
            "ready": facts.get(
                "ready"
            ),
            "scheduled": facts.get(
                "scheduled"
            ),
            "oom_killed": facts.get(
                "oom_killed"
            ),
            "containers": [
                container
            ],
        }

    @staticmethod
    def _metric_data(
        evidence: EvidenceItem,
    ) -> dict[str, Any]:
        value = evidence.facts.get(
            "value_sum"
        )

        if not isinstance(
            value,
            (
                int,
                float,
            ),
        ):
            raise ProductionIncidentEvidenceRecorderError(
                "Recorder metric evidence is unavailable"
            )

        return {
            "value": float(
                value
            ),
        }

    @staticmethod
    def _probe_from_observation(
        observation: RealIncidentObservation,
    ) -> InvestigationProbe:
        key = (
            observation.source,
            observation.kind,
        )

        mapping = {
            (
                "kubernetes",
                "pod_state",
            ): (
                InvestigationProbe.KUBERNETES_POD_STATE
            ),
            (
                "prometheus",
                "memory_working_set",
            ): (
                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET
            ),
            (
                "prometheus",
                "memory_limit",
            ): (
                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT
            ),
            (
                "prometheus",
                "restart_count",
            ): (
                InvestigationProbe.PROMETHEUS_RESTART_COUNT
            ),
        }

        probe = mapping.get(
            key
        )

        if probe is None:
            raise ProductionIncidentEvidenceRecorderError(
                "Existing capture contains an unsupported observation"
            )

        return probe

    @staticmethod
    def _load_existing(
        path: Path,
        *,
        expected_incident_id: str,
    ) -> RealIncidentReplaySource | None:
        if not path.exists():
            return None

        if (
            path.is_symlink()
            or not path.is_file()
        ):
            raise ProductionIncidentEvidenceRecorderError(
                "Existing Incident capture path is unsafe"
            )

        try:
            source = (
                RealIncidentReplaySource
                .model_validate_json(
                    path.read_text(
                        encoding="utf-8",
                    )
                )
            )

        except (
            OSError,
            UnicodeError,
            ValidationError,
        ) as exc:
            raise ProductionIncidentEvidenceRecorderError(
                "Existing Incident capture is invalid"
            ) from exc

        if (
            source.incident_id
            != expected_incident_id
        ):
            raise ProductionIncidentEvidenceRecorderError(
                "Existing Incident capture identity does not match"
            )

        return source

    def _write_new_capture(
        self,
        path: Path,
        source: RealIncidentReplaySource,
    ) -> None:
        self.output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        payload = json.dumps(
            source.model_dump(
                mode="json"
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        try:
            with path.open(
                "x",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                handle.write(
                    payload
                )
                handle.write(
                    "\n"
                )

        except FileExistsError:
            existing = (
                self._load_existing(
                    path,
                    expected_incident_id=(
                        source.incident_id
                    ),
                )
            )

            if existing is None:
                raise ProductionIncidentEvidenceRecorderError(
                    "Incident capture race could not be resolved"
                )

            if (
                existing.event.header.event_id
                != source.event.header.event_id
            ):
                raise ProductionIncidentEvidenceRecorderError(
                    "Incident capture race changed event identity"
                )


__all__ = [
    "ProductionIncidentEvidenceRecordResult",
    "ProductionIncidentEvidenceRecorder",
    "ProductionIncidentEvidenceRecorderError",
    "ProductionIncidentEvidenceScopeError",
    "ProductionIncidentEvidenceUnavailableError",
]
