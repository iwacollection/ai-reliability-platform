from __future__ import annotations

import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "production-incident-evidence-recorder-v1"
AFTER_NAME = "production_incident_evidence_recorder_v1_after.txt"
ERROR_NAME = "production_incident_evidence_recorder_v1_error.txt"

RECORDER_SOURCE = 'from __future__ import annotations\n\nimport json\nfrom dataclasses import dataclass\nfrom pathlib import Path\nfrom typing import Any\nfrom uuid import UUID\n\nfrom pydantic import ValidationError\n\nfrom services.agent_runtime.app.evaluation.real_incident.models import (\n    RealIncidentObservation,\n    RealIncidentReplaySource,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    InvestigationProbe,\n    InvestigationScope,\n)\nfrom services.agent_runtime.app.investigation.probes import (\n    InvestigationProbeError,\n    ReadOnlyInvestigationProbeExecutor,\n)\n\n\nclass ProductionIncidentEvidenceRecorderError(\n    RuntimeError\n):\n    """\n    Production Incident evidence cannot be preserved safely.\n    """\n\n\nclass ProductionIncidentEvidenceScopeError(\n    ProductionIncidentEvidenceRecorderError\n):\n    """\n    StandardEvent cannot produce one unambiguous Pod/resource scope.\n    """\n\n\nclass ProductionIncidentEvidenceUnavailableError(\n    ProductionIncidentEvidenceRecorderError\n):\n    """\n    No trusted production observation could be captured.\n    """\n\n\n@dataclass(\n    frozen=True,\n    slots=True,\n)\nclass ProductionIncidentEvidenceRecordResult:\n    """\n    Result of one idempotent evidence-preservation attempt.\n    """\n\n    incident_id: str\n    path: Path\n    created: bool\n    observation_count: int\n    collected_probes: tuple[\n        InvestigationProbe,\n        ...,\n    ]\n    failed_probes: tuple[\n        InvestigationProbe,\n        ...,\n    ]\n\n\nclass ProductionIncidentEvidenceRecorder:\n    """\n    Preserve the first bounded production evidence snapshot for one event.\n\n    The recorder deliberately reuses the existing Investigation trust\n    boundary instead of calling Kubernetes or Prometheus directly.\n\n    Flow:\n\n        StandardEvent / AgentContext\n            -> trusted InvestigationScope\n            -> ReadOnlyInvestigationProbeExecutor\n            -> trusted EvidenceItem only\n            -> RealIncidentObservation\n            -> RealIncidentReplaySource JSON\n\n    Deliberately absent:\n\n    - LLM requests\n    - PlannerPipeline\n    - Healing\n    - Action\n    - Approval\n    - Verification\n    - Ground Truth\n    - human Timeline\n\n    Idempotency:\n\n        capture ID is derived from event_id.\n\n    A duplicate delivery of the same event returns the existing capture\n    without probing production systems again.\n    """\n\n    DEFAULT_PROBES: tuple[\n        InvestigationProbe,\n        ...,\n    ] = (\n        InvestigationProbe.KUBERNETES_POD_STATE,\n        InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n        InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n    )\n\n    def __init__(\n        self,\n        output_dir: str | Path,\n        *,\n        probe_executor: (\n            ReadOnlyInvestigationProbeExecutor\n            | None\n        ) = None,\n        probes: (\n            tuple[\n                InvestigationProbe,\n                ...,\n            ]\n            | None\n        ) = None,\n    ) -> None:\n        self.output_dir = Path(\n            output_dir\n        )\n\n        self.probe_executor = (\n            probe_executor\n            if probe_executor is not None\n            else ReadOnlyInvestigationProbeExecutor()\n        )\n\n        if not isinstance(\n            self.probe_executor,\n            ReadOnlyInvestigationProbeExecutor,\n        ):\n            raise TypeError(\n                "Production Incident recorder probe executor is invalid"\n            )\n\n        resolved_probes = (\n            probes\n            if probes is not None\n            else self.DEFAULT_PROBES\n        )\n\n        if (\n            not isinstance(\n                resolved_probes,\n                tuple,\n            )\n            or not resolved_probes\n            or any(\n                not isinstance(\n                    item,\n                    InvestigationProbe,\n                )\n                for item in resolved_probes\n            )\n        ):\n            raise TypeError(\n                "Production Incident recorder probes are invalid"\n            )\n\n        if len(\n            resolved_probes\n        ) != len(\n            set(\n                resolved_probes\n            )\n        ):\n            raise ValueError(\n                "Production Incident recorder probes contain duplicates"\n            )\n\n        self.probes = (\n            resolved_probes\n        )\n\n    async def record(\n        self,\n        context,\n    ) -> ProductionIncidentEvidenceRecordResult:\n        event = getattr(\n            context,\n            "event",\n            None,\n        )\n\n        if event is None:\n            raise ProductionIncidentEvidenceScopeError(\n                "Production Incident recorder event is unavailable"\n            )\n\n        incident_id = (\n            self._incident_id(\n                event\n            )\n        )\n\n        path = (\n            self.output_dir\n            / f"{incident_id}.replay.json"\n        )\n\n        existing = (\n            self._load_existing(\n                path,\n                expected_incident_id=incident_id,\n            )\n        )\n\n        if existing is not None:\n            return ProductionIncidentEvidenceRecordResult(\n                incident_id=incident_id,\n                path=path,\n                created=False,\n                observation_count=len(\n                    existing.observations\n                ),\n                collected_probes=tuple(\n                    self._probe_from_observation(\n                        item\n                    )\n                    for item\n                    in existing.observations\n                ),\n                failed_probes=(),\n            )\n\n        scope = (\n            self._scope_from_event(\n                event\n            )\n        )\n\n        observations: list[\n            RealIncidentObservation\n        ] = []\n\n        collected_probes: list[\n            InvestigationProbe\n        ] = []\n\n        failed_probes: list[\n            InvestigationProbe\n        ] = []\n\n        for probe in self.probes:\n            try:\n                evidence = await (\n                    self.probe_executor.collect(\n                        context,\n                        scope,\n                        probe,\n                    )\n                )\n\n                observation = (\n                    self._observation_from_evidence(\n                        scope=scope,\n                        evidence=evidence,\n                    )\n                )\n\n            except (\n                InvestigationProbeError,\n                ProductionIncidentEvidenceRecorderError,\n                ValidationError,\n                ValueError,\n                TypeError,\n            ):\n                failed_probes.append(\n                    probe\n                )\n\n                continue\n\n            observations.append(\n                observation\n            )\n\n            collected_probes.append(\n                probe\n            )\n\n        if not observations:\n            raise ProductionIncidentEvidenceUnavailableError(\n                "No trusted production evidence was captured"\n            )\n\n        source = (\n            RealIncidentReplaySource(\n                incident_id=incident_id,\n                event=event.model_copy(\n                    deep=True\n                ),\n                observations=observations,\n            )\n        )\n\n        self._write_new_capture(\n            path,\n            source,\n        )\n\n        return ProductionIncidentEvidenceRecordResult(\n            incident_id=incident_id,\n            path=path,\n            created=True,\n            observation_count=len(\n                observations\n            ),\n            collected_probes=tuple(\n                collected_probes\n            ),\n            failed_probes=tuple(\n                failed_probes\n            ),\n        )\n\n    @staticmethod\n    def _incident_id(\n        event,\n    ) -> str:\n        event_id = getattr(\n            getattr(\n                event,\n                "header",\n                None,\n            ),\n            "event_id",\n            None,\n        )\n\n        if isinstance(\n            event_id,\n            UUID,\n        ):\n            normalized = str(\n                event_id\n            )\n\n        elif isinstance(\n            event_id,\n            str,\n        ):\n            try:\n                normalized = str(\n                    UUID(\n                        event_id\n                    )\n                )\n\n            except ValueError as exc:\n                raise ProductionIncidentEvidenceScopeError(\n                    "Production Incident event_id is invalid"\n                ) from exc\n\n        else:\n            raise ProductionIncidentEvidenceScopeError(\n                "Production Incident event_id is unavailable"\n            )\n\n        return (\n            f"capture-{normalized}"\n        )\n\n    @staticmethod\n    def _scope_from_event(\n        event,\n    ) -> InvestigationScope:\n        signal = getattr(\n            event,\n            "signal",\n            None,\n        )\n\n        resources = list(\n            getattr(\n                event,\n                "resources",\n                [],\n            )\n            or []\n        )\n\n        if len(\n            resources\n        ) != 1:\n            raise ProductionIncidentEvidenceScopeError(\n                "Production Incident recorder requires exactly one resource"\n            )\n\n        resource = (\n            resources[0]\n        )\n\n        name = getattr(\n            resource,\n            "name",\n            None,\n        )\n\n        namespace = (\n            getattr(\n                resource,\n                "namespace",\n                None,\n            )\n            or "default"\n        )\n\n        cluster = getattr(\n            resource,\n            "cluster",\n            None,\n        )\n\n        occurred_at = getattr(\n            getattr(\n                event,\n                "header",\n                None,\n            ),\n            "occurred_at",\n            None,\n        )\n\n        alert_name = getattr(\n            signal,\n            "name",\n            None,\n        )\n\n        alert_message = (\n            getattr(\n                signal,\n                "message",\n                "",\n            )\n            or ""\n        )\n\n        try:\n            return InvestigationScope(\n                alert_name=alert_name,\n                alert_message=(\n                    alert_message\n                ),\n                event_occurred_at=(\n                    occurred_at\n                ),\n                resource=name,\n                namespace=namespace,\n                cluster=cluster,\n            )\n\n        except ValidationError as exc:\n            raise ProductionIncidentEvidenceScopeError(\n                "Production Incident recorder scope is invalid"\n            ) from exc\n\n    @staticmethod\n    def _observation_from_evidence(\n        *,\n        scope: InvestigationScope,\n        evidence: EvidenceItem,\n    ) -> RealIncidentObservation:\n        if not isinstance(\n            evidence,\n            EvidenceItem,\n        ):\n            raise ProductionIncidentEvidenceRecorderError(\n                "Recorder evidence type is invalid"\n            )\n\n        if (\n            evidence.success\n            is not True\n            or evidence.trusted\n            is not True\n            or evidence.production_signal\n            is not True\n        ):\n            raise ProductionIncidentEvidenceRecorderError(\n                "Recorder accepts trusted production evidence only"\n            )\n\n        metadata: dict[\n            str,\n            Any,\n        ] = {\n            "resource": (\n                scope.resource\n            ),\n            "namespace": (\n                scope.namespace\n            ),\n            "cluster": (\n                scope.cluster\n            ),\n            "capture_source": (\n                "production_incident_evidence_recorder_v1"\n            ),\n            "reliability": (\n                evidence.reliability\n            ),\n        }\n\n        if (\n            evidence.probe\n            == InvestigationProbe.KUBERNETES_POD_STATE\n        ):\n            data = (\n                ProductionIncidentEvidenceRecorder\n                ._kubernetes_data(\n                    evidence\n                )\n            )\n\n            source = "kubernetes"\n            kind = "pod_state"\n\n        elif (\n            evidence.probe\n            == InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET\n        ):\n            data = (\n                ProductionIncidentEvidenceRecorder\n                ._metric_data(\n                    evidence\n                )\n            )\n\n            source = "prometheus"\n            kind = "memory_working_set"\n\n        elif (\n            evidence.probe\n            == InvestigationProbe.PROMETHEUS_MEMORY_LIMIT\n        ):\n            data = (\n                ProductionIncidentEvidenceRecorder\n                ._metric_data(\n                    evidence\n                )\n            )\n\n            source = "prometheus"\n            kind = "memory_limit"\n\n        elif (\n            evidence.probe\n            == InvestigationProbe.PROMETHEUS_RESTART_COUNT\n        ):\n            data = (\n                ProductionIncidentEvidenceRecorder\n                ._metric_data(\n                    evidence\n                )\n            )\n\n            source = "prometheus"\n            kind = "restart_count"\n\n        else:\n            raise ProductionIncidentEvidenceRecorderError(\n                "Recorder received an unsupported Probe"\n            )\n\n        return RealIncidentObservation(\n            observation_id=(\n                evidence.evidence_id\n            ),\n            source=source,\n            kind=kind,\n            observed_at=(\n                evidence.observed_at\n            ),\n            production_signal=True,\n            data=data,\n            metadata=metadata,\n        )\n\n    @staticmethod\n    def _kubernetes_data(\n        evidence: EvidenceItem,\n    ) -> dict[str, Any]:\n        facts = evidence.facts\n\n        container: dict[\n            str,\n            Any,\n        ] = {}\n\n        restart_count = facts.get(\n            "max_restart_count"\n        )\n\n        if isinstance(\n            restart_count,\n            int,\n        ):\n            container[\n                "restart_count"\n            ] = restart_count\n\n        state_reasons = facts.get(\n            "state_reasons"\n        )\n\n        if isinstance(\n            state_reasons,\n            str,\n        ) and state_reasons:\n            container[\n                "state_reason"\n            ] = state_reasons\n\n        termination_reasons = facts.get(\n            "last_termination_reasons"\n        )\n\n        if isinstance(\n            termination_reasons,\n            str,\n        ) and termination_reasons:\n            container[\n                "last_termination_reason"\n            ] = (\n                termination_reasons\n            )\n\n        return {\n            "phase": facts.get(\n                "phase"\n            ),\n            "ready": facts.get(\n                "ready"\n            ),\n            "scheduled": facts.get(\n                "scheduled"\n            ),\n            "oom_killed": facts.get(\n                "oom_killed"\n            ),\n            "containers": [\n                container\n            ],\n        }\n\n    @staticmethod\n    def _metric_data(\n        evidence: EvidenceItem,\n    ) -> dict[str, Any]:\n        value = evidence.facts.get(\n            "value_sum"\n        )\n\n        if not isinstance(\n            value,\n            (\n                int,\n                float,\n            ),\n        ):\n            raise ProductionIncidentEvidenceRecorderError(\n                "Recorder metric evidence is unavailable"\n            )\n\n        return {\n            "value": float(\n                value\n            ),\n        }\n\n    @staticmethod\n    def _probe_from_observation(\n        observation: RealIncidentObservation,\n    ) -> InvestigationProbe:\n        key = (\n            observation.source,\n            observation.kind,\n        )\n\n        mapping = {\n            (\n                "kubernetes",\n                "pod_state",\n            ): (\n                InvestigationProbe.KUBERNETES_POD_STATE\n            ),\n            (\n                "prometheus",\n                "memory_working_set",\n            ): (\n                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET\n            ),\n            (\n                "prometheus",\n                "memory_limit",\n            ): (\n                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT\n            ),\n            (\n                "prometheus",\n                "restart_count",\n            ): (\n                InvestigationProbe.PROMETHEUS_RESTART_COUNT\n            ),\n        }\n\n        probe = mapping.get(\n            key\n        )\n\n        if probe is None:\n            raise ProductionIncidentEvidenceRecorderError(\n                "Existing capture contains an unsupported observation"\n            )\n\n        return probe\n\n    @staticmethod\n    def _load_existing(\n        path: Path,\n        *,\n        expected_incident_id: str,\n    ) -> RealIncidentReplaySource | None:\n        if not path.exists():\n            return None\n\n        if (\n            path.is_symlink()\n            or not path.is_file()\n        ):\n            raise ProductionIncidentEvidenceRecorderError(\n                "Existing Incident capture path is unsafe"\n            )\n\n        try:\n            source = (\n                RealIncidentReplaySource\n                .model_validate_json(\n                    path.read_text(\n                        encoding="utf-8",\n                    )\n                )\n            )\n\n        except (\n            OSError,\n            UnicodeError,\n            ValidationError,\n        ) as exc:\n            raise ProductionIncidentEvidenceRecorderError(\n                "Existing Incident capture is invalid"\n            ) from exc\n\n        if (\n            source.incident_id\n            != expected_incident_id\n        ):\n            raise ProductionIncidentEvidenceRecorderError(\n                "Existing Incident capture identity does not match"\n            )\n\n        return source\n\n    def _write_new_capture(\n        self,\n        path: Path,\n        source: RealIncidentReplaySource,\n    ) -> None:\n        self.output_dir.mkdir(\n            parents=True,\n            exist_ok=True,\n        )\n\n        payload = json.dumps(\n            source.model_dump(\n                mode="json"\n            ),\n            ensure_ascii=False,\n            indent=2,\n            sort_keys=True,\n        )\n\n        try:\n            with path.open(\n                "x",\n                encoding="utf-8",\n                newline="\\n",\n            ) as handle:\n                handle.write(\n                    payload\n                )\n                handle.write(\n                    "\\n"\n                )\n\n        except FileExistsError:\n            existing = (\n                self._load_existing(\n                    path,\n                    expected_incident_id=(\n                        source.incident_id\n                    ),\n                )\n            )\n\n            if existing is None:\n                raise ProductionIncidentEvidenceRecorderError(\n                    "Incident capture race could not be resolved"\n                )\n\n            if (\n                existing.event.header.event_id\n                != source.event.header.event_id\n            ):\n                raise ProductionIncidentEvidenceRecorderError(\n                    "Incident capture race changed event identity"\n                )\n\n\n__all__ = [\n    "ProductionIncidentEvidenceRecordResult",\n    "ProductionIncidentEvidenceRecorder",\n    "ProductionIncidentEvidenceRecorderError",\n    "ProductionIncidentEvidenceScopeError",\n    "ProductionIncidentEvidenceUnavailableError",\n]\n'
INIT_SOURCE = 'from services.agent_runtime.app.incident_evidence.recorder import (\n    ProductionIncidentEvidenceRecordResult,\n    ProductionIncidentEvidenceRecorder,\n    ProductionIncidentEvidenceRecorderError,\n    ProductionIncidentEvidenceScopeError,\n    ProductionIncidentEvidenceUnavailableError,\n)\n\n\n__all__ = [\n    "ProductionIncidentEvidenceRecordResult",\n    "ProductionIncidentEvidenceRecorder",\n    "ProductionIncidentEvidenceRecorderError",\n    "ProductionIncidentEvidenceScopeError",\n    "ProductionIncidentEvidenceUnavailableError",\n]\n'
TEST_SOURCE = 'from __future__ import annotations\n\nimport json\nfrom datetime import UTC, datetime\nfrom types import SimpleNamespace\nfrom uuid import UUID\n\nimport pytest\n\nfrom common.domain.event import (\n    Header,\n    Resource,\n    Signal,\n    StandardEvent,\n)\nfrom common.domain.event.enums import (\n    EventSource,\n    ResourceKind,\n    Severity,\n    SignalType,\n)\n\nfrom services.agent_runtime.app.evaluation.real_incident.historical_replay import (\n    create_historical_replay_environment,\n)\nfrom services.agent_runtime.app.evaluation.real_incident.models import (\n    RealIncidentReplaySource,\n)\nfrom services.agent_runtime.app.incident_evidence.recorder import (\n    ProductionIncidentEvidenceRecorder,\n    ProductionIncidentEvidenceScopeError,\n    ProductionIncidentEvidenceUnavailableError,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationProbe,\n    InvestigationScope,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    10,\n    8,\n    10,\n    0,\n    tzinfo=UTC,\n)\n\n\nclass TrustedToolManager:\n    def __init__(\n        self,\n    ) -> None:\n        self.calls = []\n\n    async def call(\n        self,\n        name,\n        context=None,\n        **kwargs,\n    ):\n        self.calls.append(\n            {\n                "name": name,\n                "kwargs": kwargs,\n            }\n        )\n\n        if name == "kubernetes":\n            return {\n                "success": True,\n                "source": "kubernetes",\n                "mode": "read_only",\n                "production_signal": True,\n                "observed_at": NOW.isoformat(),\n                "data": {\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": True,\n                    "containers": [\n                        {\n                            "restart_count": 7,\n                            "state_reason": (\n                                "CrashLoopBackOff"\n                            ),\n                            "last_termination_reason": (\n                                "OOMKilled"\n                            ),\n                            "image_id": (\n                                "must-not-be-retained"\n                            ),\n                        }\n                    ],\n                    "uid": "must-not-be-retained",\n                },\n            }\n\n        query = kwargs[\n            "query"\n        ]\n\n        if (\n            "container_memory_working_set_bytes"\n            in query\n        ):\n            value = 503316480.0\n\n        elif (\n            "kube_pod_container_resource_limits"\n            in query\n        ):\n            value = 536870912.0\n\n        elif (\n            "kube_pod_container_status_restarts_total"\n            in query\n        ):\n            value = 7.0\n\n        else:\n            raise AssertionError(\n                "Unexpected Prometheus query"\n            )\n\n        return {\n            "success": True,\n            "source": "prometheus",\n            "mode": "read_only",\n            "production_signal": True,\n            "observed_at": NOW.isoformat(),\n            "query": (\n                "must-not-be-retained"\n            ),\n            "data": {\n                "resultType": "vector",\n                "result": [\n                    {\n                        "metric": {\n                            "pod": (\n                                "payment-api"\n                            ),\n                        },\n                        "value": [\n                            NOW.timestamp(),\n                            str(\n                                value\n                            ),\n                        ],\n                    }\n                ],\n            },\n        }\n\n\nclass UntrustedToolManager:\n    async def call(\n        self,\n        name,\n        context=None,\n        **kwargs,\n    ):\n        if name == "kubernetes":\n            return {\n                "success": True,\n                "source": "mock_kubernetes",\n                "mode": "dry_run",\n                "production_signal": False,\n                "observed_at": NOW.isoformat(),\n                "data": {\n                    "phase": "Running",\n                    "containers": [],\n                },\n            }\n\n        return {\n            "success": True,\n            "source": "mock_prometheus",\n            "mode": "mock",\n            "production_signal": False,\n            "observed_at": NOW.isoformat(),\n            "data": {\n                "resultType": "vector",\n                "result": [],\n            },\n        }\n\n\ndef event(\n    *,\n    resources: int = 1,\n) -> StandardEvent:\n    return StandardEvent(\n        header=Header(\n            event_id=UUID(\n                "11111111-1111-4111-8111-111111111111"\n            ),\n            trace_id=UUID(\n                "22222222-2222-4222-8222-222222222222"\n            ),\n            source=(\n                EventSource.ALERTMANAGER\n            ),\n            occurred_at=NOW,\n        ),\n        signal=Signal(\n            type=SignalType.ALERT,\n            name="PodOOMKilled",\n            severity=Severity.CRITICAL,\n            message=(\n                "payment-api restarted"\n            ),\n            labels={},\n        ),\n        resources=[\n            Resource(\n                kind=ResourceKind.POD,\n                name=(\n                    "payment-api"\n                    if index == 0\n                    else f"payment-api-{index}"\n                ),\n                namespace="payment",\n                cluster="production-a",\n            )\n            for index\n            in range(\n                resources\n            )\n        ],\n    )\n\n\ndef context(\n    tools,\n    *,\n    resources: int = 1,\n):\n    return SimpleNamespace(\n        event=event(\n            resources=resources\n        ),\n        tools=tools,\n    )\n\n\ndef scope() -> InvestigationScope:\n    return InvestigationScope(\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "payment-api restarted"\n        ),\n        event_occurred_at=NOW,\n        resource="payment-api",\n        namespace="payment",\n        cluster="production-a",\n    )\n\n\n@pytest.mark.asyncio\nasync def test_recorder_persists_replay_safe_capture(\n    tmp_path,\n):\n    tools = TrustedToolManager()\n\n    recorder = (\n        ProductionIncidentEvidenceRecorder(\n            tmp_path\n            / "captures"\n        )\n    )\n\n    result = await recorder.record(\n        context(\n            tools\n        )\n    )\n\n    assert result.created is True\n    assert result.observation_count == 4\n\n    assert result.collected_probes == (\n        InvestigationProbe.KUBERNETES_POD_STATE,\n        InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n        InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n    )\n\n    source = (\n        RealIncidentReplaySource\n        .model_validate_json(\n            result.path.read_text(\n                encoding="utf-8"\n            )\n        )\n    )\n\n    assert source.incident_id == (\n        "capture-"\n        "11111111-1111-4111-8111-111111111111"\n    )\n\n    assert len(\n        source.observations\n    ) == 4\n\n    serialized = json.dumps(\n        source.model_dump(\n            mode="json"\n        ),\n        sort_keys=True,\n    )\n\n    assert (\n        "ground_truth"\n        not in serialized\n    )\n\n    assert (\n        "timeline"\n        not in serialized\n    )\n\n    assert (\n        "must-not-be-retained"\n        not in serialized\n    )\n\n    assert (\n        "must-not-be-retained"\n        not in result.path.read_text(\n            encoding="utf-8"\n        )\n    )\n\n\n@pytest.mark.asyncio\nasync def test_capture_round_trips_through_historical_replay(\n    tmp_path,\n):\n    recorder = (\n        ProductionIncidentEvidenceRecorder(\n            tmp_path\n        )\n    )\n\n    result = await recorder.record(\n        context(\n            TrustedToolManager()\n        )\n    )\n\n    source = (\n        RealIncidentReplaySource\n        .model_validate_json(\n            result.path.read_text(\n                encoding="utf-8"\n            )\n        )\n    )\n\n    environment = (\n        create_historical_replay_environment(\n            source,\n            start_at=NOW,\n        )\n    )\n\n    replay_context = (\n        SimpleNamespace(\n            tools=environment.tools\n        )\n    )\n\n    pod = await (\n        environment\n        .probe_executor\n        .collect(\n            replay_context,\n            scope(),\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        )\n    )\n\n    working = await (\n        environment\n        .probe_executor\n        .collect(\n            replay_context,\n            scope(),\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n        )\n    )\n\n    limit = await (\n        environment\n        .probe_executor\n        .collect(\n            replay_context,\n            scope(),\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        )\n    )\n\n    restarts = await (\n        environment\n        .probe_executor\n        .collect(\n            replay_context,\n            scope(),\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n        )\n    )\n\n    assert (\n        pod.facts[\n            "oom_killed"\n        ]\n        is True\n    )\n\n    assert (\n        pod.facts[\n            "max_restart_count"\n        ]\n        == 7\n    )\n\n    assert (\n        working.facts[\n            "value_sum"\n        ]\n        == 503316480.0\n    )\n\n    assert (\n        limit.facts[\n            "value_sum"\n        ]\n        == 536870912.0\n    )\n\n    assert (\n        restarts.facts[\n            "value_sum"\n        ]\n        == 7.0\n    )\n\n\n@pytest.mark.asyncio\nasync def test_duplicate_event_is_idempotent_and_does_not_probe_again(\n    tmp_path,\n):\n    tools = TrustedToolManager()\n\n    recorder = (\n        ProductionIncidentEvidenceRecorder(\n            tmp_path\n        )\n    )\n\n    first = await recorder.record(\n        context(\n            tools\n        )\n    )\n\n    call_count = len(\n        tools.calls\n    )\n\n    second = await recorder.record(\n        context(\n            tools\n        )\n    )\n\n    assert first.created is True\n    assert second.created is False\n    assert first.path == second.path\n\n    assert len(\n        tools.calls\n    ) == call_count\n\n\n@pytest.mark.asyncio\nasync def test_mock_or_dry_run_evidence_is_never_persisted(\n    tmp_path,\n):\n    recorder = (\n        ProductionIncidentEvidenceRecorder(\n            tmp_path\n        )\n    )\n\n    with pytest.raises(\n        ProductionIncidentEvidenceUnavailableError,\n        match=(\n            "No trusted production evidence"\n        ),\n    ):\n        await recorder.record(\n            context(\n                UntrustedToolManager()\n            )\n        )\n\n    assert list(\n        tmp_path.glob(\n            "*.json"\n        )\n    ) == []\n\n\n@pytest.mark.asyncio\nasync def test_ambiguous_event_scope_fails_before_any_probe(\n    tmp_path,\n):\n    tools = TrustedToolManager()\n\n    recorder = (\n        ProductionIncidentEvidenceRecorder(\n            tmp_path\n        )\n    )\n\n    with pytest.raises(\n        ProductionIncidentEvidenceScopeError,\n        match=(\n            "exactly one resource"\n        ),\n    ):\n        await recorder.record(\n            context(\n                tools,\n                resources=2,\n            )\n        )\n\n    assert tools.calls == []\n\n\ndef test_recorder_has_no_llm_or_action_authority():\n    import inspect\n\n    from services.agent_runtime.app.incident_evidence import (\n        recorder as module,\n    )\n\n    source = inspect.getsource(\n        module\n    )\n\n    forbidden = (\n        "create_llm_gateway",\n        "LLMInvestigationReasoner",\n        "ActionRuntime",\n        "ApprovalService",\n        "VerificationRuntime",\n        "pipeline.execute",\n        "kubernetes_patch",\n        "kubernetes_delete",\n    )\n\n    for token in forbidden:\n        assert token not in source\n'


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def find_repo_root(
    start: Path,
) -> Path:
    for candidate in (
        start,
        *start.parents,
    ):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate

    raise RuntimeError(
        "Repository root not found. "
        "Run this script from inside ai-reliability-platform."
    )


