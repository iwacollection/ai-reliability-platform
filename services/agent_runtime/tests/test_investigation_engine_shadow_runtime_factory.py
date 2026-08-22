from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.agent_runtime.app.investigation.engine import (
    CustomInvestigationEngine,
)
from services.agent_runtime.app.investigation.engine_shadow_gate import (
    INVESTIGATION_LANGGRAPH_SHADOW_ACKNOWLEDGEMENT,
    InvestigationEngineShadowEvidence,
    InvestigationEngineShadowGateCode,
    InvestigationEngineShadowSettings,
)
from services.agent_runtime.app.investigation.engine_shadow_runtime_factory import (
    InvestigationEngineShadowRuntimeFactoryError,
    create_investigation_engine_shadow_runtime,
    plan_investigation_engine_shadow_runtime,
)
from services.agent_runtime.app.investigation.langgraph_engine import (
    LangGraphInvestigationEngine,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)
from services.agent_runtime.app.investigation.session_runtime_factory import (
    create_investigation_session_runtime,
)
from services.agent_runtime.app.investigation.session_runtime_settings import (
    INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT,
    InvestigationSessionRuntimeSettings,
)

MATRIX_DIGEST = "a" * 64
RELEASE_DIGEST = "b" * 64


class FakeReasoner(BaseInvestigationReasoner):
    async def decide(self, scope, state):
        raise AssertionError("Shadow Runtime wiring ran the reasoner")


class FakeProbeExecutor:
    async def collect(self, context, scope, probe):
        raise AssertionError("Shadow Runtime wiring ran a probe")


def evidence(now: datetime) -> InvestigationEngineShadowEvidence:
    return InvestigationEngineShadowEvidence(
        matrix_digest=MATRIX_DIGEST,
        release_digest=RELEASE_DIGEST,
        generated_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=30),
    )


def primary_settings(path: Path) -> InvestigationSessionRuntimeSettings:
    return InvestigationSessionRuntimeSettings(
        enabled=True,
        acknowledgement=(INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT),
        db_path=str(path),
    )


def shadow_settings(
    path: Path,
    **changes,
) -> InvestigationEngineShadowSettings:
    values = {
        "enabled": True,
        "acknowledgement": (INVESTIGATION_LANGGRAPH_SHADOW_ACKNOWLEDGEMENT),
        "kill_switch_engaged": False,
        "shadow_db_path": str(path),
        "expected_matrix_digest": MATRIX_DIGEST,
        "expected_release_digest": RELEASE_DIGEST,
    }
    values.update(changes)
    return InvestigationEngineShadowSettings(**values)


def test_disabled_plan_and_factory_return_before_dependencies_or_database(
    tmp_path,
):
    shadow_path = tmp_path / "must_not_exist.db"
    plan = plan_investigation_engine_shadow_runtime(
        settings=InvestigationEngineShadowSettings(shadow_db_path=str(shadow_path)),
        evidence=object(),
        primary_settings=object(),
    )

    result = create_investigation_engine_shadow_runtime(
        plan=plan,
        primary_components=None,
        reasoner=object(),
        probe_executor=object(),
        require_cluster_verified_evidence="invalid",
    )

    assert plan.decision.code == InvestigationEngineShadowGateCode.DISABLED
    assert result is None
    assert not shadow_path.exists()


@pytest.mark.parametrize(
    ("settings_changes", "expected_code"),
    [
        (
            {"kill_switch_engaged": True},
            InvestigationEngineShadowGateCode.KILL_SWITCH_ENGAGED,
        ),
        (
            {},
            InvestigationEngineShadowGateCode.EVIDENCE_MISSING,
        ),
    ],
)
def test_denied_plan_never_creates_shadow_store(
    tmp_path,
    settings_changes,
    expected_code,
):
    now = datetime.now(UTC)
    shadow_path = tmp_path / "denied.db"
    plan = plan_investigation_engine_shadow_runtime(
        settings=shadow_settings(
            shadow_path,
            **settings_changes,
        ),
        evidence=(evidence(now) if settings_changes.get("kill_switch_engaged") else None),
        primary_settings=primary_settings(tmp_path / "primary.db"),
        now=now,
    )

    result = create_investigation_engine_shadow_runtime(
        plan=plan,
        primary_components=None,
        reasoner=None,
    )

    assert plan.decision.code == expected_code
    assert result is None
    assert not shadow_path.exists()


def test_allowed_factory_builds_isolated_langgraph_over_shared_capability(
    tmp_path,
):
    now = datetime.now(UTC)
    primary_path = tmp_path / "primary.db"
    shadow_path = tmp_path / "shadow.db"
    reasoner = FakeReasoner()
    probes = FakeProbeExecutor()
    primary = create_investigation_session_runtime(
        settings=primary_settings(primary_path),
        reasoner=reasoner,
        probe_executor=probes,
        require_cluster_verified_evidence=True,
    )
    assert primary is not None
    assert isinstance(primary.engine, CustomInvestigationEngine)

    plan = plan_investigation_engine_shadow_runtime(
        settings=shadow_settings(shadow_path),
        evidence=evidence(now),
        primary_settings=primary_settings(primary_path),
        now=now,
    )
    result = create_investigation_engine_shadow_runtime(
        plan=plan,
        primary_components=primary,
        reasoner=reasoner,
        probe_executor=probes,
        require_cluster_verified_evidence=True,
    )

    assert result is not None
    assert plan.decision.allowed is True
    assert isinstance(result.engine, LangGraphInvestigationEngine)
    assert result.store.db_path == shadow_path
    assert result.store.db_path != primary.store.db_path
    assert result.service is not primary.service
    assert result.driver.reasoner is reasoner
    assert result.driver.probe_executor is probes
    assert result.driver.require_cluster_verified_evidence is True
    assert result.engine.session_service is result.service
    assert result.engine.checkpointer_enabled is False
    assert primary_path.exists()
    assert shadow_path.exists()


def test_allowed_plan_requires_enabled_primary_before_database_access(
    tmp_path,
):
    now = datetime.now(UTC)
    shadow_path = tmp_path / "must_not_exist.db"

    with pytest.raises(
        InvestigationEngineShadowRuntimeFactoryError,
        match="enabled Custom primary Runtime",
    ):
        plan_investigation_engine_shadow_runtime(
            settings=shadow_settings(shadow_path),
            evidence=evidence(now),
            primary_settings=InvestigationSessionRuntimeSettings(
                db_path=str(tmp_path / "primary.db")
            ),
            now=now,
        )

    assert not shadow_path.exists()


def test_allowed_factory_requires_active_custom_primary_without_shadow_write(
    tmp_path,
):
    now = datetime.now(UTC)
    shadow_path = tmp_path / "must_not_exist.db"
    plan = plan_investigation_engine_shadow_runtime(
        settings=shadow_settings(shadow_path),
        evidence=evidence(now),
        primary_settings=primary_settings(tmp_path / "primary.db"),
        now=now,
    )

    with pytest.raises(
        InvestigationEngineShadowRuntimeFactoryError,
        match="active Custom primary components",
    ):
        create_investigation_engine_shadow_runtime(
            plan=plan,
            primary_components=None,
            reasoner=FakeReasoner(),
            probe_executor=FakeProbeExecutor(),
        )

    assert not shadow_path.exists()
