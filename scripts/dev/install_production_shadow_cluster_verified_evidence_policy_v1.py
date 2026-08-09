from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "production-shadow-cluster-verified-evidence-policy-v1"

AFTER_NAME = (
    "production_shadow_cluster_verified_evidence_policy_v1_after.txt"
)

ERROR_NAME = (
    "production_shadow_cluster_verified_evidence_policy_v1_error.txt"
)

EXPECTED_RAW_HASHES = {'services/agent_runtime/app/investigation/coordinator.py': '89d70861ad64c22b45989363b39a7853daf79bac64d3c46f001afd32d86549ae', 'services/agent_runtime/app/verification/collector.py': '4b7439097f7f81b7f552f8a6c2315b4804ff9cd72e0b97cf1c445c1f4fa7a5ec', 'services/agent_runtime/app/runtime/runtime.py': 'a56bed9b207629cdca1b612861b78bf8233667eb4d424ed074fc1db0089cac8d'}

COORDINATOR_SOURCE = 'import asyncio\nimport time\nfrom datetime import UTC, datetime\n\nfrom services.agent_runtime.app.investigation.epistemic_guard import (\n    EpistemicConclusionGuard,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    InvestigationLimits,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n    default_investigation_probes,\n    InvestigationStatus,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n)\n\n\nclass EvidenceDrivenInvestigationCoordinator:\n    """\n    Run one bounded, read-only, Shadow evidence investigation.\n\n    This coordinator is deliberately independent from PlannerPipeline and\n    ActionRuntime in v1. Calling it writes only a bounded JSON snapshot to\n    context.metadata["investigation_shadow"]. It never writes variables,\n    Incident state, Approval, Action, Verification, budget or Kubernetes.\n    """\n\n    def __init__(\n        self,\n        reasoner: BaseInvestigationReasoner,\n        probe_executor,\n        limits: InvestigationLimits | None = None,\n        monotonic_clock=None,\n        utc_clock=None,\n        require_cluster_verified_evidence: bool = False,\n    ) -> None:\n        if not isinstance(\n            reasoner,\n            BaseInvestigationReasoner,\n        ):\n            raise TypeError(\n                "Investigation reasoner is invalid"\n            )\n\n        if probe_executor is None or not callable(\n            getattr(probe_executor, "collect", None)\n        ):\n            raise TypeError(\n                "Investigation probe executor is invalid"\n            )\n\n        if not isinstance(\n            require_cluster_verified_evidence,\n            bool,\n        ):\n            raise TypeError(\n                "Investigation cluster-verified evidence policy is invalid"\n            )\n\n        self.reasoner = reasoner\n        self.probe_executor = probe_executor\n        self.limits = limits or InvestigationLimits()\n        self.require_cluster_verified_evidence = (\n            require_cluster_verified_evidence\n        )\n        self._monotonic = monotonic_clock or time.monotonic\n        self._utc_clock = utc_clock or (\n            lambda: datetime.now(UTC)\n        )\n\n    async def investigate(\n        self,\n        context,\n    ) -> InvestigationState:\n        scope = self._scope_from_context(\n            context\n        )\n        started_at = self._now()\n        started_monotonic = self._monotonic()\n\n        state = InvestigationState(\n            status=InvestigationStatus.RUNNING,\n            scope=scope,\n            limits=self.limits,\n            started_at=started_at,\n            updated_at=started_at,\n            available_probes=self._available_probes(\n                context\n            ),\n        )\n\n        while state.status == InvestigationStatus.RUNNING:\n            if state.iteration_count >= self.limits.max_iterations:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.EXHAUSTED,\n                    reason=InvestigationStopReason.MAX_ITERATIONS,\n                )\n                break\n\n            remaining = self._remaining_seconds(\n                started_monotonic\n            )\n            if remaining <= 0:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.EXHAUSTED,\n                    reason=InvestigationStopReason.TIMEOUT,\n                )\n                break\n\n            try:\n                decision = await asyncio.wait_for(\n                    self.reasoner.decide(\n                        scope,\n                        state.model_copy(deep=True),\n                    ),\n                    timeout=remaining,\n                )\n            except TimeoutError:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.EXHAUSTED,\n                    reason=InvestigationStopReason.TIMEOUT,\n                )\n                break\n            except Exception as exc:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.FAILED,\n                    reason=InvestigationStopReason.REASONER_ERROR,\n                    failure_code=type(exc).__name__,\n                )\n                break\n\n            if not self._evidence_references_are_valid(\n                decision=decision,\n                state=state,\n            ):\n                self._stop(\n                    state,\n                    status=InvestigationStatus.FAILED,\n                    reason=InvestigationStopReason.REASONER_ERROR,\n                    failure_code="InvalidEvidenceReference",\n                )\n                break\n\n            state.iteration_count += 1\n            state.hypotheses = [\n                item.model_copy(deep=True)\n                for item in decision.hypotheses\n            ]\n            state.decision_summaries.append(\n                decision.rationale_summary\n            )\n            state.updated_at = self._now()\n\n            if decision.stop:\n                guard_result = (\n                    EpistemicConclusionGuard()\n                    .evaluate(\n                        decision=decision,\n                        state=state,\n                    )\n                )\n\n                if not guard_result.allowed:\n                    state.epistemic_guard_code = (\n                        guard_result.code\n                    )\n\n                    self._stop(\n                        state,\n                        status=InvestigationStatus.CONCLUDED,\n                        reason=(\n                            InvestigationStopReason\n                            .INSUFFICIENT_EVIDENCE\n                        ),\n                    )\n\n                    state.conclusion = None\n                    break\n\n                self._stop(\n                    state,\n                    status=InvestigationStatus.CONCLUDED,\n                    reason=decision.stop_reason,\n                )\n                state.conclusion = decision.conclusion\n                break\n\n            probe = decision.next_probe\n            if probe is None:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.EXHAUSTED,\n                    reason=InvestigationStopReason.NO_SAFE_PROBE,\n                )\n                break\n\n            if probe in state.attempted_probes:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.EXHAUSTED,\n                    reason=InvestigationStopReason.DUPLICATE_PROBE,\n                )\n                break\n\n            if state.tool_call_count >= self.limits.max_tool_calls:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.EXHAUSTED,\n                    reason=InvestigationStopReason.MAX_TOOL_CALLS,\n                )\n                break\n\n            remaining = self._remaining_seconds(\n                started_monotonic\n            )\n            if remaining <= 0:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.EXHAUSTED,\n                    reason=InvestigationStopReason.TIMEOUT,\n                )\n                break\n\n            state.attempted_probes.append(probe)\n            state.tool_call_count += 1\n\n            try:\n                evidence = await asyncio.wait_for(\n                    self.probe_executor.collect(\n                        context,\n                        scope,\n                        probe,\n                    ),\n                    timeout=remaining,\n                )\n\n                rejection_code = (\n                    self._cluster_evidence_rejection_code(\n                        scope=scope,\n                        evidence=evidence,\n                    )\n                )\n\n                if rejection_code is not None:\n                    evidence = EvidenceItem(\n                        probe=probe,\n                        source="investigation_probe",\n                        success=False,\n                        trusted=False,\n                        production_signal=False,\n                        reliability=0.0,\n                        observed_at=self._now(),\n                        facts={},\n                        error_code=rejection_code,\n                    )\n\n            except TimeoutError:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.EXHAUSTED,\n                    reason=InvestigationStopReason.TIMEOUT,\n                )\n                break\n            except Exception as exc:\n                evidence = EvidenceItem(\n                    probe=probe,\n                    source="investigation_probe",\n                    success=False,\n                    trusted=False,\n                    production_signal=False,\n                    reliability=0.0,\n                    observed_at=self._now(),\n                    facts={},\n                    error_code=type(exc).__name__[:256],\n                )\n\n            state.evidence.append(evidence)\n            state.updated_at = self._now()\n\n        self._publish_shadow_snapshot(\n            context=context,\n            state=state,\n        )\n        return state\n\n    @staticmethod\n    def _scope_from_context(\n        context,\n    ) -> InvestigationScope:\n        event = getattr(\n            context,\n            "event",\n            None,\n        )\n        signal = getattr(\n            event,\n            "signal",\n            None,\n        )\n        resources = getattr(\n            event,\n            "resources",\n            None,\n        )\n\n        if signal is None or not resources:\n            raise ValueError(\n                "Investigation requires one event resource"\n            )\n\n        resource = resources[0]\n\n        header = getattr(\n            event,\n            "header",\n            None,\n        )\n\n        event_occurred_at = getattr(\n            header,\n            "occurred_at",\n            None,\n        )\n\n        if event_occurred_at is not None:\n            if (\n                not isinstance(\n                    event_occurred_at,\n                    datetime,\n                )\n                or event_occurred_at.tzinfo is None\n            ):\n                raise ValueError(\n                    "Investigation event occurred_at "\n                    "must be timezone-aware"\n                )\n\n            event_occurred_at = (\n                event_occurred_at.astimezone(\n                    UTC\n                )\n            )\n\n        return InvestigationScope(\n            alert_name=str(\n                getattr(signal, "name", "")\n            ),\n            alert_message=str(\n                getattr(signal, "message", "")\n                or ""\n            ),\n            event_occurred_at=event_occurred_at,\n            resource=str(\n                getattr(resource, "name", "")\n            ),\n            namespace=str(\n                getattr(resource, "namespace", None)\n                or "default"\n            ),\n            cluster=(\n                str(getattr(resource, "cluster"))\n                if getattr(resource, "cluster", None)\n                else None\n            ),\n        )\n\n    def _available_probes(\n        self,\n        context,\n    ) -> list[InvestigationProbe]:\n        resolver = getattr(\n            self.probe_executor,\n            "available_probes",\n            None,\n        )\n\n        if not callable(\n            resolver\n        ):\n            return default_investigation_probes()\n\n        resolved = resolver(\n            context\n        )\n\n        if not isinstance(\n            resolved,\n            (\n                list,\n                tuple,\n            ),\n        ):\n            raise TypeError(\n                "Investigation available probes are invalid"\n            )\n\n        normalized: list[\n            InvestigationProbe\n        ] = []\n\n        for item in resolved:\n            if not isinstance(\n                item,\n                InvestigationProbe,\n            ):\n                raise TypeError(\n                    "Investigation available probe is invalid"\n                )\n\n            if item not in normalized:\n                normalized.append(\n                    item\n                )\n\n        if not normalized:\n            raise ValueError(\n                "Investigation requires at least one available probe"\n            )\n\n        return normalized\n\n    def _remaining_seconds(\n        self,\n        started_monotonic: float,\n    ) -> float:\n        return (\n            self.limits.timeout_seconds\n            - (\n                self._monotonic()\n                - started_monotonic\n            )\n        )\n\n    def _stop(\n        self,\n        state: InvestigationState,\n        status: InvestigationStatus,\n        reason: InvestigationStopReason | None,\n        failure_code: str | None = None,\n    ) -> None:\n        state.status = status\n        state.stop_reason = reason\n        state.failure_code = failure_code\n        state.updated_at = self._now()\n\n    def _now(self) -> datetime:\n        value = self._utc_clock()\n        if value.tzinfo is None:\n            return value.replace(tzinfo=UTC)\n        return value.astimezone(UTC)\n\n    def _cluster_evidence_rejection_code(\n        self,\n        *,\n        scope: InvestigationScope,\n        evidence: EvidenceItem,\n    ) -> str | None:\n        """\n        Return a sanitized rejection code for evidence cluster provenance.\n\n        Explicit mismatch always fails.\n\n        In Production Shadow strict mode, successful trusted production\n        evidence must also carry cluster_verified=True. This turns the legacy\n        identity-less compatibility path into fail-closed behavior whenever\n        Runtime has activated multi-cluster read connections.\n        """\n\n        if not self._evidence_cluster_is_consistent(\n            scope=scope,\n            evidence=evidence,\n        ):\n            return "ClusterEvidenceMismatch"\n\n        if (\n            self.require_cluster_verified_evidence\n            and isinstance(\n                evidence,\n                EvidenceItem,\n            )\n            and evidence.success\n            and evidence.trusted\n            and evidence.production_signal\n            and not evidence.cluster_verified\n        ):\n            return "ClusterVerificationRequired"\n\n        return None\n\n    @staticmethod\n    def _evidence_cluster_is_consistent(\n        *,\n        scope: InvestigationScope,\n        evidence: EvidenceItem,\n    ) -> bool:\n        """\n        Defense in depth for custom/replay ProbeExecutors.\n\n        Identity-less legacy evidence remains compatible. Any explicit\n        provider-reported cluster that conflicts with trusted Incident scope\n        is replaced by a failed, fact-free EvidenceItem before Reasoner sees\n        its facts.\n        """\n\n        if not isinstance(\n            evidence,\n            EvidenceItem,\n        ):\n            return False\n\n        if evidence.cluster is None:\n            return not evidence.cluster_verified\n\n        if scope.cluster is None:\n            return not evidence.cluster_verified\n\n        return (\n            evidence.cluster\n            == scope.cluster\n        )\n\n    @staticmethod\n    def _publish_shadow_snapshot(\n        context,\n        state: InvestigationState,\n    ) -> None:\n        metadata = getattr(\n            context,\n            "metadata",\n            None,\n        )\n\n        if not isinstance(metadata, dict):\n            raise TypeError(\n                "Investigation context metadata is unavailable"\n            )\n\n        metadata["investigation_shadow"] = (\n            state.model_dump(mode="json")\n        )\n\n    @staticmethod\n    def _evidence_references_are_valid(\n        decision,\n        state: InvestigationState,\n    ) -> bool:\n        known_ids = {\n            item.evidence_id\n            for item in state.evidence\n        }\n\n        for hypothesis in decision.hypotheses:\n            referenced_ids = set(\n                hypothesis.supporting_evidence_ids\n            ) | set(\n                hypothesis.conflicting_evidence_ids\n            )\n\n            if not referenced_ids.issubset(\n                known_ids\n            ):\n                return False\n\n        conclusion = decision.conclusion\n\n        if conclusion is None:\n            return True\n\n        conclusion_ids = set(\n            conclusion.evidence_ids\n        )\n        if not conclusion_ids.issubset(\n            known_ids\n        ):\n            return False\n\n        trusted_ids = {\n            item.evidence_id\n            for item in state.evidence\n            if item.trusted\n        }\n\n        return (\n            bool(conclusion_ids)\n            and conclusion_ids.issubset(\n                trusted_ids\n            )\n        )\n'
COLLECTOR_SOURCE = 'from collections.abc import Callable, Mapping\nfrom dataclasses import dataclass, field\nfrom datetime import UTC, datetime, timedelta\nfrom typing import Any\n\nfrom services.agent_runtime.app.tools.manager import (\n    ToolManager,\n)\nfrom services.agent_runtime.app.verification.models import (\n    VerificationCheck,\n    VerificationSource,\n)\n\n\n@dataclass(\n    frozen=True,\n    slots=True,\n)\nclass VerificationEvaluation:\n    """\n    Result produced after trusted evidence is evaluated.\n    """\n\n    passed: bool | None\n    observed_value: Any = None\n    expected_value: Any = None\n    message: str = ""\n    metadata: Mapping[str, Any] = field(\n        default_factory=dict\n    )\n\n\nEvidenceEvaluator = Callable[\n    [Mapping[str, Any]],\n    VerificationEvaluation,\n]\n\n\n@dataclass(\n    frozen=True,\n    slots=True,\n)\nclass VerificationProbe:\n    """\n    Definition of one read-only verification probe.\n\n    The tool response must use this evidence envelope:\n    - success: true\n    - source: expected provider name\n    - mode: live / production / read_only\n    - production_signal: true\n    - observed_at: timezone-aware datetime or ISO-8601 text\n    """\n\n    name: str\n    source: VerificationSource\n    tool: str\n    provider: str\n    arguments: Mapping[str, Any]\n    evaluator: EvidenceEvaluator\n    required: bool = True\n\n\nclass VerificationEvidenceCollector:\n    """\n    Collect and evaluate production verification evidence.\n\n    This class is fail-closed:\n    untrusted, stale, malformed, or unavailable evidence produces\n    an inconclusive VerificationCheck instead of a passing check.\n    """\n\n    _TRUSTED_MODES = {\n        "live",\n        "production",\n        "read_only",\n    }\n\n    _UNTRUSTED_MARKERS = {\n        "dry_run",\n        "fake",\n        "mock",\n        "simulated",\n        "simulation",\n        "test",\n    }\n\n    def __init__(\n        self,\n        tools: ToolManager,\n        max_evidence_age: timedelta = timedelta(\n            minutes=5\n        ),\n        max_future_skew: timedelta = timedelta(\n            seconds=30\n        ),\n        clock: Callable[[], datetime] | None = None,\n        require_cluster_verified_evidence: bool = False,\n    ) -> None:\n        if max_evidence_age <= timedelta(0):\n            raise ValueError(\n                "max_evidence_age must be positive"\n            )\n\n        if max_future_skew < timedelta(0):\n            raise ValueError(\n                "max_future_skew cannot be negative"\n            )\n\n        if not isinstance(\n            require_cluster_verified_evidence,\n            bool,\n        ):\n            raise TypeError(\n                "Verification cluster-verified evidence policy is invalid"\n            )\n\n        self.tools = tools\n        self.max_evidence_age = max_evidence_age\n        self.max_future_skew = max_future_skew\n        self.require_cluster_verified_evidence = (\n            require_cluster_verified_evidence\n        )\n        self._clock = clock or (\n            lambda: datetime.now(UTC)\n        )\n\n    async def collect(\n        self,\n        probes: list[VerificationProbe],\n        context=None,\n    ) -> list[VerificationCheck]:\n        """\n        Run probes sequentially to avoid verification load spikes.\n        """\n\n        checks: list[VerificationCheck] = []\n\n        for probe in probes:\n            check = await self.collect_one(\n                probe,\n                context=context,\n            )\n            checks.append(check)\n\n        return checks\n\n    async def collect_one(\n        self,\n        probe: VerificationProbe,\n        context=None,\n    ) -> VerificationCheck:\n        checked_at = self._now()\n\n        try:\n            result = await self.tools.call(\n                probe.tool,\n                context=context,\n                **dict(probe.arguments),\n            )\n        except Exception as exc:\n            return self._inconclusive_check(\n                probe=probe,\n                checked_at=checked_at,\n                message=(\n                    "Verification evidence collection failed"\n                ),\n                metadata={\n                    "error_type": type(exc).__name__,\n                    "error": str(exc),\n                },\n            )\n\n        if not isinstance(result, Mapping):\n            return self._inconclusive_check(\n                probe=probe,\n                checked_at=checked_at,\n                message=(\n                    "Verification evidence was rejected"\n                ),\n                metadata={\n                    "rejection_reasons": [\n                        "tool result is not a mapping"\n                    ]\n                },\n            )\n\n        (\n            rejection_reasons,\n            observed_at,\n            evidence_cluster,\n            cluster_verified,\n        ) = self._validate_evidence(\n            probe=probe,\n            evidence=result,\n            now=checked_at,\n        )\n\n        if rejection_reasons:\n            return self._inconclusive_check(\n                probe=probe,\n                checked_at=checked_at,\n                message=(\n                    "Verification evidence was rejected"\n                ),\n                metadata={\n                    "rejection_reasons": (\n                        rejection_reasons\n                    )\n                },\n            )\n\n        try:\n            evaluation = probe.evaluator(\n                result\n            )\n        except Exception as exc:\n            return self._inconclusive_check(\n                probe=probe,\n                checked_at=checked_at,\n                message=(\n                    "Verification evidence evaluation failed"\n                ),\n                metadata={\n                    "error_type": type(exc).__name__,\n                    "error": str(exc),\n                },\n            )\n\n        if not isinstance(\n            evaluation,\n            VerificationEvaluation,\n        ):\n            return self._inconclusive_check(\n                probe=probe,\n                checked_at=checked_at,\n                message=(\n                    "Verification evaluator returned "\n                    "an invalid result"\n                ),\n            )\n\n        metadata = dict(\n            evaluation.metadata\n        )\n        metadata.update(\n            {\n                "trusted": True,\n                "tool": probe.tool,\n                "provider": probe.provider,\n                "evidence_observed_at": (\n                    observed_at.isoformat()\n                    if observed_at\n                    else None\n                ),\n                "evidence_cluster": (\n                    evidence_cluster\n                ),\n                "cluster_verified": (\n                    cluster_verified\n                ),\n            }\n        )\n\n        return VerificationCheck(\n            name=probe.name,\n            source=probe.source,\n            passed=evaluation.passed,\n            required=probe.required,\n            observed_value=(\n                evaluation.observed_value\n            ),\n            expected_value=(\n                evaluation.expected_value\n            ),\n            message=evaluation.message,\n            checked_at=checked_at,\n            metadata=metadata,\n        )\n\n    def _validate_evidence(\n        self,\n        probe: VerificationProbe,\n        evidence: Mapping[str, Any],\n        now: datetime,\n    ) -> tuple[\n        list[str],\n        datetime | None,\n        str | None,\n        bool,\n    ]:\n        reasons: list[str] = []\n        evidence_cluster: str | None = None\n        cluster_verified = False\n\n        if evidence.get("success") is not True:\n            reasons.append(\n                "success is not true"\n            )\n\n        provider = str(\n            evidence.get(\n                "source",\n                "",\n            )\n        ).strip().lower()\n\n        expected_provider = (\n            probe.provider.strip().lower()\n        )\n\n        if provider != expected_provider:\n            reasons.append(\n                "source does not match expected provider"\n            )\n\n        mode = str(\n            evidence.get(\n                "mode",\n                "",\n            )\n        ).strip().lower()\n\n        if mode not in self._TRUSTED_MODES:\n            reasons.append(\n                "mode is not trusted"\n            )\n\n        identity_text = (\n            f"{provider} {mode}"\n        ).lower()\n\n        if any(\n            marker in identity_text\n            for marker in self._UNTRUSTED_MARKERS\n        ):\n            reasons.append(\n                "mock, test, or simulated evidence "\n                "is not allowed"\n            )\n\n        if (\n            evidence.get(\n                "production_signal"\n            )\n            is not True\n        ):\n            reasons.append(\n                "production_signal is not true"\n            )\n\n        expected_cluster_value = (\n            probe.arguments.get(\n                "cluster"\n            )\n        )\n\n        expected_cluster = (\n            str(\n                expected_cluster_value\n            ).strip()\n            if expected_cluster_value\n            is not None\n            else None\n        )\n\n        reported_cluster_value = (\n            evidence.get(\n                "cluster"\n            )\n        )\n\n        if reported_cluster_value is not None:\n            if not isinstance(\n                reported_cluster_value,\n                str,\n            ):\n                reasons.append(\n                    "cluster identity is invalid"\n                )\n\n            else:\n                reported_cluster = (\n                    reported_cluster_value.strip()\n                )\n\n                if (\n                    not reported_cluster\n                    or reported_cluster\n                    != reported_cluster_value\n                    or len(\n                        reported_cluster\n                    )\n                    > 256\n                    or "\\x00"\n                    in reported_cluster\n                ):\n                    reasons.append(\n                        "cluster identity is invalid"\n                    )\n\n                else:\n                    evidence_cluster = (\n                        reported_cluster\n                    )\n\n                    if expected_cluster:\n                        if (\n                            evidence_cluster\n                            != expected_cluster\n                        ):\n                            reasons.append(\n                                "cluster does not match expected scope"\n                            )\n\n                        else:\n                            cluster_verified = True\n\n        if (\n            self.require_cluster_verified_evidence\n            and probe.required\n            and not cluster_verified\n        ):\n            reasons.append(\n                "cluster verification is required"\n            )\n\n        observed_at = self._parse_datetime(\n            evidence.get(\n                "observed_at"\n            )\n        )\n\n        if observed_at is None:\n            reasons.append(\n                "observed_at is missing or invalid"\n            )\n            return (\n                reasons,\n                None,\n                evidence_cluster,\n                cluster_verified,\n            )\n\n        age = now - observed_at\n\n        if age > self.max_evidence_age:\n            reasons.append(\n                "evidence is stale"\n            )\n\n        if age < -self.max_future_skew:\n            reasons.append(\n                "observed_at is too far in the future"\n            )\n\n        return (\n            reasons,\n            observed_at,\n            evidence_cluster,\n            cluster_verified,\n        )\n\n    def _inconclusive_check(\n        self,\n        probe: VerificationProbe,\n        checked_at: datetime,\n        message: str,\n        metadata: Mapping[str, Any] | None = None,\n    ) -> VerificationCheck:\n        check_metadata = dict(\n            metadata or {}\n        )\n        check_metadata.update(\n            {\n                "trusted": False,\n                "tool": probe.tool,\n                "provider": probe.provider,\n            }\n        )\n\n        return VerificationCheck(\n            name=probe.name,\n            source=probe.source,\n            passed=None,\n            required=probe.required,\n            message=message,\n            checked_at=checked_at,\n            metadata=check_metadata,\n        )\n\n    def _now(\n        self,\n    ) -> datetime:\n        value = self._clock()\n\n        if value.tzinfo is None:\n            raise ValueError(\n                "clock must return a timezone-aware datetime"\n            )\n\n        return value.astimezone(\n            UTC\n        )\n\n    @staticmethod\n    def _parse_datetime(\n        value: Any,\n    ) -> datetime | None:\n        if isinstance(\n            value,\n            datetime,\n        ):\n            parsed = value\n        elif isinstance(\n            value,\n            str,\n        ):\n            text = value.strip()\n\n            if text.endswith("Z"):\n                text = (\n                    f"{text[:-1]}+00:00"\n                )\n\n            try:\n                parsed = datetime.fromisoformat(\n                    text\n                )\n            except ValueError:\n                return None\n        else:\n            return None\n\n        if parsed.tzinfo is None:\n            return None\n\n        return parsed.astimezone(\n            UTC\n        )\n'
RUNTIME_SOURCE = 'from copy import deepcopy\n\nfrom services.agent_runtime.app.registry.factory import (\n    create_agent_registry,\n)\nfrom services.agent_runtime.app.llm.gateway.factory import (\n    create_llm_gateway,\n)\nfrom services.agent_runtime.app.llm.gateway.gateway import (\n    LLMGateway,\n)\nfrom services.agent_runtime.app.planner.agent_planner import (\n    AgentPlanner,\n)\nfrom services.agent_runtime.app.pipeline.planner_pipeline import (\n    PlannerPipeline,\n)\nfrom services.agent_runtime.app.memory.store import (\n    MemoryStore,\n)\nfrom services.agent_runtime.app.tools.factory import (\n    create_tool_manager,\n)\nfrom services.agent_runtime.app.tools.kubernetes.router import (\n    KubernetesClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.kubernetes.connection_factory import (\n    create_kubernetes_cluster_registry,\n)\nfrom services.agent_runtime.app.tools.prometheus.router import (\n    PrometheusClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.prometheus.connection_factory import (\n    create_prometheus_cluster_registry,\n)\nfrom services.agent_runtime.app.skills.factory import (\n    create_skill_registry,\n)\nfrom services.agent_runtime.app.mcp.factory import (\n    create_mcp_registry,\n)\nfrom services.agent_runtime.app.observability.collector import (\n    TraceCollector,\n)\nfrom services.agent_runtime.app.evaluation.factory import (\n    create_evaluation_registry,\n)\nfrom services.agent_runtime.app.policy.factory import (\n    create_policy_engine,\n)\nfrom services.agent_runtime.app.approval.service import (\n    ApprovalService,\n)\nfrom services.agent_runtime.app.incident.store import (\n    IncidentStore,\n)\nfrom services.agent_runtime.app.incident.service import (\n    IncidentService,\n)\nfrom services.agent_runtime.app.investigation.comparison import (\n    build_rca_investigation_comparison,\n)\nfrom services.agent_runtime.app.investigation.factory import (\n    create_investigation_coordinator,\n)\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    InvestigationLLMGatewayAdapter,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n    LLMInvestigationReasoner,\n)\nfrom services.agent_runtime.app.investigation.settings import (\n    InvestigationSettings,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationState,\n)\nfrom services.agent_runtime.app.model.context import (\n    AgentContext,\n)\nfrom services.agent_runtime.app.workflow.service import (\n    WorkflowService,\n)\nfrom services.agent_runtime.app.action.execution_service import (\n    ActionExecutionService,\n)\nfrom services.agent_runtime.app.action.execution_store import (\n    ActionExecutionStore,\n)\nfrom services.agent_runtime.app.action.kubernetes_preflight import (\n    KubernetesPreflightResolver,\n)\nfrom services.agent_runtime.app.action.kubernetes_preflight_factory import (\n    create_kubernetes_preflight_resolver,\n)\nfrom services.agent_runtime.app.action.kubernetes_production_executor import (\n    KubernetesProductionExecutor,\n)\nfrom services.agent_runtime.app.action.kubernetes_production_factory import (\n    create_kubernetes_production_executor,\n)\nfrom services.agent_runtime.app.action.preflight_artifact_service import (\n    PreflightArtifactService,\n)\nfrom services.agent_runtime.app.action.preflight_artifact_store import (\n    PreflightArtifactStore,\n)\nfrom services.agent_runtime.app.action.production_action_preparation import (\n    ProductionActionPreparationService,\n)\nfrom services.agent_runtime.app.action.production_action_query import (\n    ProductionActionQueryService,\n)\nfrom services.agent_runtime.app.action.production_action_guard import (\n    ProductionActionExpiryGuard,\n)\nfrom services.agent_runtime.app.action.production_pilot import (\n    KubernetesProductionPilotControl,\n    ProductionPilotReadinessService,\n)\nfrom services.agent_runtime.app.action.production_pilot_factory import (\n    create_kubernetes_production_pilot_control,\n)\nfrom services.agent_runtime.app.action.production_pilot_budget_service import (\n    ProductionPilotBudgetService,\n)\nfrom services.agent_runtime.app.action.production_pilot_budget_store import (\n    ProductionPilotBudgetStore,\n)\nfrom services.agent_runtime.app.action.production_pilot_rehearsal import (\n    ProductionPilotRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_crash_rehearsal import (\n    ProductionPilotCrashRecoveryRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_pre_enable_evidence import (\n    ProductionPilotPreEnableEvidenceService,\n)\nfrom services.agent_runtime.app.action.production_pilot_final_handoff import (\n    ProductionPilotFinalHandoffRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_live_probe import (\n    ProductionPilotLiveReadinessProbe,\n    create_production_pilot_live_readiness_probe,\n)\nfrom services.agent_runtime.app.action.production_pilot_go_no_go_service import (\n    ProductionPilotGoNoGoService,\n)\nfrom services.agent_runtime.app.action.production_pilot_go_no_go_store import (\n    ProductionPilotGoNoGoStore,\n)\nfrom services.agent_runtime.app.action.production_pilot_ceremony_service import (\n    ProductionPilotCeremonyService,\n)\nfrom services.agent_runtime.app.action.production_pilot_ceremony_store import (\n    ProductionPilotCeremonyStore,\n)\nfrom services.agent_runtime.app.verification.collector import (\n    VerificationEvidenceCollector,\n)\nfrom services.agent_runtime.app.verification.coordinator import (\n    VerificationCoordinator,\n)\nfrom services.agent_runtime.app.verification.profiles import (\n    VerificationProfileFactory,\n)\nfrom services.agent_runtime.app.verification.service import (\n    VerificationService,\n)\nfrom services.agent_runtime.app.verification.store import (\n    VerificationStore,\n)\nfrom services.agent_runtime.app.runtime.action_runtime import (\n    ActionRuntime,\n)\nfrom services.agent_runtime.app.runtime.verification_runtime import (\n    VerificationRuntime,\n)\nfrom services.agent_runtime.app.security.factory import (\n    create_authentication_service,\n)\nfrom services.agent_runtime.app.security.policy import (\n    SecurityPolicyEngine,\n)\nfrom services.agent_runtime.app.security.service import (\n    AuthenticationService,\n)\nfrom services.sandbox.executor.local import (\n    LocalSandboxExecutor,\n)\nfrom services.sandbox.policy.validator import (\n    SandboxPolicyValidator,\n)\n\n\nfrom services.agent_runtime.app.incident_evidence.recorder import (\n    ProductionIncidentEvidenceRecorder,\n)\nfrom services.agent_runtime.app.incident_evidence.settings import (\n    IncidentEvidenceRecorderSettings,\n)\n\nclass AgentRuntime:\n    """\n    Runtime container.\n\n    Owns and shares security and runtime infrastructure\n    across Pipeline, Action and Verification.\n\n    security_policy is the RBAC authorization policy. The existing policy\n    attribute remains the remediation business policy engine.\n    """\n\n    def __init__(\n        self,\n        authentication_service: (\n            AuthenticationService | None\n        ) = None,\n        security_policy: (\n            SecurityPolicyEngine | None\n        ) = None,\n        kubernetes_preflight: (\n            KubernetesPreflightResolver | None\n        ) = None,\n        kubernetes_production_executor: (\n            KubernetesProductionExecutor | None\n        ) = None,\n        production_pilot_control: (\n            KubernetesProductionPilotControl | None\n        ) = None,\n        production_pilot_budget_service: (\n            ProductionPilotBudgetService | None\n        ) = None,\n        production_pilot_live_probe: (\n            ProductionPilotLiveReadinessProbe | None\n        ) = None,\n        kubernetes_cluster_registry: (\n            KubernetesClusterRegistry | None\n        ) = None,\n        prometheus_cluster_registry: (\n            PrometheusClusterRegistry | None\n        ) = None,\n        llm_gateway: (\n            LLMGateway | None\n        ) = None,\n        investigation_reasoner: (\n            BaseInvestigationReasoner | None\n        ) = None,\n        investigation_settings: (\n            InvestigationSettings | None\n        ) = None,\n    ) -> None:\n        # Validate every injected security component before factories, stores\n        # or other runtime components can produce side effects.\n        if (\n            authentication_service is not None\n            and not isinstance(\n                authentication_service,\n                AuthenticationService,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime authentication service is invalid"\n            )\n\n        if (\n            security_policy is not None\n            and not isinstance(\n                security_policy,\n                SecurityPolicyEngine,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime security policy is invalid"\n            )\n\n        if (\n            kubernetes_preflight is not None\n            and not isinstance(\n                kubernetes_preflight,\n                KubernetesPreflightResolver,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes preflight resolver is invalid"\n            )\n\n        if (\n            kubernetes_production_executor is not None\n            and not isinstance(\n                kubernetes_production_executor,\n                KubernetesProductionExecutor,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production executor is invalid"\n            )\n\n        if (\n            production_pilot_control is not None\n            and not isinstance(\n                production_pilot_control,\n                KubernetesProductionPilotControl,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production pilot control is invalid"\n            )\n\n        if (\n            production_pilot_budget_service is not None\n            and not isinstance(\n                production_pilot_budget_service,\n                ProductionPilotBudgetService,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production pilot budget service is invalid"\n            )\n\n        if (\n            production_pilot_live_probe is not None\n            and not isinstance(\n                production_pilot_live_probe,\n                ProductionPilotLiveReadinessProbe,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Production Pilot live probe is invalid"\n            )\n\n        if (\n            kubernetes_cluster_registry is not None\n            and not isinstance(\n                kubernetes_cluster_registry,\n                KubernetesClusterRegistry,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes cluster registry is invalid"\n            )\n\n        if (\n            prometheus_cluster_registry is not None\n            and not isinstance(\n                prometheus_cluster_registry,\n                PrometheusClusterRegistry,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Prometheus cluster registry is invalid"\n            )\n\n        if (\n            llm_gateway is not None\n            and not isinstance(\n                llm_gateway,\n                LLMGateway,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime shared LLM gateway is invalid"\n            )\n\n        if (\n            investigation_reasoner is not None\n            and not isinstance(\n                investigation_reasoner,\n                BaseInvestigationReasoner,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation reasoner is invalid"\n            )\n\n        if (\n            investigation_settings is not None\n            and not isinstance(\n                investigation_settings,\n                InvestigationSettings,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation settings are invalid"\n            )\n\n        # Resolve disabled-default Investigation configuration before any\n        # Runtime store, tool, credential, network or LLM component is created.\n        self.investigation_settings = (\n            investigation_settings\n            if investigation_settings is not None\n            else InvestigationSettings.from_environment()\n        )\n\n        investigation_shared_gateway = None\n\n        # An enabled LLM-backed Investigation must use the exact shared\n        # LLMGateway instance that AgentRuntime will provide to its Agents.\n        #\n        # Disabled Investigation deliberately does not inspect or touch the\n        # supplied reasoner\'s LLM adapter.\n        if (\n            self.investigation_settings.enabled\n            and isinstance(\n                investigation_reasoner,\n                LLMInvestigationReasoner,\n            )\n        ):\n            investigation_llm = (\n                investigation_reasoner.investigation_llm\n            )\n\n            if not isinstance(\n                investigation_llm,\n                InvestigationLLMGatewayAdapter,\n            ):\n                raise TypeError(\n                    "AgentRuntime LLM Investigation requires "\n                    "InvestigationLLMGatewayAdapter"\n                )\n\n            investigation_shared_gateway = (\n                investigation_llm.llm_gateway\n            )\n\n            if not isinstance(\n                investigation_shared_gateway,\n                LLMGateway,\n            ):\n                raise TypeError(\n                    "AgentRuntime Investigation shared LLM gateway is invalid"\n                )\n\n            if (\n                llm_gateway is not None\n                and investigation_shared_gateway\n                is not llm_gateway\n            ):\n                raise TypeError(\n                    "AgentRuntime Investigation LLM gateway must be shared"\n                )\n\n        # Preserve the existing fail-closed Investigation assembly boundary.\n        # Enabled mode without an explicit reasoner still fails here before\n        # any Runtime or LLM infrastructure is constructed.\n        self.investigation_coordinator = (\n            create_investigation_coordinator(\n                reasoner=investigation_reasoner,\n                settings=self.investigation_settings,\n            )\n        )\n\n        # Do not construct a default Gateway yet. Keeping this unresolved\n        # preserves the previous initialization order. If Investigation\n        # already carries the approved Gateway Adapter, Runtime adopts that\n        # exact Gateway object as its shared instance.\n        self.llm_gateway = (\n            llm_gateway\n            if llm_gateway is not None\n            else investigation_shared_gateway\n        )\n\n        self.authentication = (\n            authentication_service\n            if authentication_service is not None\n            else create_authentication_service()\n        )\n\n        self.security_policy = (\n            security_policy\n            if security_policy is not None\n            else SecurityPolicyEngine()\n        )\n\n        self.kubernetes_preflight = (\n            kubernetes_preflight\n            if kubernetes_preflight is not None\n            else create_kubernetes_preflight_resolver()\n        )\n\n        self.production_pilot_control = (\n            production_pilot_control\n            if production_pilot_control is not None\n            else create_kubernetes_production_pilot_control()\n        )\n\n        # This independent gate may read both credential values at startup,\n        # but can construct only a two-GET probe. Disabled mode returns before\n        # any credential or CA access.\n        self.production_pilot_live_probe = (\n            production_pilot_live_probe\n            if production_pilot_live_probe is not None\n            else create_production_pilot_live_readiness_probe()\n        )\n\n        self.production_pilot_budget_store = None\n        self.production_pilot_budget_service = (\n            production_pilot_budget_service\n        )\n        if (\n            self.production_pilot_budget_service is None\n            and self.production_pilot_control.config.enabled\n        ):\n            self.production_pilot_budget_store = (\n                ProductionPilotBudgetStore()\n            )\n            self.production_pilot_budget_service = (\n                ProductionPilotBudgetService(\n                    store=(\n                        self.production_pilot_budget_store\n                    )\n                )\n            )\n\n        self.kubernetes_production_executor = (\n            kubernetes_production_executor\n            if kubernetes_production_executor is not None\n            else create_kubernetes_production_executor(\n                pilot_control=(\n                    self.production_pilot_control\n                ),\n                pilot_budget_service=(\n                    self.production_pilot_budget_service\n                ),\n            )\n        )\n\n        if self.kubernetes_production_executor is not None:\n            executor_control = getattr(\n                self.kubernetes_production_executor,\n                "pilot_control",\n                None,\n            )\n            if executor_control is None:\n                self.kubernetes_production_executor.pilot_control = (\n                    self.production_pilot_control\n                )\n            elif executor_control is not self.production_pilot_control:\n                raise TypeError(\n                    "AgentRuntime Kubernetes production pilot control must be shared"\n                )\n            executor_budget = getattr(\n                self.kubernetes_production_executor,\n                "pilot_budget_service",\n                None,\n            )\n            if executor_budget is None:\n                if self.production_pilot_budget_service is None:\n                    raise TypeError(\n                        "AgentRuntime Kubernetes production pilot budget is unavailable"\n                    )\n                self.kubernetes_production_executor.pilot_budget_service = (\n                    self.production_pilot_budget_service\n                )\n            elif executor_budget is not self.production_pilot_budget_service:\n                raise TypeError(\n                    "AgentRuntime Kubernetes production pilot budget must be shared"\n                )\n\n        if (\n            self.kubernetes_production_executor is not None\n            and self.kubernetes_preflight is None\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production executor requires "\n                "trusted preflight"\n            )\n\n        self.production_pilot_readiness = (\n            ProductionPilotReadinessService(\n                control=(\n                    self.production_pilot_control\n                ),\n                production_executor_configured=(\n                    self.kubernetes_production_executor\n                    is not None\n                ),\n            )\n        )\n        self.production_pilot_rehearsal = (\n            ProductionPilotRehearsalService(\n                control=(\n                    self.production_pilot_control\n                ),\n                budget_service=(\n                    self.production_pilot_budget_service\n                ),\n                production_executor_configured=(\n                    self.kubernetes_production_executor\n                    is not None\n                ),\n            )\n        )\n        # Pure recovery-policy proof. It owns no store, credential, network\n        # client or executor and is available while the production gate is\n        # disabled so operators can rehearse recovery before enablement.\n        self.production_pilot_crash_recovery_rehearsal = (\n            ProductionPilotCrashRecoveryRehearsalService()\n        )\n\n        self.memory = MemoryStore()\n\n        if (\n            kubernetes_cluster_registry\n            is None\n        ):\n            self.kubernetes_cluster_registry = (\n                create_kubernetes_cluster_registry()\n            )\n        else:\n            self.kubernetes_cluster_registry = (\n                kubernetes_cluster_registry\n            )\n\n        if (\n            prometheus_cluster_registry\n            is None\n        ):\n            self.prometheus_cluster_registry = (\n                create_prometheus_cluster_registry()\n            )\n        else:\n            self.prometheus_cluster_registry = (\n                prometheus_cluster_registry\n            )\n\n        self.cluster_verified_evidence_required = (\n            self.kubernetes_cluster_registry\n            is not None\n            or self.prometheus_cluster_registry\n            is not None\n        )\n\n        if (\n            self.investigation_coordinator\n            is not None\n        ):\n            self.investigation_coordinator.require_cluster_verified_evidence = (\n                self.cluster_verified_evidence_required\n            )\n\n        tool_manager_kwargs = {}\n\n        if (\n            self.kubernetes_cluster_registry\n            is not None\n        ):\n            tool_manager_kwargs[\n                "kubernetes_cluster_registry"\n            ] = self.kubernetes_cluster_registry\n\n        if (\n            self.prometheus_cluster_registry\n            is not None\n        ):\n            tool_manager_kwargs[\n                "prometheus_cluster_registry"\n            ] = self.prometheus_cluster_registry\n\n        if tool_manager_kwargs:\n            self.tools = create_tool_manager(\n                **tool_manager_kwargs\n            )\n        else:\n            self.tools = create_tool_manager()\n\n        self.skills = create_skill_registry()\n        self.mcp = create_mcp_registry()\n        self.tracer = TraceCollector()\n        self.evaluators = create_evaluation_registry()\n\n        # Remediation business policy. This is intentionally separate from\n        # security_policy, which authorizes operator-facing operations.\n        self.policy = create_policy_engine()\n\n        self.preflight_artifact_store = None\n        self.preflight_artifact_service = None\n        self.production_action_guard = None\n        self.production_action_preparation = None\n        self.production_action_query = None\n\n        if self.kubernetes_preflight is not None:\n            self.preflight_artifact_store = PreflightArtifactStore()\n            self.preflight_artifact_service = PreflightArtifactService(\n                store=self.preflight_artifact_store\n            )\n            self.production_action_guard = (\n                ProductionActionExpiryGuard(\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    )\n                )\n            )\n\n        self.approval = ApprovalService()\n\n        if self.production_action_guard is not None:\n            self.approval.manager.set_transition_guard(\n                self.production_action_guard\n            )\n\n        if self.preflight_artifact_service is not None:\n            self.production_action_preparation = (\n                ProductionActionPreparationService(\n                    resolver=self.kubernetes_preflight,\n                    artifact_service=self.preflight_artifact_service,\n                    approval_service=self.approval,\n                )\n            )\n\n        self.production_pilot_ceremony_store = None\n        self.production_pilot_ceremony = None\n        if (\n            self.production_pilot_control.config.enabled\n            and self.production_pilot_budget_service is not None\n            and self.preflight_artifact_service is not None\n        ):\n            self.production_pilot_ceremony_store = (\n                ProductionPilotCeremonyStore()\n            )\n            self.production_pilot_ceremony = (\n                ProductionPilotCeremonyService(\n                    store=(\n                        self.production_pilot_ceremony_store\n                    ),\n                    control=(\n                        self.production_pilot_control\n                    ),\n                    rehearsal=(\n                        self.production_pilot_rehearsal\n                    ),\n                    budget_service=(\n                        self.production_pilot_budget_service\n                    ),\n                    approval_service=self.approval,\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                )\n            )\n\n        self.incident_store = IncidentStore()\n\n        if self.preflight_artifact_service is not None:\n            self.production_action_query = (\n                ProductionActionQueryService(\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                    approval_service=self.approval,\n                    incident_store=self.incident_store,\n                )\n            )\n\n        self.incident_service = IncidentService(\n            store=self.incident_store\n        )\n\n        self.workflow_service = WorkflowService(\n            incident_service=self.incident_service\n        )\n\n        self.action_execution_store = ActionExecutionStore()\n\n        self.action_execution_service = ActionExecutionService(\n            store=self.action_execution_store\n        )\n\n        self.action_runtime = ActionRuntime(\n            approval_service=self.approval,\n            incident_store=self.incident_store,\n            action_execution_service=self.action_execution_service,\n            production_action_guard=(\n                self.production_action_guard\n            ),\n            kubernetes_production_executor=(\n                self.kubernetes_production_executor\n            ),\n            preflight_artifact_service=(\n                self.preflight_artifact_service\n                if self.kubernetes_production_executor is not None\n                else None\n            ),\n            production_pilot_control=(\n                self.production_pilot_control\n            ),\n            production_pilot_budget_service=(\n                self.production_pilot_budget_service\n            ),\n            production_pilot_ceremony_service=(\n                self.production_pilot_ceremony\n                if self.kubernetes_production_executor is not None\n                else None\n            ),\n        )\n\n        self.verification_store = VerificationStore()\n\n        self.verification = VerificationService(\n            store=self.verification_store\n        )\n\n        self.verification_runtime = VerificationRuntime(\n            verification_service=self.verification,\n            incident_store=self.incident_store,\n        )\n\n        self.verification_profile_factory = VerificationProfileFactory()\n\n        self.verification_collector = VerificationEvidenceCollector(\n            tools=self.tools,\n            require_cluster_verified_evidence=(\n                self.cluster_verified_evidence_required\n            ),\n        )\n\n        self.verification_coordinator = VerificationCoordinator(\n            profile_factory=self.verification_profile_factory,\n            collector=self.verification_collector,\n            verification_runtime=self.verification_runtime,\n        )\n\n        # Final pre-enable evidence is assembled only when every production\n        # preparation component is available. The service is read-only and\n        # deliberately owns no executor or mutable workflow operation.\n        self.production_pilot_pre_enable_evidence = None\n        if all(\n            component is not None\n            for component in (\n                self.production_pilot_ceremony,\n                self.production_pilot_budget_service,\n                self.preflight_artifact_service,\n            )\n        ):\n            self.production_pilot_pre_enable_evidence = (\n                ProductionPilotPreEnableEvidenceService(\n                    readiness_service=(\n                        self.production_pilot_readiness\n                    ),\n                    rehearsal_service=(\n                        self.production_pilot_rehearsal\n                    ),\n                    crash_rehearsal_service=(\n                        self.production_pilot_crash_recovery_rehearsal\n                    ),\n                    ceremony_service=(\n                        self.production_pilot_ceremony\n                    ),\n                    budget_service=(\n                        self.production_pilot_budget_service\n                    ),\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                    approval_service=self.approval,\n                    incident_store=self.incident_store,\n                    action_execution_service=(\n                        self.action_execution_service\n                    ),\n                    verification_service=self.verification,\n                )\n            )\n\n        # The final handoff rehearsal is also strictly read-only. It is\n        # available only with the full prepared Pilot chain and explicitly\n        # records whether production executors remain absent while the gate\n        # is disabled.\n        self.production_pilot_final_handoff_rehearsal = None\n        if self.production_pilot_pre_enable_evidence is not None:\n            self.production_pilot_final_handoff_rehearsal = (\n                ProductionPilotFinalHandoffRehearsalService(\n                    pilot_control=self.production_pilot_control,\n                    pre_enable_evidence_service=(\n                        self.production_pilot_pre_enable_evidence\n                    ),\n                    preflight_resolver=self.kubernetes_preflight,\n                    production_executor_configured=(\n                        self.kubernetes_production_executor is not None\n                    ),\n                    action_runtime_production_executor_configured=(\n                        getattr(\n                            self.action_runtime,\n                            "kubernetes_production_executor",\n                            None,\n                        )\n                        is not None\n                    ),\n                )\n            )\n\n        # A dedicated database is created only when the separately gated live\n        # probe exists and the full zero-write handoff chain is available.\n        self.production_pilot_go_no_go_store = None\n        self.production_pilot_go_no_go = None\n        if (\n            self.production_pilot_live_probe is not None\n            and self.production_pilot_final_handoff_rehearsal is not None\n            and self.preflight_artifact_service is not None\n        ):\n            self.production_pilot_go_no_go_store = (\n                ProductionPilotGoNoGoStore()\n            )\n            self.production_pilot_go_no_go = (\n                ProductionPilotGoNoGoService(\n                    store=self.production_pilot_go_no_go_store,\n                    live_probe=self.production_pilot_live_probe,\n                    final_handoff_service=(\n                        self.production_pilot_final_handoff_rehearsal\n                    ),\n                    artifact_service=self.preflight_artifact_service,\n                    pilot_control=self.production_pilot_control,\n                )\n            )\n\n        self.sandbox = LocalSandboxExecutor()\n\n        self.sandbox_policy = SandboxPolicyValidator()\n\n        if self.llm_gateway is None:\n            self.llm_gateway = create_llm_gateway()\n\n        self.registry = create_agent_registry(\n            llm_gateway=self.llm_gateway,\n        )\n\n        self.planner = AgentPlanner()\n\n        self.pipeline = PlannerPipeline(\n            self.registry,\n            self.planner,\n            self.tracer,\n            self.evaluators,\n            incident_store=self.incident_store,\n            incident_service=self.incident_service,\n            workflow_service=self.workflow_service,\n        )\n\n    async def execute(\n        self,\n        context: AgentContext,\n    ):\n        """\n        Execute the primary PlannerPipeline and, when explicitly enabled,\n        run Investigation automatically as a best-effort Shadow.\n\n        Ordering is deliberate:\n\n        1. PlannerPipeline completes first.\n        2. Investigation receives an isolated AgentContext.\n        3. Only the bounded investigation_shadow snapshot is copied back.\n\n        Investigation can never change the Pipeline result, Incident,\n        variables, results, trace, Approval, executions or evaluations.\n\n        Investigation orchestration failure is sanitized and recorded in\n        metadata without failing an otherwise successful Pipeline execution.\n        """\n\n        if not isinstance(\n            context,\n            AgentContext,\n        ):\n            raise TypeError(\n                "AgentRuntime execution context is invalid"\n            )\n\n        # Reserved Shadow metadata from a previous execution must never be\n        # visible to the primary Pipeline, even when this Runtime currently\n        # has Investigation disabled.\n        for reserved_key in (\n            "investigation_shadow",\n            "investigation_shadow_orchestration",\n            "investigation_rca_comparison",\n        ):\n            context.metadata.pop(\n                reserved_key,\n                None,\n            )\n\n        # Primary workflow semantics remain authoritative. Pipeline failure\n        # propagates normally and Investigation is not attempted afterward.\n        context.metadata.pop(\n            "incident_evidence_recorder",\n            None,\n        )\n\n        results = await self.pipeline.execute(\n            context\n        )\n\n        # Evidence Recorder is evaluation-only and best-effort.\n        await self._record_incident_evidence_shadow(\n            context\n        )\n\n        if self.investigation_coordinator is None:\n            return results\n\n        shadow_context = (\n            self._create_investigation_shadow_context(\n                context\n            )\n        )\n\n        try:\n            await self.run_investigation_shadow(\n                shadow_context\n            )\n\n            snapshot = shadow_context.metadata.get(\n                "investigation_shadow"\n            )\n\n            if (\n                not isinstance(\n                    snapshot,\n                    dict,\n                )\n                or snapshot.get(\n                    "shadow_mode"\n                )\n                is not True\n                or snapshot.get(\n                    "read_only"\n                )\n                is not True\n            ):\n                raise RuntimeError(\n                    "Investigation Shadow snapshot is invalid"\n                )\n\n            context.metadata[\n                "investigation_shadow"\n            ] = deepcopy(\n                snapshot\n            )\n\n        except Exception as exc:\n            # Shadow means Shadow: an Investigation orchestration fault must\n            # never convert a successful PlannerPipeline execution to failed.\n            #\n            # Raw exception text is deliberately excluded because provider,\n            # URL, credential or tool details may be present in it.\n            context.metadata[\n                "investigation_shadow_orchestration"\n            ] = {\n                "shadow_mode": True,\n                "read_only": True,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n\n        # Comparison is evaluation-only. It cannot change the authoritative\n        # RCA stored in context.variables["rca"] and has no Healing authority.\n        try:\n            context.metadata[\n                "investigation_rca_comparison"\n            ] = build_rca_investigation_comparison(\n                rca=context.variables.get(\n                    "rca"\n                ),\n                investigation_snapshot=(\n                    context.metadata.get(\n                        "investigation_shadow"\n                    )\n                ),\n                orchestration_snapshot=(\n                    context.metadata.get(\n                        "investigation_shadow_orchestration"\n                    )\n                ),\n            )\n        except Exception as exc:\n            # A comparison bug must remain weaker than Shadow itself and must\n            # never fail a successful primary Pipeline.\n            context.metadata[\n                "investigation_rca_comparison"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "available": False,\n                "comparison_status": (\n                    "comparison_failed"\n                ),\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n\n        return results\n\n    def _create_investigation_shadow_context(\n        self,\n        context: AgentContext,\n    ) -> AgentContext:\n        """\n        Build the minimum-privilege context for automatic Investigation.\n\n        Copied:\n        - event input\n        - request correlation ID\n\n        Shared:\n        - exact Runtime-owned ToolManager\n\n        Deliberately not shared:\n        - Incident\n        - variables\n        - results\n        - metadata\n        - trace\n        - memory\n        - skills\n        - MCP\n        - sandbox\n        - Approval\n        - executions\n        - evaluations\n        """\n\n        return AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n    async def run_investigation_shadow(\n        self,\n        context: AgentContext,\n    ) -> InvestigationState:\n        """\n        Explicitly execute the enabled read-only Investigation Shadow.\n\n        This method is intentionally separate from PlannerPipeline.\n\n        PlannerPipeline itself never invokes Investigation. AgentRuntime\n        may call this lower-level entry point after a successful Pipeline\n        execution when automatic Shadow Investigation is enabled.\n\n        The supplied AgentContext must use the exact Runtime ToolManager so\n        Investigation probes cannot bypass Runtime-owned tool boundaries.\n        """\n\n        if not isinstance(\n            context,\n            AgentContext,\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation Shadow context is invalid"\n            )\n\n        if self.investigation_coordinator is None:\n            raise RuntimeError(\n                "AgentRuntime Investigation Shadow is disabled"\n            )\n\n        if context.tools is not self.tools:\n            raise TypeError(\n                "AgentRuntime Investigation Shadow requires shared Runtime tools"\n            )\n\n        return await (\n            self.investigation_coordinator.investigate(\n                context\n            )\n        )\n\n    async def _record_incident_evidence_shadow(\n        self,\n        context: AgentContext,\n    ) -> None:\n        """\n        Best-effort, decision-isolated production evidence preservation.\n\n        Runs only after the authoritative PlannerPipeline succeeds.\n        Disabled mode constructs no Recorder and issues no production Probe.\n        """\n\n        try:\n            settings = (\n                IncidentEvidenceRecorderSettings\n                .from_environment()\n            )\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n            return\n\n        if not settings.enabled:\n            return\n\n        recorder_context = AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n        try:\n            recorder = ProductionIncidentEvidenceRecorder(\n                settings.resolve_output_dir()\n            )\n\n            result = await recorder.record(\n                recorder_context\n            )\n\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "captured",\n                "created": result.created,\n                "incident_id": result.incident_id,\n                "observation_count": (\n                    result.observation_count\n                ),\n                "capture_file": result.path.name,\n            }\n\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n'
TEST_SOURCE = 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\nfrom types import SimpleNamespace\nfrom typing import Any\n\nimport pytest\n\nimport services.agent_runtime.app.runtime.runtime as runtime_module\n\nfrom common.config.settings import (\n    AuthenticationConfig,\n)\n\nfrom services.agent_runtime.app.investigation.coordinator import (\n    EvidenceDrivenInvestigationCoordinator,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    IncidentHypothesis,\n    InvestigationDecision,\n    InvestigationLimits,\n    InvestigationProbe,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n)\nfrom services.agent_runtime.app.investigation.settings import (\n    InvestigationSettings,\n)\nfrom services.agent_runtime.app.security.factory import (\n    create_authentication_service,\n)\nfrom services.agent_runtime.app.tools.manager import (\n    ToolManager,\n)\nfrom services.agent_runtime.app.tools.registry import (\n    ToolRegistry,\n)\nfrom services.agent_runtime.app.verification.collector import (\n    VerificationEvaluation,\n    VerificationEvidenceCollector,\n    VerificationProbe,\n)\nfrom services.agent_runtime.app.verification.models import (\n    VerificationSource,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    11,\n    5,\n    30,\n    tzinfo=UTC,\n)\n\nCLUSTER = "prod-us-03"\n\n\nclass StopAfterOneProbeReasoner(\n    BaseInvestigationReasoner\n):\n    def __init__(\n        self,\n    ) -> None:\n        self.calls = 0\n\n    async def decide(\n        self,\n        scope,\n        state,\n    ) -> InvestigationDecision:\n        self.calls += 1\n\n        hypothesis = IncidentHypothesis(\n            hypothesis_id="strict-cluster-policy",\n            cause="collect one bounded evidence item",\n            confidence=0.1,\n            supporting_evidence_ids=[],\n            conflicting_evidence_ids=[],\n            missing_evidence=[\n                "verified production evidence"\n            ],\n            optional_evidence=[],\n        )\n\n        if self.calls == 1:\n            return InvestigationDecision(\n                hypotheses=[\n                    hypothesis\n                ],\n                rationale_summary=(\n                    "collect one Kubernetes read"\n                ),\n                stop=False,\n                next_probe=(\n                    InvestigationProbe\n                    .KUBERNETES_POD_STATE\n                ),\n            )\n\n        return InvestigationDecision(\n            hypotheses=[\n                hypothesis\n            ],\n            rationale_summary=(\n                "stop after evidence admission policy"\n            ),\n            stop=True,\n            stop_reason=(\n                InvestigationStopReason\n                .INSUFFICIENT_EVIDENCE\n            ),\n            next_probe=None,\n            conclusion=None,\n        )\n\n\nclass OneEvidenceExecutor:\n    def __init__(\n        self,\n        evidence: EvidenceItem,\n    ) -> None:\n        self.evidence = evidence\n\n    async def collect(\n        self,\n        context,\n        scope,\n        probe,\n    ) -> EvidenceItem:\n        return self.evidence\n\n\ndef context():\n    return SimpleNamespace(\n        event=SimpleNamespace(\n            header=SimpleNamespace(\n                occurred_at=NOW,\n            ),\n            signal=SimpleNamespace(\n                name="PodRestartHigh",\n                message="restart rate elevated",\n            ),\n            resources=[\n                SimpleNamespace(\n                    name="device-gateway-xyz789",\n                    namespace="fleet-edge",\n                    cluster=CLUSTER,\n                )\n            ],\n        ),\n        metadata={},\n    )\n\n\ndef identityless_evidence() -> EvidenceItem:\n    return EvidenceItem(\n        evidence_id="identityless-production-evidence",\n        probe=(\n            InvestigationProbe\n            .KUBERNETES_POD_STATE\n        ),\n        source="kubernetes",\n        success=True,\n        trusted=True,\n        production_signal=True,\n        reliability=1.0,\n        observed_at=NOW,\n        cluster=None,\n        cluster_verified=False,\n        facts={\n            "ready": True,\n        },\n    )\n\n\ndef verified_evidence() -> EvidenceItem:\n    return EvidenceItem(\n        evidence_id="verified-production-evidence",\n        probe=(\n            InvestigationProbe\n            .KUBERNETES_POD_STATE\n        ),\n        source="kubernetes",\n        success=True,\n        trusted=True,\n        production_signal=True,\n        reliability=1.0,\n        observed_at=NOW,\n        cluster=CLUSTER,\n        cluster_verified=True,\n        facts={\n            "ready": True,\n        },\n    )\n\n\n@pytest.mark.asyncio\nasync def test_default_investigation_policy_preserves_identityless_legacy_compatibility():\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=(\n                StopAfterOneProbeReasoner()\n            ),\n            probe_executor=(\n                OneEvidenceExecutor(\n                    identityless_evidence()\n                )\n            ),\n            limits=InvestigationLimits(\n                max_iterations=3,\n                max_tool_calls=2,\n                timeout_seconds=10,\n            ),\n            utc_clock=lambda: NOW,\n        )\n    )\n\n    result = await coordinator.investigate(\n        context()\n    )\n\n    admitted = result.evidence[\n        0\n    ]\n\n    assert admitted.success is True\n    assert admitted.trusted is True\n\n    assert (\n        admitted.cluster_verified\n        is False\n    )\n\n    assert admitted.facts == {\n        "ready": True,\n    }\n\n\n@pytest.mark.asyncio\nasync def test_strict_investigation_policy_strips_identityless_production_evidence():\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=(\n                StopAfterOneProbeReasoner()\n            ),\n            probe_executor=(\n                OneEvidenceExecutor(\n                    identityless_evidence()\n                )\n            ),\n            limits=InvestigationLimits(\n                max_iterations=3,\n                max_tool_calls=2,\n                timeout_seconds=10,\n            ),\n            utc_clock=lambda: NOW,\n            require_cluster_verified_evidence=True,\n        )\n    )\n\n    result = await coordinator.investigate(\n        context()\n    )\n\n    rejected = result.evidence[\n        0\n    ]\n\n    assert rejected.success is False\n    assert rejected.trusted is False\n\n    assert rejected.error_code == (\n        "ClusterVerificationRequired"\n    )\n\n    assert rejected.cluster is None\n    assert rejected.facts == {}\n\n\n@pytest.mark.asyncio\nasync def test_strict_investigation_policy_accepts_matching_verified_evidence():\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=(\n                StopAfterOneProbeReasoner()\n            ),\n            probe_executor=(\n                OneEvidenceExecutor(\n                    verified_evidence()\n                )\n            ),\n            limits=InvestigationLimits(\n                max_iterations=3,\n                max_tool_calls=2,\n                timeout_seconds=10,\n            ),\n            utc_clock=lambda: NOW,\n            require_cluster_verified_evidence=True,\n        )\n    )\n\n    result = await coordinator.investigate(\n        context()\n    )\n\n    admitted = result.evidence[\n        0\n    ]\n\n    assert admitted.success is True\n    assert admitted.trusted is True\n\n    assert (\n        admitted.cluster_verified\n        is True\n    )\n\n    assert admitted.cluster == (\n        CLUSTER\n    )\n\n\nclass RecordingTools:\n    def __init__(\n        self,\n        result: dict[str, Any],\n    ) -> None:\n        self.result = result\n        self.calls = []\n\n    async def call(\n        self,\n        name,\n        context=None,\n        **kwargs,\n    ):\n        self.calls.append(\n            {\n                "name": name,\n                "kwargs": kwargs,\n            }\n        )\n\n        return self.result\n\n\ndef verification_result(\n    *,\n    include_cluster: bool,\n) -> dict[str, Any]:\n    result = {\n        "success": True,\n        "source": "prometheus",\n        "mode": "read_only",\n        "production_signal": True,\n        "observed_at": NOW.isoformat(),\n        "data": {\n            "resultType": "vector",\n            "result": [],\n        },\n    }\n\n    if include_cluster:\n        result[\n            "cluster"\n        ] = CLUSTER\n\n    return result\n\n\ndef verification_probe(\n    *,\n    required: bool,\n    evaluator,\n) -> VerificationProbe:\n    return VerificationProbe(\n        name="restart_metric",\n        source=VerificationSource.METRIC,\n        tool="prometheus",\n        provider="prometheus",\n        arguments={\n            "query": (\n                \'up{cluster="prod-us-03"}\'\n            ),\n            "cluster": CLUSTER,\n        },\n        evaluator=evaluator,\n        required=required,\n    )\n\n\n@pytest.mark.asyncio\nasync def test_strict_verification_rejects_required_identityless_evidence_before_evaluator():\n    evaluator_calls = []\n\n    def evaluator(\n        evidence,\n    ):\n        evaluator_calls.append(\n            evidence\n        )\n\n        return VerificationEvaluation(\n            passed=True\n        )\n\n    collector = VerificationEvidenceCollector(\n        tools=RecordingTools(\n            verification_result(\n                include_cluster=False\n            )\n        ),\n        clock=lambda: NOW,\n        require_cluster_verified_evidence=True,\n    )\n\n    check = await collector.collect_one(\n        verification_probe(\n            required=True,\n            evaluator=evaluator,\n        )\n    )\n\n    assert check.passed is None\n\n    assert (\n        "cluster verification is required"\n        in check.metadata[\n            "rejection_reasons"\n        ]\n    )\n\n    assert evaluator_calls == []\n\n\n@pytest.mark.asyncio\nasync def test_strict_verification_accepts_required_cluster_verified_evidence():\n    def evaluator(\n        evidence,\n    ):\n        return VerificationEvaluation(\n            passed=True,\n            message="verified cluster evidence",\n        )\n\n    collector = VerificationEvidenceCollector(\n        tools=RecordingTools(\n            verification_result(\n                include_cluster=True\n            )\n        ),\n        clock=lambda: NOW,\n        require_cluster_verified_evidence=True,\n    )\n\n    check = await collector.collect_one(\n        verification_probe(\n            required=True,\n            evaluator=evaluator,\n        )\n    )\n\n    assert check.passed is True\n\n    assert (\n        check.metadata[\n            "cluster_verified"\n        ]\n        is True\n    )\n\n    assert (\n        check.metadata[\n            "evidence_cluster"\n        ]\n        == CLUSTER\n    )\n\n\n@pytest.mark.asyncio\nasync def test_optional_verification_probe_keeps_identityless_compatibility_in_strict_mode():\n    def evaluator(\n        evidence,\n    ):\n        return VerificationEvaluation(\n            passed=True,\n            message="optional legacy evidence",\n        )\n\n    collector = VerificationEvidenceCollector(\n        tools=RecordingTools(\n            verification_result(\n                include_cluster=False\n            )\n        ),\n        clock=lambda: NOW,\n        require_cluster_verified_evidence=True,\n    )\n\n    check = await collector.collect_one(\n        verification_probe(\n            required=False,\n            evaluator=evaluator,\n        )\n    )\n\n    assert check.passed is True\n\n    assert (\n        check.metadata[\n            "cluster_verified"\n        ]\n        is False\n    )\n\n\ndef _runtime_with_registry_presence(\n    monkeypatch,\n    tmp_path,\n    *,\n    kubernetes_registry,\n    prometheus_registry,\n):\n    monkeypatch.chdir(\n        tmp_path\n    )\n\n    fake_coordinator = SimpleNamespace(\n        require_cluster_verified_evidence=False\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_investigation_coordinator",\n        lambda **_: fake_coordinator,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_cluster_registry",\n        lambda: kubernetes_registry,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_prometheus_cluster_registry",\n        lambda: prometheus_registry,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_tool_manager",\n        lambda **_: ToolManager(\n            ToolRegistry()\n        ),\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_preflight_resolver",\n        lambda: None,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_production_executor",\n        lambda **_: None,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_production_pilot_live_readiness_probe",\n        lambda: None,\n    )\n\n    runtime = runtime_module.AgentRuntime(\n        authentication_service=(\n            create_authentication_service(\n                AuthenticationConfig()\n            )\n        ),\n        investigation_settings=(\n            InvestigationSettings()\n        ),\n    )\n\n    return (\n        runtime,\n        fake_coordinator,\n    )\n\n\ndef test_runtime_default_keeps_cluster_verified_policy_disabled(\n    monkeypatch,\n    tmp_path,\n):\n    runtime, coordinator = (\n        _runtime_with_registry_presence(\n            monkeypatch,\n            tmp_path,\n            kubernetes_registry=None,\n            prometheus_registry=None,\n        )\n    )\n\n    assert (\n        runtime.cluster_verified_evidence_required\n        is False\n    )\n\n    assert (\n        coordinator.require_cluster_verified_evidence\n        is False\n    )\n\n    assert (\n        runtime.verification_collector\n        .require_cluster_verified_evidence\n        is False\n    )\n\n\n@pytest.mark.parametrize(\n    (\n        "kubernetes_registry",\n        "prometheus_registry",\n    ),\n    [\n        (\n            object(),\n            None,\n        ),\n        (\n            None,\n            object(),\n        ),\n        (\n            object(),\n            object(),\n        ),\n    ],\n)\ndef test_runtime_automatically_enables_strict_policy_when_read_registry_exists(\n    monkeypatch,\n    tmp_path,\n    kubernetes_registry,\n    prometheus_registry,\n):\n    runtime, coordinator = (\n        _runtime_with_registry_presence(\n            monkeypatch,\n            tmp_path,\n            kubernetes_registry=(\n                kubernetes_registry\n            ),\n            prometheus_registry=(\n                prometheus_registry\n            ),\n        )\n    )\n\n    assert (\n        runtime.cluster_verified_evidence_required\n        is True\n    )\n\n    assert (\n        coordinator.require_cluster_verified_evidence\n        is True\n    )\n\n    assert (\n        runtime.verification_collector\n        .require_cluster_verified_evidence\n        is True\n    )\n\n\ndef test_strict_policy_components_reject_non_boolean_configuration():\n    with pytest.raises(\n        TypeError,\n        match="Investigation cluster-verified evidence policy",\n    ):\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=(\n                StopAfterOneProbeReasoner()\n            ),\n            probe_executor=(\n                OneEvidenceExecutor(\n                    identityless_evidence()\n                )\n            ),\n            require_cluster_verified_evidence=1,\n        )\n\n    with pytest.raises(\n        TypeError,\n        match="Verification cluster-verified evidence policy",\n    ):\n        VerificationEvidenceCollector(\n            tools=ToolManager(\n                ToolRegistry()\n            ),\n            require_cluster_verified_evidence=1,\n        )\n'


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
        "Repository root not found."
    )


def write_text(
    path: Path,
    value: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        value.replace(
            "\r\n",
            "\n",
        ).replace(
            "\r",
            "\n",
        ),
        encoding="utf-8",
        newline="\n",
    )


def raw_sha256(
    path: Path,
) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


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
            result.stdout.rstrip()
            or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            result.stderr.rstrip()
            or "<EMPTY>",
        ]
    )


