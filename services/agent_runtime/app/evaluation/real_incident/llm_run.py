import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.config import (
    get_settings,
)

from services.agent_runtime.app.evaluation.real_incident.investigation_runner import (
    HistoricalIncidentInvestigationResult,
    HistoricalIncidentInvestigationRunner,
)
from services.agent_runtime.app.investigation.coordinator import (
    EvidenceDrivenInvestigationCoordinator,
)
from services.agent_runtime.app.investigation.llm_gateway_adapter import (
    InvestigationLLMGatewayAdapter,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationLimits,
)
from services.agent_runtime.app.investigation.reasoner import (
    LLMInvestigationReasoner,
)
from services.agent_runtime.app.investigation.settings import (
    INVESTIGATION_ENABLE_ACKNOWLEDGEMENT,
    InvestigationSettings,
)
from services.agent_runtime.app.llm.gateway.factory import (
    create_llm_gateway,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)


class HistoricalLLMRunConfigurationError(
    RuntimeError
):
    """
    Real LLM historical Investigation cannot be safely assembled.
    """


class HistoricalRuntimeProbeGuard:
    """
    Guard attached to the lightweight Runtime shell.

    HistoricalIncidentInvestigationRunner replaces this executor with the
    causal historical Replay executor before Investigation starts.

    If this guard is ever called, the composition boundary was violated.
    """

    async def collect(
        self,
        context,
        scope,
        probe,
    ):
        raise RuntimeError(
            "Historical LLM Runtime live probe backend is unavailable"
        )


def configured_llm_provider_name(
) -> str:
    """
    Return only the non-secret configured provider identifier.
    """

    settings = get_settings()

    provider = getattr(
        getattr(
            settings,
            "llm",
            None,
        ),
        "provider",
        None,
    )

    if not isinstance(
        provider,
        str,
    ):
        raise HistoricalLLMRunConfigurationError(
            "Configured LLM provider is invalid"
        )

    normalized = provider.strip()

    if not normalized:
        raise HistoricalLLMRunConfigurationError(
            "Configured LLM provider is invalid"
        )

    return normalized


def create_historical_llm_runtime(
    *,
    limits: InvestigationLimits | None = None,
    provider_name: str | None = None,
) -> AgentRuntime:
    # Historical evaluation reuses the canonical Gateway/Reasoner chain,
    # while allowing an explicit non-mock provider without mutating app.yaml.
    if provider_name is None:
        resolved_provider_name = configured_llm_provider_name()
    else:
        if not isinstance(provider_name, str):
            raise TypeError(
                "Historical LLM provider override must be text"
            )

        resolved_provider_name = provider_name.strip()

        if not resolved_provider_name:
            raise HistoricalLLMRunConfigurationError(
                "Historical LLM provider override cannot be blank"
            )

    if resolved_provider_name.lower() == "mock":
        raise HistoricalLLMRunConfigurationError(
            "Real LLM historical Investigation refuses the mock provider"
        )

    resolved_limits = (
        limits
        if limits is not None
        else InvestigationLimits(
            max_iterations=6,
            max_tool_calls=10,
            timeout_seconds=30,
        )
    )

    if not isinstance(
        resolved_limits,
        InvestigationLimits,
    ):
        raise TypeError(
            "Historical LLM Investigation limits are invalid"
        )

    try:
        if provider_name is None:
            gateway = create_llm_gateway()
        else:
            gateway = create_llm_gateway(
                provider_name=resolved_provider_name,
            )
    except Exception:
        raise HistoricalLLMRunConfigurationError(
            "Shared LLM Gateway could not be constructed"
        ) from None

    adapter = InvestigationLLMGatewayAdapter(
        gateway
    )

    reasoner = LLMInvestigationReasoner(
        adapter
    )

    investigation_settings = InvestigationSettings(
        enabled=True,
        acknowledgement=(
            INVESTIGATION_ENABLE_ACKNOWLEDGEMENT
        ),
        limits=resolved_limits,
    )

    coordinator = EvidenceDrivenInvestigationCoordinator(
        reasoner=reasoner,
        probe_executor=HistoricalRuntimeProbeGuard(),
        limits=resolved_limits,
    )

    runtime = object.__new__(
        AgentRuntime
    )

    runtime.llm_gateway = gateway
    runtime.historical_llm_provider_name = (
        resolved_provider_name
    )
    runtime.investigation_settings = (
        investigation_settings
    )
    runtime.investigation_coordinator = (
        coordinator
    )

    return runtime


async def run_real_llm_historical_incident(
    path: str | Path,
    *,
    replay_at: datetime | None = None,
    limits: InvestigationLimits | None = None,
    provider_name: str | None = None,
) -> HistoricalIncidentInvestigationResult:
    runtime = create_historical_llm_runtime(
        limits=limits,
        provider_name=provider_name,
    )

    runner = HistoricalIncidentInvestigationRunner(
        runtime
    )

    return await runner.run_file(
        path,
        replay_at=replay_at,
    )


