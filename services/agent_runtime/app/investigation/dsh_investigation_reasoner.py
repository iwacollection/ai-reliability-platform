from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import uuid4

from pydantic import ValidationError

from services.agent_runtime.app.investigation.dsh_runtime_adapter import (
    DshRunResult,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationDecision,
    InvestigationScope,
    InvestigationState,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
    InvestigationReasonerError,
)


class DshInvestigationReasonerError(InvestigationReasonerError):
    """Sanitized failure at the DeepSeek Harness reasoning boundary."""


class DshInvestigationReasonerTimeoutError(TimeoutError):
    """Sanitized DSH timeout preserving durable timeout semantics."""


@dataclass(frozen=True, slots=True)
class DshInvestigationReasonerConfig:
    cwd: str
    provider: str = "deepseek-official"
    model: str = "deepseek-v4-flash"
    max_tokens: int = 4096

    def __post_init__(self) -> None:
        if not isinstance(self.cwd, str) or not self.cwd.strip():
            raise ValueError("DSH Investigation Reasoner cwd is invalid")
        if (
            not isinstance(self.provider, str)
            or not self.provider.strip()
        ):
            raise ValueError(
                "DSH Investigation Reasoner provider is invalid"
            )
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError(
                "DSH Investigation Reasoner model is invalid"
            )
        if (
            not isinstance(self.max_tokens, int)
            or isinstance(self.max_tokens, bool)
            or not 128 <= self.max_tokens <= 65536
        ):
            raise ValueError(
                "DSH Investigation Reasoner max_tokens is invalid"
            )


@runtime_checkable
class DshReasonerRuntime(Protocol):
    async def __aenter__(self) -> "DshReasonerRuntime":
        ...

    async def __aexit__(self, exc_type, exc, tb) -> None:
        ...

    async def initialize(
        self,
        *,
        cwd: str,
        provider: str,
        model: str,
        max_tokens: int | None = None,
    ) -> dict:
        ...

    async def run_turn(
        self,
        input_text: str,
        *,
        session_id: str,
    ) -> DshRunResult:
        ...


DshReasonerRuntimeFactory = Callable[[], DshReasonerRuntime]


