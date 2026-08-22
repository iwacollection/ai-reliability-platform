from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from services.agent_runtime.app.investigation.engine import (
    CustomInvestigationEngine,
)
from services.agent_runtime.app.investigation.engine_shadow_gate import (
    InvestigationEngineShadowEvidence,
    InvestigationEngineShadowGate,
    InvestigationEngineShadowGateDecision,
    InvestigationEngineShadowSettings,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)
from services.agent_runtime.app.investigation.session_driver import (
    DurableInvestigationSessionDriver,
)
from services.agent_runtime.app.investigation.session_runtime_factory import (
    InvestigationSessionRuntimeComponents,
    create_investigation_session_runtime,
)
from services.agent_runtime.app.investigation.session_runtime_settings import (
    INVESTIGATION_LANGGRAPH_ENGINE_ACKNOWLEDGEMENT,
    INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT,
    InvestigationEngineBackend,
    InvestigationSessionRuntimeSettings,
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionService,
)
from services.agent_runtime.app.investigation.session_store import (
    InvestigationSessionStore,
)

if TYPE_CHECKING:
    from services.agent_runtime.app.investigation.langgraph_engine import (
        LangGraphInvestigationEngine,
    )


class InvestigationEngineShadowRuntimeFactoryError(RuntimeError):
    """Guarded Shadow Runtime cannot be assembled safely."""


@dataclass(frozen=True)
class InvestigationEngineShadowRuntimePlan:
    """Pure startup decision produced before any Runtime side effect."""

    settings: InvestigationEngineShadowSettings
    evidence: InvestigationEngineShadowEvidence | None
    decision: InvestigationEngineShadowGateDecision


@dataclass(frozen=True)
class InvestigationEngineShadowRuntimeComponents:
    """Isolated LangGraph Shadow object graph owned only by AgentRuntime."""

    decision: InvestigationEngineShadowGateDecision
    store: InvestigationSessionStore
    service: InvestigationSessionService
    driver: DurableInvestigationSessionDriver
    engine: "LangGraphInvestigationEngine"


def plan_investigation_engine_shadow_runtime(
    *,
    settings: InvestigationEngineShadowSettings | None = None,
    evidence: InvestigationEngineShadowEvidence | None = None,
    primary_settings: InvestigationSessionRuntimeSettings | None = None,
    now: datetime | None = None,
    gate: InvestigationEngineShadowGate | None = None,
) -> InvestigationEngineShadowRuntimePlan:
    """
    Evaluate Shadow eligibility without opening SQLite or touching LLM/tools.

    Disabled mode deliberately returns before validating evidence or primary
    Runtime dependencies. Enabled-but-denied mode remains a bounded No-Go and
    also creates no durable component.
    """

    resolved_settings = (
        settings if settings is not None else InvestigationEngineShadowSettings.from_environment()
    )
    if not isinstance(
        resolved_settings,
        InvestigationEngineShadowSettings,
    ):
        raise TypeError("Investigation Engine Shadow settings are invalid")

    resolved_gate = gate or InvestigationEngineShadowGate()
    if not isinstance(
        resolved_gate,
        InvestigationEngineShadowGate,
    ):
        raise TypeError("Investigation Engine Shadow Gate is invalid")

    if not resolved_settings.enabled:
        decision = resolved_gate.evaluate(
            settings=resolved_settings,
            evidence=None,
            primary_backend=InvestigationEngineBackend.CUSTOM,
            primary_db_path="data/investigation_sessions.db",
            now=now or datetime.now(UTC),
        )
        return InvestigationEngineShadowRuntimePlan(
            settings=resolved_settings,
            evidence=None,
            decision=decision,
        )

    if evidence is not None and not isinstance(
        evidence,
        InvestigationEngineShadowEvidence,
    ):
        raise TypeError("Investigation Engine Shadow evidence is invalid")

    resolved_primary_settings = (
        primary_settings if primary_settings is not None else InvestigationSessionRuntimeSettings()
    )
    if not isinstance(
        resolved_primary_settings,
        InvestigationSessionRuntimeSettings,
    ):
        raise TypeError("Investigation Engine Shadow primary settings are invalid")

    decision = resolved_gate.evaluate(
        settings=resolved_settings,
        evidence=evidence,
        primary_backend=(resolved_primary_settings.engine_backend),
        primary_db_path=resolved_primary_settings.db_path,
        now=now or datetime.now(UTC),
    )

    if decision.allowed and not resolved_primary_settings.enabled:
        raise InvestigationEngineShadowRuntimeFactoryError(
            "Allowed LangGraph Shadow requires enabled Custom primary Runtime"
        )

    return InvestigationEngineShadowRuntimePlan(
        settings=resolved_settings,
        evidence=evidence,
        decision=decision,
    )


