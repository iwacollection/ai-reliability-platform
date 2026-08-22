from pathlib import Path

import pytest

from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)
from services.agent_runtime.app.investigation.engine import (
    CustomInvestigationEngine,
)
from services.agent_runtime.app.investigation.session_runtime_factory import (
    InvestigationSessionRuntimeFactoryError,
    create_investigation_session_runtime,
)
from services.agent_runtime.app.investigation.session_runtime_settings import (
    INVESTIGATION_LANGGRAPH_ENGINE_ACKNOWLEDGEMENT,
    INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT,
    InvestigationEngineBackend,
    InvestigationSessionRuntimeSettings,
)


class FakeReasoner(BaseInvestigationReasoner):
    async def decide(self, scope, state):
        raise AssertionError("Factory wiring must not run the reasoner")


class FakeProbeExecutor:
    async def collect(self, context, scope, probe):
        raise AssertionError("Factory wiring must not run a probe")


def enabled_settings(db_path: Path):
    return InvestigationSessionRuntimeSettings(
        enabled=True,
        acknowledgement=(
            INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT
        ),
        db_path=str(db_path),
    )


def test_disabled_factory_returns_before_dependency_or_database_access(
    tmp_path,
):
    db_path = tmp_path / "must_not_exist.db"
    result = create_investigation_session_runtime(
        settings=InvestigationSessionRuntimeSettings(
            db_path=str(db_path)
        ),
        reasoner=object(),
        probe_executor=object(),
        require_cluster_verified_evidence="invalid",
    )

    assert result is None
    assert not db_path.exists()


def test_enabled_factory_requires_reasoner(tmp_path):
    db_path = tmp_path / "sessions.db"
    with pytest.raises(
        InvestigationSessionRuntimeFactoryError,
        match="requires a reasoner",
    ):
        create_investigation_session_runtime(
            settings=enabled_settings(db_path),
            probe_executor=FakeProbeExecutor(),
        )
    assert not db_path.exists()


def test_enabled_factory_requires_read_only_probe_executor(tmp_path):
    db_path = tmp_path / "sessions.db"
    with pytest.raises(
        InvestigationSessionRuntimeFactoryError,
        match="read-only probe executor",
    ):
        create_investigation_session_runtime(
            settings=enabled_settings(db_path),
            reasoner=FakeReasoner(),
            probe_executor=object(),
        )
    assert not db_path.exists()


def test_enabled_factory_builds_one_shared_graph_without_external_calls(
    tmp_path,
):
    db_path = tmp_path / "sessions.db"
    reasoner = FakeReasoner()
    probes = FakeProbeExecutor()

    components = create_investigation_session_runtime(
        settings=enabled_settings(db_path),
        reasoner=reasoner,
        probe_executor=probes,
        require_cluster_verified_evidence=True,
    )

    assert components is not None
    assert components.store.db_path == db_path
    assert components.service.store is components.store
    assert components.driver.session_service is components.service
    assert components.driver.reasoner is reasoner
    assert components.driver.probe_executor is probes
    assert (
        components.driver.require_cluster_verified_evidence
        is True
    )
    assert components.loop.session_service is components.service
    assert components.loop.session_driver is components.driver
    assert isinstance(
        components.engine,
        CustomInvestigationEngine,
    )
    assert components.engine.session_service is components.service
    assert components.engine.session_loop is components.loop
    assert db_path.exists()


def test_enabled_factory_builds_langgraph_over_same_durable_service(
    tmp_path,
):
    from services.agent_runtime.app.investigation.langgraph_engine import (
        LangGraphInvestigationEngine,
    )

    db_path = tmp_path / "langgraph.db"
    components = create_investigation_session_runtime(
        settings=InvestigationSessionRuntimeSettings(
            enabled=True,
            acknowledgement=(
                INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT
            ),
            db_path=str(db_path),
            engine_backend=(
                InvestigationEngineBackend.LANGGRAPH
            ),
            langgraph_acknowledgement=(
                INVESTIGATION_LANGGRAPH_ENGINE_ACKNOWLEDGEMENT
            ),
        ),
        reasoner=FakeReasoner(),
        probe_executor=FakeProbeExecutor(),
    )

    assert components is not None
    assert isinstance(
        components.engine,
        LangGraphInvestigationEngine,
    )
    assert components.engine.session_service is components.service
    assert components.engine.session_driver is components.driver
    assert components.engine.checkpointer_enabled is False
