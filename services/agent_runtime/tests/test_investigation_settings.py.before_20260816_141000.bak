import pytest
from pydantic import ValidationError

from services.agent_runtime.app.investigation.settings import (
    INVESTIGATION_ENABLE_ACKNOWLEDGEMENT,
    InvestigationConfigurationError,
    InvestigationSettings,
)


def test_default_environment_is_disabled():
    settings = InvestigationSettings.from_environment(
        {}
    )

    assert settings.enabled is False
    assert settings.shadow_mode is True
    assert settings.acknowledgement is None
    assert settings.limits.max_iterations == 6
    assert settings.limits.max_tool_calls == 10
    assert settings.limits.timeout_seconds == 30.0


def test_enabled_environment_requires_exact_acknowledgement():
    with pytest.raises(
        InvestigationConfigurationError,
        match="configuration is invalid",
    ):
        InvestigationSettings.from_environment(
            {
                "AGENT_INVESTIGATION_SHADOW_ENABLED": (
                    "true"
                ),
                "AGENT_INVESTIGATION_SHADOW_ACKNOWLEDGEMENT": (
                    "wrong"
                ),
            }
        )


def test_enabled_environment_loads_bounded_limits():
    settings = InvestigationSettings.from_environment(
        {
            "AGENT_INVESTIGATION_SHADOW_ENABLED": "true",
            "AGENT_INVESTIGATION_SHADOW_ACKNOWLEDGEMENT": (
                INVESTIGATION_ENABLE_ACKNOWLEDGEMENT
            ),
            "AGENT_INVESTIGATION_MAX_ITERATIONS": "4",
            "AGENT_INVESTIGATION_MAX_TOOL_CALLS": "7",
            "AGENT_INVESTIGATION_TIMEOUT_SECONDS": "12.5",
        }
    )

    assert settings.enabled is True
    assert settings.limits.max_iterations == 4
    assert settings.limits.max_tool_calls == 7
    assert settings.limits.timeout_seconds == 12.5


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (
            "AGENT_INVESTIGATION_SHADOW_ENABLED",
            "sometimes",
        ),
        (
            "AGENT_INVESTIGATION_MAX_ITERATIONS",
            "many",
        ),
        (
            "AGENT_INVESTIGATION_MAX_TOOL_CALLS",
            "0",
        ),
        (
            "AGENT_INVESTIGATION_TIMEOUT_SECONDS",
            "3600",
        ),
    ],
)
def test_invalid_environment_fails_closed(
    name,
    value,
):
    environment = {
        name: value
    }

    with pytest.raises(
        InvestigationConfigurationError,
        match="configuration is invalid",
    ):
        InvestigationSettings.from_environment(
            environment
        )


def test_settings_are_frozen_and_extra_fields_are_rejected():
    settings = InvestigationSettings()

    with pytest.raises(ValidationError):
        settings.enabled = True

    with pytest.raises(ValidationError):
        InvestigationSettings.model_validate(
            {
                "unexpected": True
            }
        )

