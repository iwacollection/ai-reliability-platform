import os
from collections.abc import Mapping
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from services.agent_runtime.app.investigation.models import (
    InvestigationLimits,
)


INVESTIGATION_ENABLE_ACKNOWLEDGEMENT = (
    "I_ENABLE_READ_ONLY_INVESTIGATION_SHADOW_V1"
)


class InvestigationConfigurationError(ValueError):
    """
    Investigation Shadow configuration is invalid.
    """


class InvestigationSettings(BaseModel):
    """
    Fail-closed settings for read-only Investigation Shadow wiring.

    The default configuration is disabled. No module import reads the
    environment. from_environment is called explicitly by the Runtime factory.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    enabled: bool = False
    shadow_mode: Literal[True] = True
    acknowledgement: str | None = Field(
        default=None,
        max_length=128,
    )
    limits: InvestigationLimits = Field(
        default_factory=InvestigationLimits
    )

    @model_validator(mode="after")
    def validate_enablement(self):
        if (
            self.enabled
            and self.acknowledgement
            != INVESTIGATION_ENABLE_ACKNOWLEDGEMENT
        ):
            raise ValueError(
                "enabled Investigation Shadow requires exact acknowledgement"
            )

        return self

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "InvestigationSettings":
        values = environment if environment is not None else os.environ

        try:
            enabled = parse_bool(
                values.get(
                    "AGENT_INVESTIGATION_SHADOW_ENABLED"
                ),
                default=False,
                name=(
                    "AGENT_INVESTIGATION_SHADOW_ENABLED"
                ),
            )
            acknowledgement = optional_text(
                values.get(
                    "AGENT_INVESTIGATION_SHADOW_ACKNOWLEDGEMENT"
                )
            )
            max_iterations = parse_int(
                values.get(
                    "AGENT_INVESTIGATION_MAX_ITERATIONS"
                ),
                default=6,
                name=(
                    "AGENT_INVESTIGATION_MAX_ITERATIONS"
                ),
            )
            max_tool_calls = parse_int(
                values.get(
                    "AGENT_INVESTIGATION_MAX_TOOL_CALLS"
                ),
                default=10,
                name=(
                    "AGENT_INVESTIGATION_MAX_TOOL_CALLS"
                ),
            )
            timeout_seconds = parse_float(
                values.get(
                    "AGENT_INVESTIGATION_TIMEOUT_SECONDS"
                ),
                default=30.0,
                name=(
                    "AGENT_INVESTIGATION_TIMEOUT_SECONDS"
                ),
            )

            return cls(
                enabled=enabled,
                acknowledgement=acknowledgement,
                limits=InvestigationLimits(
                    max_iterations=max_iterations,
                    max_tool_calls=max_tool_calls,
                    timeout_seconds=timeout_seconds,
                ),
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise InvestigationConfigurationError(
                "Investigation Shadow configuration is invalid"
            ) from exc


def parse_bool(
    value: str | None,
    *,
    default: bool,
    name: str,
) -> bool:
    if value is None:
        return default

    normalized = value.strip().lower()

    if normalized in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True

    if normalized in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False

    raise ValueError(
        f"{name} must be a boolean"
    )


def parse_int(
    value: str | None,
    *,
    default: int,
    name: str,
) -> int:
    if value is None:
        return default

    try:
        return int(value.strip())
    except (AttributeError, ValueError) as exc:
        raise ValueError(
            f"{name} must be an integer"
        ) from exc


def parse_float(
    value: str | None,
    *,
    default: float,
    name: str,
) -> float:
    if value is None:
        return default

    try:
        return float(value.strip())
    except (AttributeError, ValueError) as exc:
        raise ValueError(
            f"{name} must be a number"
        ) from exc


def optional_text(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None

