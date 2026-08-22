from __future__ import annotations

from pathlib import Path

import pytest

import services.agent_runtime.app.runtime.runtime as runtime_module

from common.config.settings import AuthenticationConfig
from services.agent_runtime.app.investigation.dsh_reasoner_runtime_settings import (
    INVESTIGATION_DSH_REASONER_ACKNOWLEDGEMENT,
    DshInvestigationReasonerRuntimeSettings,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)
from services.agent_runtime.app.investigation.session_runtime_settings import (
    INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT,
    InvestigationSessionRuntimeSettings,
)
from services.agent_runtime.app.investigation.settings import (
    INVESTIGATION_ENABLE_ACKNOWLEDGEMENT,
    InvestigationSettings,
)
from services.agent_runtime.app.security.factory import (
    create_authentication_service,
)


DSH_ENV_NAMES = [
    "AGENT_INVESTIGATION_DSH_REASONER_ENABLED",
    "AGENT_INVESTIGATION_DSH_REASONER_ACKNOWLEDGEMENT",
    "AGENT_INVESTIGATION_DSH_RUNTIME_EXECUTABLE",
    "AGENT_INVESTIGATION_DSH_RUNTIME_ENTRYPOINT",
    "AGENT_INVESTIGATION_DSH_CORDIS_CONFIG_PATH",
    "AGENT_INVESTIGATION_DSH_SESSION_ROOT",
    "AGENT_INVESTIGATION_DSH_EXPECTED_RUNTIME_VERSION",
    "AGENT_INVESTIGATION_DSH_PROVIDER",
    "AGENT_INVESTIGATION_DSH_MODEL",
    "AGENT_INVESTIGATION_DSH_MAX_TOKENS",
    "AGENT_INVESTIGATION_DSH_REQUEST_TIMEOUT_SECONDS",
    "AGENT_INVESTIGATION_DSH_TURN_TIMEOUT_SECONDS",
    "AGENT_INVESTIGATION_DSH_SHUTDOWN_TIMEOUT_SECONDS",
]


class FakeReasoner(BaseInvestigationReasoner):
    async def decide(self, scope, state):
        raise AssertionError(
            "Runtime wiring must not execute the reasoner"
        )


def disabled_authentication_service():
    return create_authentication_service(
        AuthenticationConfig()
    )


def isolate_runtime(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.chdir(tmp_path)
    for name in DSH_ENV_NAMES:
        monkeypatch.delenv(
            name,
            raising=False,
        )

    # Keep this wiring test independent from production network/preflight
    # infrastructure. These are the same side-effect boundaries used by the
    # existing Runtime Investigation wiring tests.
    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_preflight_resolver",
        lambda: None,
    )
    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_production_executor",
        lambda **_: None,
    )
    monkeypatch.setattr(
        runtime_module,
        "create_production_pilot_live_readiness_probe",
        lambda: None,
    )


def enabled_session_settings(
    db_path: Path,
):
    return InvestigationSessionRuntimeSettings(
        enabled=True,
        acknowledgement=(
            INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT
        ),
        db_path=str(db_path),
    )


def enabled_investigation_settings():
    return InvestigationSettings(
        enabled=True,
        acknowledgement=(
            INVESTIGATION_ENABLE_ACKNOWLEDGEMENT
        ),
    )


def enabled_dsh_settings():
    # The DSH factory is monkeypatched in these Runtime wiring tests. The
    # settings model still requires explicit, non-empty runtime bindings,
    # proving that Runtime cannot silently enable DSH.
    return DshInvestigationReasonerRuntimeSettings(
        enabled=True,
        acknowledgement=(
            INVESTIGATION_DSH_REASONER_ACKNOWLEDGEMENT
        ),
        runtime_executable="X:/bound/node.exe",
        runtime_entrypoint="X:/bound/packaged-bin.js",
        cordis_config_path="X:/bound/reasoner.yml",
        session_root="X:/bound/sessions",
        expected_runtime_version="0.1.0-rc.7",
    )


def test_default_runtime_keeps_dsh_reasoner_disabled(
    monkeypatch,
    tmp_path: Path,
):
    isolate_runtime(
        monkeypatch,
        tmp_path,
    )
    calls = []

    monkeypatch.setattr(
        runtime_module,
        "create_dsh_investigation_reasoner",
        lambda **kwargs: calls.append(
            kwargs
        ),
    )

    runtime = runtime_module.AgentRuntime(
        authentication_service=(
            disabled_authentication_service()
        ),
    )

    assert (
        runtime.dsh_investigation_reasoner_runtime_settings.enabled
        is False
    )
    assert runtime.investigation_reasoner is None
    assert calls == []
    assert runtime.investigation_session_store is None


