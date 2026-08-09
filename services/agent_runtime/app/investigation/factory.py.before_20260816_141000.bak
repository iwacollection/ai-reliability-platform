from services.agent_runtime.app.investigation.coordinator import (
    EvidenceDrivenInvestigationCoordinator,
)
from services.agent_runtime.app.investigation.probes import (
    ReadOnlyInvestigationProbeExecutor,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)
from services.agent_runtime.app.investigation.settings import (
    InvestigationSettings,
)


class InvestigationFactoryError(RuntimeError):
    """
    Enabled Investigation Shadow cannot be safely assembled.
    """


def create_investigation_coordinator(
    *,
    reasoner: BaseInvestigationReasoner | None = None,
    settings: InvestigationSettings | None = None,
    probe_executor=None,
) -> EvidenceDrivenInvestigationCoordinator | None:
    """
    Create the bounded coordinator only when explicitly enabled.

    Disabled mode returns before validating or touching a reasoner, executor,
    Runtime ToolManager, LLM gateway, credential, database or network client.
    """

    resolved_settings = (
        settings
        if settings is not None
        else InvestigationSettings.from_environment()
    )

    if not isinstance(
        resolved_settings,
        InvestigationSettings,
    ):
        raise TypeError(
            "Investigation settings are invalid"
        )

    if not resolved_settings.enabled:
        return None

    if not isinstance(
        reasoner,
        BaseInvestigationReasoner,
    ):
        raise InvestigationFactoryError(
            "Enabled Investigation Shadow requires a reasoner"
        )

    resolved_executor = (
        probe_executor
        if probe_executor is not None
        else ReadOnlyInvestigationProbeExecutor()
    )

    if not callable(
        getattr(resolved_executor, "collect", None)
    ):
        raise InvestigationFactoryError(
            "Enabled Investigation Shadow requires a read-only probe executor"
        )

    return EvidenceDrivenInvestigationCoordinator(
        reasoner=reasoner,
        probe_executor=resolved_executor,
        limits=resolved_settings.limits,
    )