def create_investigation_engine_shadow_runtime(
    *,
    plan: InvestigationEngineShadowRuntimePlan,
    primary_components: InvestigationSessionRuntimeComponents | None,
    reasoner: BaseInvestigationReasoner | None,
    probe_executor=None,
    require_cluster_verified_evidence: bool = False,
) -> InvestigationEngineShadowRuntimeComponents | None:
    """
    Construct one isolated Shadow graph only after an explicit Allow decision.

    The existing Custom Store remains authoritative. The Shadow Store uses a
    different path and is never attached to PlannerPipeline.
    """

    if not isinstance(
        plan,
        InvestigationEngineShadowRuntimePlan,
    ):
        raise TypeError("Investigation Engine Shadow Runtime plan is invalid")
    if not plan.decision.allowed:
        return None
    if not isinstance(
        primary_components,
        InvestigationSessionRuntimeComponents,
    ) or not isinstance(
        primary_components.engine,
        CustomInvestigationEngine,
    ):
        raise InvestigationEngineShadowRuntimeFactoryError(
            "Allowed LangGraph Shadow requires active Custom primary components"
        )
    if not isinstance(reasoner, BaseInvestigationReasoner):
        raise InvestigationEngineShadowRuntimeFactoryError(
            "Allowed LangGraph Shadow requires a reasoner"
        )
    if probe_executor is None or not callable(getattr(probe_executor, "collect", None)):
        raise InvestigationEngineShadowRuntimeFactoryError(
            "Allowed LangGraph Shadow requires a read-only probe executor"
        )
    if not isinstance(require_cluster_verified_evidence, bool):
        raise TypeError("Investigation Engine Shadow cluster evidence policy is invalid")

    shadow_components = create_investigation_session_runtime(
        settings=InvestigationSessionRuntimeSettings(
            enabled=True,
            acknowledgement=(INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT),
            db_path=plan.settings.shadow_db_path,
            engine_backend=InvestigationEngineBackend.LANGGRAPH,
            langgraph_acknowledgement=(INVESTIGATION_LANGGRAPH_ENGINE_ACKNOWLEDGEMENT),
        ),
        reasoner=reasoner,
        probe_executor=probe_executor,
        require_cluster_verified_evidence=(require_cluster_verified_evidence),
    )
    from services.agent_runtime.app.investigation.langgraph_engine import (
        LangGraphInvestigationEngine,
    )

    if (
        shadow_components is None
        or not isinstance(
            shadow_components.engine,
            LangGraphInvestigationEngine,
        )
        or shadow_components.store.db_path == primary_components.store.db_path
        or shadow_components.service is primary_components.service
    ):
        raise InvestigationEngineShadowRuntimeFactoryError(
            "LangGraph Shadow Runtime isolation could not be proven"
        )

    return InvestigationEngineShadowRuntimeComponents(
        decision=plan.decision,
        store=shadow_components.store,
        service=shadow_components.service,
        driver=shadow_components.driver,
        engine=shadow_components.engine,
    )


__all__ = [
    "InvestigationEngineShadowRuntimeComponents",
    "InvestigationEngineShadowRuntimeFactoryError",
    "InvestigationEngineShadowRuntimePlan",
    "create_investigation_engine_shadow_runtime",
    "plan_investigation_engine_shadow_runtime",
]
