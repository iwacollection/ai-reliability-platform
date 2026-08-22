from pathlib import Path

import pytest
from pydantic import ValidationError

from services.agent_runtime.app.investigation.session_runtime_settings import (
    INVESTIGATION_LANGGRAPH_ENGINE_ACKNOWLEDGEMENT,
    INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT,
    InvestigationEngineBackend,
    InvestigationSessionRuntimeConfigurationError,
    InvestigationSessionRuntimeSettings,
)


def test_default_environment_is_disabled_and_bounded():
    settings = InvestigationSessionRuntimeSettings.from_environment({})

    assert settings.enabled is False
    assert settings.acknowledgement is None
    assert settings.db_path == "data/investigation_sessions.db"
    assert settings.engine_backend == InvestigationEngineBackend.CUSTOM
    assert settings.langgraph_acknowledgement is None


def test_enabled_environment_requires_exact_acknowledgement():
    with pytest.raises(
        InvestigationSessionRuntimeConfigurationError,
        match="configuration is invalid",
    ):
        InvestigationSessionRuntimeSettings.from_environment(
            {
                "AGENT_INVESTIGATION_SESSION_RUNTIME_ENABLED": "true",
                "AGENT_INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT": (
                    "wrong"
                ),
            }
        )


def test_enabled_environment_loads_explicit_database_path(tmp_path):
    db_path = tmp_path / "durable_sessions.db"
    settings = InvestigationSessionRuntimeSettings.from_environment(
        {
            "AGENT_INVESTIGATION_SESSION_RUNTIME_ENABLED": "true",
            "AGENT_INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT": (
                INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT
            ),
            "AGENT_INVESTIGATION_SESSION_DB_PATH": str(db_path),
        }
    )

    assert settings.enabled is True
    assert Path(settings.db_path) == db_path
    assert not db_path.exists()


def test_enabled_langgraph_environment_requires_exact_acknowledgement(
    tmp_path,
):
    values = {
        "AGENT_INVESTIGATION_SESSION_RUNTIME_ENABLED": "true",
        "AGENT_INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT": (
            INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT
        ),
        "AGENT_INVESTIGATION_SESSION_ENGINE": "langgraph",
        "AGENT_INVESTIGATION_SESSION_DB_PATH": str(
            tmp_path / "langgraph.db"
        ),
    }
    with pytest.raises(
        InvestigationSessionRuntimeConfigurationError,
        match="configuration is invalid",
    ):
        InvestigationSessionRuntimeSettings.from_environment(
            values
        )

    values[
        "AGENT_INVESTIGATION_LANGGRAPH_ACKNOWLEDGEMENT"
    ] = INVESTIGATION_LANGGRAPH_ENGINE_ACKNOWLEDGEMENT
    settings = (
        InvestigationSessionRuntimeSettings.from_environment(
            values
        )
    )

    assert settings.enabled is True
    assert (
        settings.engine_backend
        == InvestigationEngineBackend.LANGGRAPH
    )


def test_disabled_langgraph_selection_has_no_enablement_side_effect():
    settings = InvestigationSessionRuntimeSettings.from_environment(
        {
            "AGENT_INVESTIGATION_SESSION_ENGINE": "langgraph",
        }
    )

    assert settings.enabled is False
    assert (
        settings.engine_backend
        == InvestigationEngineBackend.LANGGRAPH
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (
            "AGENT_INVESTIGATION_SESSION_RUNTIME_ENABLED",
            "sometimes",
        ),
        (
            "AGENT_INVESTIGATION_SESSION_DB_PATH",
            "sessions.sqlite",
        ),
        (
            "AGENT_INVESTIGATION_SESSION_DB_PATH",
            "bad\x00sessions.db",
        ),
    ],
)
def test_invalid_environment_fails_closed(name, value):
    with pytest.raises(
        InvestigationSessionRuntimeConfigurationError,
        match="configuration is invalid",
    ):
        InvestigationSessionRuntimeSettings.from_environment(
            {name: value}
        )


def test_settings_are_frozen_and_extra_fields_are_rejected():
    settings = InvestigationSessionRuntimeSettings()

    with pytest.raises(ValidationError):
        settings.enabled = True

    with pytest.raises(ValidationError):
        InvestigationSessionRuntimeSettings.model_validate(
            {"unexpected": True}
        )