def write_text(
    path: Path,
    text: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        text.replace(
            "\r\n",
            "\n",
        ).replace(
            "\r",
            "\n",
        ),
        encoding="utf-8",
        newline="\n",
    )


def backup_file(
    path: Path,
) -> Path:
    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup = path.with_name(
        f"{path.name}.before_{VERSION}_{stamp}.bak"
    )

    shutil.copy2(
        path,
        backup,
    )

    return backup


def run_command(
    *,
    root: Path,
    name: str,
    command: list[str],
) -> CommandResult:
    process = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    return CommandResult(
        name=name,
        command=command,
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )


def section(
    lines: list[str],
    title: str,
) -> None:
    lines.extend(
        [
            "",
            "=" * 120,
            title,
            "=" * 120,
            "",
        ]
    )


def add_command(
    lines: list[str],
    result: CommandResult,
) -> None:
    section(
        lines,
        f"COMMAND: {result.name}",
    )

    lines.extend(
        [
            " ".join(
                result.command
            ),
            "",
            f"ExitCode: {result.returncode}",
            "",
            "STDOUT",
            "-" * 120,
            result.stdout.rstrip() or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            result.stderr.rstrip() or "<EMPTY>",
        ]
    )


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after = root / AFTER_NAME
    error = root / ERROR_NAME

    for output in (
        after,
        error,
    ):
        try:
            output.unlink()
        except FileNotFoundError:
            pass

    package_dir = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "incident_evidence"
    )

    recorder_file = (
        package_dir
        / "recorder.py"
    )

    init_file = (
        package_dir
        / "__init__.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_production_incident_evidence_recorder.py"
    )

    targets = (
        recorder_file,
        init_file,
        test_file,
    )

    backups = []
    preexisting = {
        path: path.exists()
        for path in targets
    }

    report = [
        "Production Incident Evidence Recorder v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Purpose:",
        "- preserve bounded trusted production Incident evidence",
        "- reuse ReadOnlyInvestigationProbeExecutor",
        "- write RealIncidentReplaySource directly",
        "- no Ground Truth / human Timeline in capture",
        "- no LLM / Action / Approval / Verification authority",
        "- idempotent by StandardEvent event_id",
        "- focused tests included",
    ]

    try:
        section(
            report,
            "BACKUP",
        )

        for target in targets:
            if target.exists():
                backup = backup_file(
                    target
                )

                backups.append(
                    (
                        target,
                        backup,
                    )
                )

                report.append(
                    "backup="
                    + str(
                        backup.relative_to(
                            root
                        )
                    )
                )

        write_text(
            recorder_file,
            RECORDER_SOURCE,
        )

        write_text(
            init_file,
            INIT_SOURCE,
        )

        write_text(
            test_file,
            TEST_SOURCE,
        )

        syntax = run_command(
            root=root,
            name="Python syntax",
            command=[
                "uv",
                "run",
                "python",
                "-m",
                "py_compile",
                str(
                    recorder_file.relative_to(
                        root
                    )
                ),
                str(
                    init_file.relative_to(
                        root
                    )
                ),
                str(
                    test_file.relative_to(
                        root
                    )
                ),
            ],
        )

        add_command(
            report,
            syntax,
        )

        if syntax.returncode != 0:
            raise RuntimeError(
                "Python syntax verification failed"
            )

        focused = run_command(
            root=root,
            name="Recorder focused tests",
            command=[
                "uv",
                "run",
                "pytest",
                str(
                    test_file.relative_to(
                        root
                    )
                ),
                "-q",
            ],
        )

        add_command(
            report,
            focused,
        )

        if focused.returncode != 0:
            raise RuntimeError(
                "Recorder focused tests failed"
            )

        compatibility = run_command(
            root=root,
            name=(
                "Investigation + Historical Replay "
                "compatibility tests"
            ),
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_readonly_tool_contract.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_historical_evidence_replay.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_real_incident_dataset.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            compatibility,
        )

        if compatibility.returncode != 0:
            raise RuntimeError(
                "Recorder compatibility tests failed"
            )

        diff = run_command(
            root=root,
            name="Git diff",
            command=[
                "git",
                "diff",
                "--",
                str(
                    recorder_file.relative_to(
                        root
                    )
                ),
                str(
                    init_file.relative_to(
                        root
                    )
                ),
                str(
                    test_file.relative_to(
                        root
                    )
                ),
            ],
        )

        add_command(
            report,
            diff,
        )

        section(
            report,
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "Recorder core is installed.",
                "",
                "Capture contract:",
                "StandardEvent / AgentContext",
                "-> bounded read-only Probe executor",
                "-> trusted production evidence only",
                "-> RealIncidentReplaySource",
                "-> *.replay.json",
                "",
                "No existing Runtime/Gateway file was modified in v1.",
                "",
                "Next stage:",
                "wire this recorder into the production event path behind "
                "an explicit disabled-default feature flag.",
            ]
        )

        write_text(
            after,
            "\n".join(
                report
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "PRODUCTION INCIDENT EVIDENCE RECORDER V1 PASSED"
        )
        print("=" * 72)
        print("")
        print(
            "Recorder core installed with focused tests."
        )
        print("")
        print(
            "Upload:"
        )
        print(
            after
        )

        return 0

    except Exception as exc:
        rollback = []

        for original, backup in reversed(
            backups
        ):
            try:
                shutil.copy2(
                    backup,
                    original,
                )

                rollback.append(
                    "RESTORED "
                    + str(
                        original.relative_to(
                            root
                        )
                    )
                )
            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK FAILED "
                    + str(
                        original.relative_to(
                            root
                        )
                    )
                    + ": "
                    + f"{type(rollback_exc).__name__}: {rollback_exc}"
                )

        for target in targets:
            if (
                not preexisting[
                    target
                ]
                and target.exists()
            ):
                try:
                    target.unlink()

                    rollback.append(
                        "REMOVED newly-created "
                        + str(
                            target.relative_to(
                                root
                            )
                        )
                    )
                except Exception as rollback_exc:
                    rollback.append(
                        "ROLLBACK FAILED removing "
                        + str(
                            target.relative_to(
                                root
                            )
                        )
                        + ": "
                        + f"{type(rollback_exc).__name__}: {rollback_exc}"
                    )

        error_lines = [
            "Production Incident Evidence Recorder v1 FAILED",
            f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
            "",
            "Exception:",
            f"{type(exc).__name__}: {exc}",
            "",
            "Traceback:",
            traceback.format_exc(),
            "",
            "ROLLBACK",
            "=" * 120,
            *rollback,
            "",
            "PARTIAL REPORT",
            "=" * 120,
            *report,
        ]

        write_text(
            error,
            "\n".join(
                error_lines
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "PRODUCTION INCIDENT EVIDENCE RECORDER V1 FAILED"
        )
        print("=" * 72)
        print("")
        print(
            "Modified/new files were rolled back where possible."
        )
        print("")
        print(
            "Upload:"
        )
        print(
            error
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