def test_enabled_dsh_reasoner_is_shared_by_coordinator_and_durable_driver(
    monkeypatch,
    tmp_path: Path,
):
    isolate_runtime(
        monkeypatch,
        tmp_path,
    )

    dsh_reasoner = FakeReasoner()
    calls = []

    def fake_factory(
        *,
        settings,
        cwd,
    ):
        calls.append(
            {
                "settings": settings,
                "cwd": Path(cwd),
            }
        )
        return dsh_reasoner

    monkeypatch.setattr(
        runtime_module,
        "create_dsh_investigation_reasoner",
        fake_factory,
    )

    dsh_settings = enabled_dsh_settings()
    runtime = runtime_module.AgentRuntime(
        authentication_service=(
            disabled_authentication_service()
        ),
        investigation_settings=(
            enabled_investigation_settings()
        ),
        investigation_session_runtime_settings=(
            enabled_session_settings(
                tmp_path / "dsh-runtime.db"
            )
        ),
        dsh_investigation_reasoner_runtime_settings=(
            dsh_settings
        ),
    )

    assert len(calls) == 1
    assert calls[0]["settings"] is dsh_settings
    assert calls[0]["cwd"] == tmp_path.resolve()

    assert runtime.investigation_reasoner is dsh_reasoner
    assert (
        runtime.investigation_coordinator.reasoner
        is dsh_reasoner
    )
    assert (
        runtime.investigation_session_driver.reasoner
        is dsh_reasoner
    )
    assert (
        runtime.investigation_session_driver.probe_executor
        is runtime.investigation_probe_executor
    )
    assert (
        runtime.investigation_coordinator.probe_executor
        is runtime.investigation_probe_executor
    )


def test_dsh_reasoner_enablement_conflicts_with_explicit_reasoner(
    monkeypatch,
    tmp_path: Path,
):
    isolate_runtime(
        monkeypatch,
        tmp_path,
    )
    calls = []

    monkeypatch.setattr(
        runtime_module,
        "create_dsh_investigation_reasoner",
        lambda **kwargs: calls.append(
            kwargs
        ),
    )

    with pytest.raises(
        TypeError,
        match="conflicts with explicitly injected",
    ):
        runtime_module.AgentRuntime(
            authentication_service=(
                disabled_authentication_service()
            ),
            investigation_reasoner=FakeReasoner(),
            investigation_session_runtime_settings=(
                enabled_session_settings(
                    tmp_path / "conflict.db"
                )
            ),
            dsh_investigation_reasoner_runtime_settings=(
                enabled_dsh_settings()
            ),
        )

    assert calls == []
    assert not (
        tmp_path / "conflict.db"
    ).exists()


def test_dsh_reasoner_setting_alone_does_not_enable_investigation(
    monkeypatch,
    tmp_path: Path,
):
    isolate_runtime(
        monkeypatch,
        tmp_path,
    )
    calls = []

    monkeypatch.setattr(
        runtime_module,
        "create_dsh_investigation_reasoner",
        lambda **kwargs: calls.append(
            kwargs
        ),
    )

    runtime = runtime_module.AgentRuntime(
        authentication_service=(
            disabled_authentication_service()
        ),
        dsh_investigation_reasoner_runtime_settings=(
            enabled_dsh_settings()
        ),
    )

    assert calls == []
    assert runtime.investigation_reasoner is None
    assert runtime.investigation_probe_executor is None
    assert runtime.investigation_session_store is None


def test_existing_explicit_reasoner_path_is_unchanged_when_dsh_disabled(
    monkeypatch,
    tmp_path: Path,
):
    isolate_runtime(
        monkeypatch,
        tmp_path,
    )

    reasoner = FakeReasoner()
    runtime = runtime_module.AgentRuntime(
        authentication_service=(
            disabled_authentication_service()
        ),
        investigation_reasoner=reasoner,
        investigation_session_runtime_settings=(
            enabled_session_settings(
                tmp_path / "existing.db"
            )
        ),
        dsh_investigation_reasoner_runtime_settings=(
            DshInvestigationReasonerRuntimeSettings()
        ),
    )

    assert runtime.investigation_reasoner is reasoner
    assert (
        runtime.investigation_session_driver.reasoner
        is reasoner
    )