def safe_result_payload(
    result: HistoricalIncidentInvestigationResult,
    *,
    provider_name: str | None = None,
) -> dict[str, Any]:
    if not isinstance(
        result,
        HistoricalIncidentInvestigationResult,
    ):
        raise TypeError(
            "Historical LLM Investigation result is invalid"
        )

    investigation = result.investigation
    conclusion = investigation.conclusion

    configured_provider = (
        provider_name
        if provider_name is not None
        else configured_llm_provider_name()
    )

    return {
        "schema_version": "v1",
        "run_mode": (
            "real_llm_historical_investigation"
        ),
        "configured_provider": configured_provider,
        "incident_id": result.incident_id,
        "incident_time": (
            result.incident_time.isoformat()
        ),
        "replay_at": result.replay_at.isoformat(),
        "shadow_mode": True,
        "read_only": True,
        "decision_influence": False,
        "agent": {
            "status": investigation.status.value,
            "stop_reason": (
                investigation.stop_reason.value
                if investigation.stop_reason is not None
                else None
            ),
            "iteration_count": (
                investigation.iteration_count
            ),
            "tool_call_count": (
                investigation.tool_call_count
            ),
            "attempted_probes": [
                probe.value
                for probe
                in investigation.attempted_probes
            ],
            "hypotheses": [
                item.model_dump(
                    mode="json"
                )
                for item
                in investigation.hypotheses
            ],
            "evidence": [
                item.model_dump(
                    mode="json"
                )
                for item
                in investigation.evidence
            ],
            "conclusion": (
                conclusion.model_dump(
                    mode="json"
                )
                if conclusion is not None
                else None
            ),
        },
    }


def _parse_replay_at(
    value: str | None,
) -> datetime | None:

    if value is None:
        return None

    text = value.strip()

    if not text:
        raise HistoricalLLMRunConfigurationError(
            "replay_at cannot be blank"
        )

    if text.endswith(
        "Z"
    ):
        text = (
            f"{text[:-1]}+00:00"
        )

    try:
        parsed = datetime.fromisoformat(
            text
        )

    except ValueError as exc:
        raise HistoricalLLMRunConfigurationError(
            "replay_at is invalid"
        ) from exc

    if parsed.tzinfo is None:
        raise HistoricalLLMRunConfigurationError(
            "replay_at must be timezone-aware"
        )

    return parsed.astimezone(
        UTC
    )


async def _async_main(
    args,
) -> int:
    replay_at = _parse_replay_at(
        args.replay_at
    )

    result = await run_real_llm_historical_incident(
        args.incident,
        replay_at=replay_at,
        provider_name=args.provider,
    )

    payload = safe_result_payload(
        result,
        provider_name=args.provider,
    )

    output_path = Path(
        args.output
    )

    output_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    agent = payload["agent"]

    print(
        "Historical LLM Investigation completed"
    )
    print(
        f"Provider: {payload['configured_provider']}"
    )
    print(
        f"Incident: {payload['incident_id']}"
    )
    print(
        f"Status: {agent['status']}"
    )
    print(
        f"Stop reason: {agent['stop_reason']}"
    )
    print(
        "Attempted probes: "
        + ", ".join(
            agent["attempted_probes"]
        )
    )

    conclusion = agent["conclusion"]

    if conclusion is None:
        print("Conclusion: NONE")
    else:
        print(
            "Conclusion: "
            + str(
                conclusion.get(
                    "root_cause"
                )
            )
        )

    print(
        f"Result file: {output_path}"
    )

    return 0


def main(
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run one real LLM historical "
            "SRE Investigation"
        )
    )

    parser.add_argument(
        "incident",
        help=(
            "Validated Real Incident Dataset JSON"
        ),
    )

    parser.add_argument(
        "--replay-at",
        default=None,
        help=(
            "Optional timezone-aware ISO-8601 "
            "point-in-time replay cutoff"
        ),
    )

    parser.add_argument(
        "--output",
        default=(
            "historical_llm_"
            "investigation_result.json"
        ),
        help=(
            "Safe Agent result JSON output path"
        ),
    )

    parser.add_argument(
        "--provider",
        default=None,
        help=(
            "Explicit non-mock provider override. "
            "Example: openai. "
            "If omitted, configs/app.yaml remains "
            "the source of truth."
        ),
    )

    args = parser.parse_args()

    try:
        return asyncio.run(
            _async_main(
                args
            )
        )
    except Exception as exc:
        print(
            f"{type(exc).__name__}: "
            f"{str(exc)}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )


__all__ = [
    "HistoricalLLMRunConfigurationError",
    "HistoricalRuntimeProbeGuard",
    "configured_llm_provider_name",
    "create_historical_llm_runtime",
    "run_real_llm_historical_incident",
    "safe_result_payload",
]
