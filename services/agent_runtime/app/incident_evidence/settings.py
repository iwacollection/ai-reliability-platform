from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


INCIDENT_EVIDENCE_ENABLE_ACKNOWLEDGEMENT = (
    "I_ACKNOWLEDGE_READ_ONLY_PRODUCTION_INCIDENT_EVIDENCE_CAPTURE"
)

DEFAULT_INCIDENT_EVIDENCE_OUTPUT_DIR = (
    "evaluation_data/production_incident_captures"
)


class IncidentEvidenceRecorderConfigurationError(RuntimeError):
    pass


class IncidentEvidenceRecorderSettings(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    enabled: bool = False
    acknowledgement: str | None = None
    output_dir: str = Field(
        default=DEFAULT_INCIDENT_EVIDENCE_OUTPUT_DIR,
        min_length=1,
        max_length=512,
    )

    @model_validator(mode="after")
    def validate_configuration(self):
        if (
            self.enabled
            and self.acknowledgement
            != INCIDENT_EVIDENCE_ENABLE_ACKNOWLEDGEMENT
        ):
            raise ValueError(
                "enabled recorder requires exact acknowledgement"
            )

        path = Path(self.output_dir)

        if path.is_absolute() or ".." in path.parts:
            raise ValueError(
                "recorder output directory must be repository-relative"
            )

        return self

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "IncidentEvidenceRecorderSettings":
        source = (
            environment
            if environment is not None
            else os.environ
        )

        try:
            return cls(
                enabled=_parse_bool(
                    source.get(
                        "AGENT_INCIDENT_EVIDENCE_RECORDER_ENABLED"
                    ),
                    default=False,
                ),
                acknowledgement=_optional_text(
                    source.get(
                        "AGENT_INCIDENT_EVIDENCE_RECORDER_ACKNOWLEDGEMENT"
                    )
                ),
                output_dir=(
                    _optional_text(
                        source.get(
                            "AGENT_INCIDENT_EVIDENCE_RECORDER_OUTPUT_DIR"
                        )
                    )
                    or DEFAULT_INCIDENT_EVIDENCE_OUTPUT_DIR
                ),
            )
        except Exception:
            raise IncidentEvidenceRecorderConfigurationError(
                "Incident Evidence Recorder configuration is invalid"
            ) from None

    def resolve_output_dir(self) -> Path:
        repository_root = (
            Path(__file__).resolve().parents[4]
        )

        resolved = (
            repository_root
            / self.output_dir
        ).resolve()

        try:
            resolved.relative_to(
                repository_root
            )
        except ValueError:
            raise IncidentEvidenceRecorderConfigurationError(
                "Incident Evidence Recorder output directory is invalid"
            ) from None

        return resolved


def _parse_bool(
    value: Any,
    *,
    default: bool,
) -> bool:
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if not isinstance(value, str):
        raise ValueError(
            "boolean environment value is invalid"
        )

    normalized = value.strip().lower()

    if normalized == "true":
        return True

    if normalized == "false":
        return False

    raise ValueError(
        "boolean environment value is invalid"
    )


def _optional_text(
    value: Any,
) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValueError(
            "environment text value is invalid"
        )

    normalized = value.strip()

    return normalized if normalized else None


__all__ = [
    "DEFAULT_INCIDENT_EVIDENCE_OUTPUT_DIR",
    "INCIDENT_EVIDENCE_ENABLE_ACKNOWLEDGEMENT",
    "IncidentEvidenceRecorderConfigurationError",
    "IncidentEvidenceRecorderSettings",
]
