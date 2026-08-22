from __future__ import annotations

from dataclasses import dataclass

from services.agent_runtime.app.investigation.engine import (
    BaseInvestigationEngine,
    CustomInvestigationEngine,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)
from services.agent_runtime.app.investigation.session_driver import (
    DurableInvestigationSessionDriver,
)
from services.agent_runtime.app.investigation.session_loop import (
    DurableInvestigationSessionLoop,
)
from services.agent_runtime.app.investigation.session_runtime_settings import (
    InvestigationEngineBackend,
    InvestigationSessionRuntimeSettings,
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionService,
)
from services.agent_runtime.app.investigation.session_store import (
    InvestigationSessionStore,
)


class InvestigationSessionRuntimeFactoryError(RuntimeError):
    """Enabled Session Runtime cannot be assembled safely."""


@dataclass(frozen=True)
class InvestigationSessionRuntimeComponents:
    store: InvestigationSessionStore
    service: InvestigationSessionService
    driver: DurableInvestigationSessionDriver
    loop: DurableInvestigationSessionLoop
    engine: BaseInvestigationEngine


def create_investigation_session_runtime(
    *,
    settings: InvestigationSessionRuntimeSettings | None = None,
    reasoner: BaseInvestigationReasoner | None = None,
    probe_executor=None,
    require_cluster_verified_evidence: bool = False,
) -> InvestigationSessionRuntimeComponents | None:
    """
    Build one shared Store -> Service -> Driver -> Loop object graph.

    Disabled mode returns before validating dependencies, resolving the path,
    creating a directory, opening SQLite, or touching LLM/Probe capability.
    """

    resolved_settings = (
        settings
        if settings is not None
        else InvestigationSessionRuntimeSettings.from_environment()
    )
    if not isinstance(
        resolved_settings,
        InvestigationSessionRuntimeSettings,
    ):
        raise TypeError(
            "Investigation Session Runtime settings are invalid"
        )
    if not resolved_settings.enabled:
        return None
    if not isinstance(reasoner, BaseInvestigationReasoner):
        raise InvestigationSessionRuntimeFactoryError(
            "Enabled Investigation Session Runtime requires a reasoner"
        )
    if probe_executor is None or not callable(
        getattr(probe_executor, "collect", None)
    ):
        raise InvestigationSessionRuntimeFactoryError(
            "Enabled Investigation Session Runtime requires a read-only probe executor"
        )
    if not isinstance(require_cluster_verified_evidence, bool):
        raise TypeError(
            "Investigation Session cluster evidence policy is invalid"
        )

    store = InvestigationSessionStore(
        resolved_settings.db_path
    )
    service = InvestigationSessionService(store)
    driver = DurableInvestigationSessionDriver(
        session_service=service,
        reasoner=reasoner,
        probe_executor=probe_executor,
        require_cluster_verified_evidence=(
            require_cluster_verified_evidence
        ),
    )
    loop = DurableInvestigationSessionLoop(
        session_service=service,
        session_driver=driver,
    )
    if (
        resolved_settings.engine_backend
        == InvestigationEngineBackend.LANGGRAPH
    ):
        # Import only after explicit enablement and acknowledgement. Disabled
        # and custom modes do not import or construct LangGraph machinery.
        from services.agent_runtime.app.investigation.langgraph_engine import (
            LangGraphInvestigationEngine,
        )

        engine: BaseInvestigationEngine = (
            LangGraphInvestigationEngine(
                session_service=service,
                session_driver=driver,
            )
        )
    else:
        engine = CustomInvestigationEngine(
            session_service=service,
            session_loop=loop,
        )
    return InvestigationSessionRuntimeComponents(
        store=store,
        service=service,
        driver=driver,
        loop=loop,
        engine=engine,
    )


__all__ = [
    "InvestigationSessionRuntimeComponents",
    "InvestigationSessionRuntimeFactoryError",
    "create_investigation_session_runtime",
]
