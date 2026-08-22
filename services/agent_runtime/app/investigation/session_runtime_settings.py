from __future__ import annotations

import os

from collections.abc import Mapping
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.agent_runtime.app.investigation.settings import (
    optional_text,
    parse_bool,
)


INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT = (
    "I_ENABLE_DURABLE_INVESTIGATION_SESSION_RUNTIME_V1"
)

INVESTIGATION_LANGGRAPH_ENGINE_ACKNOWLEDGEMENT = (
    "I_ENABLE_LANGGRAPH_INVESTIGATION_ENGINE_V1"
)


class InvestigationEngineBackend(str, Enum):
    """Supported Investigation orchestration backends."""

    CUSTOM = "custom"
    LANGGRAPH = "langgraph"


class InvestigationSessionRuntimeConfigurationError(ValueError):
    """Durable Investigation Session Runtime configuration is invalid."""


class InvestigationSessionRuntimeSettings(BaseModel):
    """
    Disabled-default settings for durable Investigation Session components.

    Importing this module never reads the environment or touches the database.
    The database path is only consumed by the Factory after explicit enablement
    and exact acknowledgement.
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
    db_path: str = Field(
        default="data/investigation_sessions.db",
        min_length=1,
        max_length=512,
    )
    engine_backend: InvestigationEngineBackend = (
        InvestigationEngineBackend.CUSTOM
    )
    langgraph_acknowledgement: str | None = Field(
        default=None,
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_enablement(self):
        if (
            self.enabled
            and self.acknowledgement
            != INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT
        ):
            raise ValueError(
                "enabled Session Runtime requires exact acknowledgement"
            )
        if (
            self.enabled
            and self.engine_backend
            == InvestigationEngineBackend.LANGGRAPH
            and self.langgraph_acknowledgement
            != INVESTIGATION_LANGGRAPH_ENGINE_ACKNOWLEDGEMENT
        ):
            raise ValueError(
                "enabled LangGraph Engine requires exact acknowledgement"
            )
        if (
            "\x00" in self.db_path
            or Path(self.db_path).suffix.lower() != ".db"
        ):
            raise ValueError(
                "Investigation Session database path is invalid"
            )
        return self

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "InvestigationSessionRuntimeSettings":
        values = environment if environment is not None else os.environ
        try:
            enabled = parse_bool(
                values.get(
                    "AGENT_INVESTIGATION_SESSION_RUNTIME_ENABLED"
                ),
                default=False,
                name="AGENT_INVESTIGATION_SESSION_RUNTIME_ENABLED",
            )
            acknowledgement = optional_text(
                values.get(
                    "AGENT_INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT"
                )
            )
            db_path = optional_text(
                values.get(
                    "AGENT_INVESTIGATION_SESSION_DB_PATH"
                )
            ) or "data/investigation_sessions.db"
            engine_backend = optional_text(
                values.get(
                    "AGENT_INVESTIGATION_SESSION_ENGINE"
                )
            ) or InvestigationEngineBackend.CUSTOM.value
            langgraph_acknowledgement = optional_text(
                values.get(
                    "AGENT_INVESTIGATION_LANGGRAPH_ACKNOWLEDGEMENT"
                )
            )
            return cls(
                enabled=enabled,
                acknowledgement=acknowledgement,
                db_path=db_path,
                engine_backend=engine_backend,
                langgraph_acknowledgement=(
                    langgraph_acknowledgement
                ),
            )
        except (TypeError, ValueError) as error:
            raise InvestigationSessionRuntimeConfigurationError(
                "Investigation Session Runtime configuration is invalid"
            ) from error


__all__ = [
    "INVESTIGATION_LANGGRAPH_ENGINE_ACKNOWLEDGEMENT",
    "INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT",
    "InvestigationEngineBackend",
    "InvestigationSessionRuntimeConfigurationError",
    "InvestigationSessionRuntimeSettings",
]