def verify_raw_hash(
    *,
    root: Path,
    relative: str,
) -> None:
    path = root / relative

    if not path.exists():
        raise RuntimeError(
            f"Required current file is missing: {relative}"
        )

    actual = raw_sha256(
        path
    )

    expected = EXPECTED_RAW_HASHES[
        relative
    ]

    if actual != expected:
        raise RuntimeError(
            (
                f"{relative} changed after the installed Cross-Source Evidence baseline. "
                f"expected_raw_sha256={expected} actual_raw_sha256={actual}. "
                "Refusing stale Production Shadow Cluster-Verified Evidence Policy installation."
            )
        )


def require_tests(
    *,
    root: Path,
    relative_paths: list[str],
    label: str,
) -> list[str]:
    missing = [
        relative
        for relative in relative_paths
        if not (
            root
            / relative
        ).exists()
    ]

    if missing:
        raise RuntimeError(
            (
                f"Required {label} tests are missing: "
                + ", ".join(
                    missing
                )
            )
        )

    return relative_paths


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

    coordinator_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
        / "coordinator.py"
    )

    collector_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "verification"
        / "collector.py"
    )

    runtime_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "runtime"
        / "runtime.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_production_cluster_verified_evidence_policy.py"
    )

    sources = {
        coordinator_file: COORDINATOR_SOURCE,
        collector_file: COLLECTOR_SOURCE,
        runtime_file: RUNTIME_SOURCE,
        test_file: TEST_SOURCE,
    }

    targets = list(
        sources
    )

    preexisting = {
        path: path.exists()
        for path in targets
    }

    backups = []

    report = [
        "Production Shadow Cluster-Verified Evidence Policy v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Automatic activation:",
        "- no new operator switch is introduced",
        "- Runtime requires cluster-verified evidence whenever Kubernetes or Prometheus read registry is active",
        "- disabled/default legacy mode preserves identity-less compatibility",
        "",
        "Investigation strict policy:",
        "- successful trusted production evidence with cluster_verified=False is replaced by fact-free failed evidence",
        "- rejection code is ClusterVerificationRequired",
        "- explicit mismatch remains ClusterEvidenceMismatch",
        "- matching cluster_verified=True evidence remains admissible",
        "",
        "Verification strict policy:",
        "- required probes must be cluster_verified when Runtime strict mode is active",
        "- identity-less required evidence is rejected before evaluator execution",
        "- optional probes retain legacy compatibility but remain visibly cluster_verified=False",
        "",
        "Runtime ownership:",
        "- one shared boolean cluster_verified_evidence_required is derived from active read registries",
        "- the same policy is applied to Investigation Coordinator and VerificationEvidenceCollector",
        "- explicit registry injection activates the same strict policy as configured registries",
        "",
        "Compatibility:",
        "- no Settings schema change",
        "- no Router/Connection Factory change",
        "- no EvidenceItem schema change",
        "- no historical/default mode behavior change",
        "",
        "Authority:",
        "- evidence admission policy only",
        "- no Action / Approval / remediation authority",
        "- no mutating Kubernetes/Prometheus call",
        "",
        "Installer sends no real Kubernetes/Prometheus/LLM request.",
    ]

    try:
        section(
            report,
            "CURRENT RAW HASH PREFLIGHT",
        )

        for relative in EXPECTED_RAW_HASHES:
            verify_raw_hash(
                root=root,
                relative=relative,
            )

            report.append(
                relative
                + "="
                + EXPECTED_RAW_HASHES[
                    relative
                ]
            )

        if test_file.exists():
            raise RuntimeError(
                "Production cluster-verified policy test already exists; refusing to overwrite an unreviewed test"
            )

        section(
            report,
            "BACKUP",
        )

        for path in targets:
            if path.exists():
                backup = backup_file(
                    path
                )

                backups.append(
                    (
                        path,
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

        for path, source in sources.items():
            write_text(
                path,
                source,
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
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
                    for path in targets
                ],
            ],
        )

        add_command(
            report,
            syntax,
        )

        if syntax.returncode != 0:
            raise RuntimeError(
                "Production Shadow Cluster-Verified Evidence Policy syntax failed"
            )

        focused_paths = require_tests(
            root=root,
            label="strict cluster-verified policy",
            relative_paths=[
                (
                    "services/agent_runtime/tests/"
                    "test_production_cluster_verified_evidence_policy.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_cross_source_cluster_evidence_consistency.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_coordinator.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_verification_collector.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_runtime_verification_profile_wiring.py"
                ),
            ],
        )

        focused = run_command(
            root=root,
            name="Production strict evidence policy focused suite",
            command=[
                "uv",
                "run",
                "pytest",
                *focused_paths,
                "-q",
            ],
        )

        add_command(
            report,
            focused,
        )

        if focused.returncode != 0:
            raise RuntimeError(
                "Production strict evidence policy focused tests failed"
            )

        multi_cluster_paths = require_tests(
            root=root,
            label="multi-cluster connection/routing",
            relative_paths=[
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_kubernetes_router.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_prometheus_router.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_connection_config.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_prometheus_connection_config.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_production_scope_integrity.py"
                ),
            ],
        )

        multi_cluster = run_command(
            root=root,
            name="Multi-Cluster routing/config compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                *multi_cluster_paths,
                "-q",
            ],
        )

        add_command(
            report,
            multi_cluster,
        )

        if multi_cluster.returncode != 0:
            raise RuntimeError(
                "Production strict evidence policy Multi-Cluster compatibility failed"
            )

        investigation_paths = require_tests(
            root=root,
            label="Investigation compatibility",
            relative_paths=[
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_models.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_probes.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_epistemic_guard.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_historical_evidence_replay.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_historical_incident_investigation_runner.py"
                ),
            ],
        )

        investigation = run_command(
            root=root,
            name="Investigation / Historical compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                *investigation_paths,
                "-q",
            ],
        )

        add_command(
            report,
            investigation,
        )

        if investigation.returncode != 0:
            raise RuntimeError(
                "Production strict evidence policy Investigation compatibility failed"
            )

        verification_paths = require_tests(
            root=root,
            label="Verification compatibility",
            relative_paths=[
                (
                    "services/agent_runtime/tests/"
                    "test_verification_profiles.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_verification_coordinator.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_verification_fail_closed_e2e.py"
                ),
            ],
        )

        verification = run_command(
            root=root,
            name="Verification compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                *verification_paths,
                "-q",
            ],
        )

        add_command(
            report,
            verification,
        )

        if verification.returncode != 0:
            raise RuntimeError(
                "Production strict evidence policy Verification compatibility failed"
            )

        preflight = run_command(
            root=root,
            name="Strict evidence policy architecture preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "c=Path(r'services/agent_runtime/app/investigation/coordinator.py').read_text(encoding='utf-8'); "
                    "v=Path(r'services/agent_runtime/app/verification/collector.py').read_text(encoding='utf-8'); "
                    "r=Path(r'services/agent_runtime/app/runtime/runtime.py').read_text(encoding='utf-8'); "
                    "print('investigation_strict_policy='+str('ClusterVerificationRequired' in c)); "
                    "print('verification_strict_policy='+str('cluster verification is required' in v)); "
                    "print('runtime_shared_policy='+str('cluster_verified_evidence_required' in r)); "
                    "print('runtime_registry_activation='+str('self.kubernetes_cluster_registry\\n            is not None\\n            or self.prometheus_cluster_registry\\n            is not None' in r)); "
                    "assert 'ClusterVerificationRequired' in c; "
                    "assert 'cluster verification is required' in v; "
                    "assert 'cluster_verified_evidence_required' in r; "
                    "assert 'self.kubernetes_cluster_registry\\n            is not None\\n            or self.prometheus_cluster_registry\\n            is not None' in r"
                ),
            ],
        )

        add_command(
            report,
            preflight,
        )

        if preflight.returncode != 0:
            raise RuntimeError(
                "Production strict evidence policy architecture preflight failed"
            )

        authority = run_command(
            root=root,
            name="Strict evidence policy authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "files=["
                    "Path(r'services/agent_runtime/app/investigation/coordinator.py'),"
                    "Path(r'services/agent_runtime/app/verification/collector.py')"
                    "]; "
                    "s='\\n'.join(x.read_text(encoding='utf-8') for x in files); "
                    "bad=[x for x in ['KubernetesProductionExecutor','.post(','.patch(','.put(','.delete('] if x in s]; "
                    "print('forbidden_matches='+str(bad)); "
                    "raise SystemExit(1 if bad else 0)"
                ),
            ],
        )

        add_command(
            report,
            authority,
        )

        if authority.returncode != 0:
            raise RuntimeError(
                "Production strict evidence policy authority boundary failed"
            )

        status = run_command(
            root=root,
            name="Git status",
            command=[
                "git",
                "status",
                "--short",
                "--",
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
                    for path in targets
                ],
            ],
        )

        add_command(
            report,
            status,
        )

        section(
            report,
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "Production Shadow Cluster-Verified Evidence Policy v1 is installed.",
                "",
                "Guarantee:",
                "- default/legacy Runtime preserves identity-less compatibility",
                "- any active Kubernetes or Prometheus read registry automatically enables strict cluster verification",
                "- strict Investigation strips identity-less trusted production evidence before Reasoner reuse",
                "- strict required Verification rejects identity-less evidence before evaluator execution",
                "- matching cluster_verified=True routed evidence remains admissible",
                "- optional Verification probes remain compatible but visibly unverified",
                "",
                "This closes the migration gap between legacy evidence compatibility and production multi-cluster trust.",
                "",
                "Next recommended step:",
                "- Production Multi-Cluster Readiness / Coverage Gate v1: before enabling Investigation Shadow against production connections, prove that every required probe family has a routable cluster binding and cluster-verified evidence path.",
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
            "PRODUCTION SHADOW CLUSTER-VERIFIED EVIDENCE POLICY V1 PASSED"
        )
        print("=" * 72)
        print()
        print(
            "No real LLM/Kubernetes/Prometheus request was sent."
        )
        print()
        print(
            "Upload only:"
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
                    + (
                        f"{type(rollback_exc).__name__}: "
                        f"{rollback_exc}"
                    )
                )

        for path in targets:
            if (
                not preexisting[
                    path
                ]
                and path.exists()
            ):
                try:
                    path.unlink()

                    rollback.append(
                        "REMOVED newly-created "
                        + str(
                            path.relative_to(
                                root
                            )
                        )
                    )

                except Exception as rollback_exc:
                    rollback.append(
                        "ROLLBACK REMOVE FAILED "
                        + str(
                            path.relative_to(
                                root
                            )
                        )
                        + ": "
                        + (
                            f"{type(rollback_exc).__name__}: "
                            f"{rollback_exc}"
                        )
                    )

        write_text(
            error,
            "\n".join(
                [
                    "Production Shadow Cluster-Verified Evidence Policy v1 FAILED",
                    (
                        "GeneratedAt: "
                        + datetime.now().astimezone().isoformat()
                    ),
                    "",
                    (
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "",
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
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "PRODUCTION SHADOW CLUSTER-VERIFIED EVIDENCE POLICY V1 FAILED"
        )
        print("=" * 72)
        print()
        print(
            "Modified files were rolled back where possible."
        )
        print()
        print(
            "Upload only:"
        )
        print(
            error
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
