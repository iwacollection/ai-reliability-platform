from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.agent_runtime.app.investigation.settings import (
    optional_text,
    parse_bool,
)


INVESTIGATION_DSH_REASONER_ACKNOWLEDGEMENT = (
    "I_ENABLE_DSH_INVESTIGATION_REASONER_V1"
)


class DshInvestigationReasonerRuntimeConfigurationError(ValueError):
    """DSH Investigation Reasoner runtime configuration is invalid."""


class DshInvestigationReasonerRuntimeSettings(BaseModel):
    """
    Disabled-default process boundary for the DSH Investigation reasoner.

    Environment parsing is side-effect free. Filesystem/runtime validation
    belongs to the DSH reasoner factory and happens only after explicit
    enablement plus the exact acknowledgement.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    enabled: bool = False
    acknowledgement: str | None = Field(
        default=None,
        max_length=128,
    )

    runtime_executable: str | None = Field(
        default=None,
        max_length=1024,
    )
    runtime_entrypoint: str | None = Field(
        default=None,
        max_length=1024,
    )
    cordis_config_path: str | None = Field(
        default=None,
        max_length=1024,
    )
    session_root: str | None = Field(
        default=None,
        max_length=1024,
    )

    expected_runtime_version: str = Field(
        default="0.1.0-rc.7",
        min_length=1,
        max_length=64,
    )

    provider: str = Field(
        default="deepseek-official",
        min_length=1,
        max_length=128,
    )
    model: str = Field(
        default="deepseek-v4-flash",
        min_length=1,
        max_length=256,
    )
    max_tokens: int = Field(
        default=4096,
        ge=128,
        le=65536,
    )

    request_timeout_seconds: float = Field(
        default=15.0,
        gt=0.0,
        le=120.0,
    )
    turn_timeout_seconds: float = Field(
        default=60.0,
        gt=0.0,
        le=600.0,
    )
    shutdown_timeout_seconds: float = Field(
        default=3.0,
        gt=0.0,
        le=30.0,
    )

    @model_validator(mode="after")
    def validate_enablement(self):
        if not self.enabled:
            return self

        if (
            self.acknowledgement
            != INVESTIGATION_DSH_REASONER_ACKNOWLEDGEMENT
        ):
            raise ValueError(
                "enabled DSH Investigation Reasoner requires exact acknowledgement"
            )

        required_paths = {
            "runtime_executable": self.runtime_executable,
            "runtime_entrypoint": self.runtime_entrypoint,
            "cordis_config_path": self.cordis_config_path,
            "session_root": self.session_root,
        }
        for name, value in required_paths.items():
            if (
                not isinstance(value, str)
                or not value.strip()
                or "\x00" in value
            ):
                raise ValueError(
                    f"enabled DSH Investigation Reasoner requires valid {name}"
                )

        return self

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "DshInvestigationReasonerRuntimeSettings":
        values = (
            environment
            if environment is not None
            else os.environ
        )
        try:
            return cls(
                enabled=parse_bool(
                    values.get(
                        "AGENT_INVESTIGATION_DSH_REASONER_ENABLED"
                    ),
                    default=False,
                    name=(
                        "AGENT_INVESTIGATION_DSH_REASONER_ENABLED"
                    ),
                ),
                acknowledgement=optional_text(
                    values.get(
                        "AGENT_INVESTIGATION_DSH_REASONER_ACKNOWLEDGEMENT"
                    )
                ),
                runtime_executable=optional_text(
                    values.get(
                        "AGENT_INVESTIGATION_DSH_RUNTIME_EXECUTABLE"
                    )
                ),
                runtime_entrypoint=optional_text(
                    values.get(
                        "AGENT_INVESTIGATION_DSH_RUNTIME_ENTRYPOINT"
                    )
                ),
                cordis_config_path=optional_text(
                    values.get(
                        "AGENT_INVESTIGATION_DSH_CORDIS_CONFIG_PATH"
                    )
                ),
                session_root=optional_text(
                    values.get(
                        "AGENT_INVESTIGATION_DSH_SESSION_ROOT"
                    )
                ),
                expected_runtime_version=(
                    optional_text(
                        values.get(
                            "AGENT_INVESTIGATION_DSH_EXPECTED_RUNTIME_VERSION"
                        )
                    )
                    or "0.1.0-rc.7"
                ),
                provider=(
                    optional_text(
                        values.get(
                            "AGENT_INVESTIGATION_DSH_PROVIDER"
                        )
                    )
                    or "deepseek-official"
                ),
                model=(
                    optional_text(
                        values.get(
                            "AGENT_INVESTIGATION_DSH_MODEL"
                        )
                    )
                    or "deepseek-v4-flash"
                ),
                max_tokens=(
                    optional_text(
                        values.get(
                            "AGENT_INVESTIGATION_DSH_MAX_TOKENS"
                        )
                    )
                    or 4096
                ),
                request_timeout_seconds=(
                    optional_text(
                        values.get(
                            "AGENT_INVESTIGATION_DSH_REQUEST_TIMEOUT_SECONDS"
                        )
                    )
                    or 15.0
                ),
                turn_timeout_seconds=(
                    optional_text(
                        values.get(
                            "AGENT_INVESTIGATION_DSH_TURN_TIMEOUT_SECONDS"
                        )
                    )
                    or 60.0
                ),
                shutdown_timeout_seconds=(
                    optional_text(
                        values.get(
                            "AGENT_INVESTIGATION_DSH_SHUTDOWN_TIMEOUT_SECONDS"
                        )
                    )
                    or 3.0
                ),
            )
        except (TypeError, ValueError) as error:
            raise DshInvestigationReasonerRuntimeConfigurationError(
                "DSH Investigation Reasoner runtime configuration is invalid"
            ) from error


__all__ = [
    "INVESTIGATION_DSH_REASONER_ACKNOWLEDGEMENT",
    "DshInvestigationReasonerRuntimeConfigurationError",
    "DshInvestigationReasonerRuntimeSettings",
]