class DshInvestigationReasoner(BaseInvestigationReasoner):
    """
    DeepSeek Harness-backed bounded Investigation reasoner.

    One fresh DSH Runtime is created per decision. The durable Investigation
    state is sent in full on every call, so no DSH session is a second source
    of truth and no state can leak across Incidents.

    DSH may reason, but it may return only an InvestigationDecision. The
    existing DurableInvestigationSessionDriver remains the only component that
    can execute the selected symbolic read-only Probe and persist the result.
    """

    _CONTRACT_VERSION = "ai-reliability-dsh-reasoner-v1"

    def __init__(
        self,
        *,
        runtime_factory: DshReasonerRuntimeFactory,
        config: DshInvestigationReasonerConfig,
    ) -> None:
        if not callable(runtime_factory):
            raise TypeError(
                "DSH Investigation Reasoner runtime factory is invalid"
            )
        if not isinstance(config, DshInvestigationReasonerConfig):
            raise TypeError(
                "DSH Investigation Reasoner config is invalid"
            )

        self.runtime_factory = runtime_factory
        self.config = config

    async def decide(
        self,
        scope: InvestigationScope,
        state: InvestigationState,
    ) -> InvestigationDecision:
        if not isinstance(scope, InvestigationScope):
            raise TypeError(
                "DSH Investigation Reasoner scope is invalid"
            )
        if not isinstance(state, InvestigationState):
            raise TypeError(
                "DSH Investigation Reasoner state is invalid"
            )
        if state.scope != scope:
            raise ValueError(
                "DSH Investigation Reasoner state scope does not match"
            )

        try:
            runtime = self.runtime_factory()
        except Exception:
            raise DshInvestigationReasonerError(
                "DSH Investigation Runtime could not be created"
            ) from None

        if not isinstance(runtime, DshReasonerRuntime):
            raise TypeError(
                "DSH Investigation Runtime does not satisfy the protocol"
            )

        session_id = (
            "investigation-reasoner-"
            + uuid4().hex
        )

        try:
            async with runtime:
                initialized = await runtime.initialize(
                    cwd=str(Path(self.config.cwd).resolve()),
                    provider=self.config.provider,
                    model=self.config.model,
                    max_tokens=self.config.max_tokens,
                )
                if not isinstance(initialized, dict):
                    raise DshInvestigationReasonerError(
                        "DSH Investigation Runtime initialize response is invalid"
                    )

                result = await runtime.run_turn(
                    self._build_prompt(
                        scope=scope,
                        state=state,
                    ),
                    session_id=session_id,
                )

        except DshInvestigationReasonerError:
            raise
        except TimeoutError:
            raise DshInvestigationReasonerTimeoutError(
                "DSH Investigation reasoning timed out"
            ) from None
        except Exception:
            # Do not surface provider/runtime details, command lines, stderr,
            # credentials, prompts, or model output through the durable Driver.
            raise DshInvestigationReasonerError(
                "DSH Investigation reasoning failed"
            ) from None

        if not isinstance(result, DshRunResult):
            raise DshInvestigationReasonerError(
                "DSH Investigation Runtime returned an invalid result"
            )
        if result.finish_reason not in {
            "completed",
            "stop",
        }:
            raise DshInvestigationReasonerError(
                "DSH Investigation reasoning did not complete safely"
            )

        decision = self._parse_decision(
            result.final_response
        )
        self._validate_decision_against_state(
            decision=decision,
            state=state,
        )
        return decision

    @classmethod
    def _build_prompt(
        cls,
        *,
        scope: InvestigationScope,
        state: InvestigationState,
    ) -> str:
        payload = {
            "contract_version": cls._CONTRACT_VERSION,
            "scope": scope.model_dump(
                mode="json"
            ),
            "state": state.model_dump(
                mode="json"
            ),
            "allowed_probes": [
                probe.value
                for probe in state.available_probes
                if probe not in state.attempted_probes
            ],
        }

        return (
            "You are the bounded SRE Investigation Reasoner inside "
            "AI Reliability Platform.\n"
            "Use only the trusted scope and evidence in INPUT_JSON.\n"
            "Never invent evidence, resource identity, commands, URLs, "
            "credentials, PromQL, kubectl arguments, or write actions.\n"
            "You may only choose next_probe from allowed_probes.\n"
            "Return exactly one raw JSON object and no markdown/code fence.\n"
            "The JSON must satisfy this decision contract:\n"
            "{\n"
            '  "hypotheses": [\n'
            "    {\n"
            '      "hypothesis_id": "stable short id",\n'
            '      "cause": "candidate cause",\n'
            '      "confidence": 0.0,\n'
            '      "supporting_evidence_ids": [],\n'
            '      "conflicting_evidence_ids": [],\n'
            '      "missing_evidence": [],\n'
            '      "optional_evidence": []\n'
            "    }\n"
            "  ],\n"
            '  "rationale_summary": "bounded reasoning summary",\n'
            '  "stop": false,\n'
            '  "stop_reason": null,\n'
            '  "next_probe": "one exact value from allowed_probes",\n'
            '  "conclusion": null\n'
            "}\n"
            "If trusted evidence is sufficient, stop=true, "
            'stop_reason="sufficient_evidence", next_probe=null, and '
            "provide a conclusion referencing only exact trusted evidence_ids.\n"
            "If uncertainty cannot be safely reduced with an allowed unattempted "
            "probe, stop=true with insufficient_evidence or no_safe_probe and "
            "next_probe=null.\n"
            "INPUT_JSON:\n"
            + json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )

    @staticmethod
    def _parse_decision(
        content: str,
    ) -> InvestigationDecision:
        if not isinstance(content, str) or not content.strip():
            raise DshInvestigationReasonerError(
                "DSH Investigation reasoning returned no decision"
            )

        stripped = content.strip()
        if stripped.startswith("```") or stripped.endswith("```"):
            raise DshInvestigationReasonerError(
                "DSH Investigation reasoning returned a non-raw JSON decision"
            )

        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            raise DshInvestigationReasonerError(
                "DSH Investigation reasoning returned invalid JSON"
            ) from None

        if not isinstance(payload, dict):
            raise DshInvestigationReasonerError(
                "DSH Investigation reasoning returned a non-object decision"
            )

        try:
            return InvestigationDecision.model_validate(
                payload
            )
        except ValidationError:
            raise DshInvestigationReasonerError(
                "DSH Investigation reasoning violated the decision contract"
            ) from None

    @staticmethod
    def _validate_decision_against_state(
        *,
        decision: InvestigationDecision,
        state: InvestigationState,
    ) -> None:
        trusted_evidence_ids = {
            item.evidence_id
            for item in state.evidence
            if (
                item.success
                and item.trusted
                and item.production_signal
            )
        }

        for hypothesis in decision.hypotheses:
            referenced = set(
                hypothesis.supporting_evidence_ids
            ) | set(
                hypothesis.conflicting_evidence_ids
            )
            if not referenced.issubset(
                trusted_evidence_ids
            ):
                raise DshInvestigationReasonerError(
                    "DSH Investigation decision referenced untrusted evidence"
                )

        if decision.conclusion is not None:
            if not set(
                decision.conclusion.evidence_ids
            ).issubset(
                trusted_evidence_ids
            ):
                raise DshInvestigationReasonerError(
                    "DSH Investigation conclusion referenced untrusted evidence"
                )

        probe = decision.next_probe
        if probe is None:
            return

        if probe not in state.available_probes:
            raise DshInvestigationReasonerError(
                "DSH Investigation decision selected a disallowed probe"
            )
        if probe in state.attempted_probes:
            raise DshInvestigationReasonerError(
                "DSH Investigation decision repeated an attempted probe"
            )
        if state.tool_call_count >= state.limits.max_tool_calls:
            raise DshInvestigationReasonerError(
                "DSH Investigation decision exceeded the probe budget"
            )


__all__ = [
    "DshInvestigationReasoner",
    "DshInvestigationReasonerConfig",
    "DshInvestigationReasonerError",
    "DshInvestigationReasonerTimeoutError",
    "DshReasonerRuntime",
    "DshReasonerRuntimeFactory",
]
