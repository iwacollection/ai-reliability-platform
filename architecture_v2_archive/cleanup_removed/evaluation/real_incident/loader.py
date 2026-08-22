import json
from pathlib import Path

from pydantic import ValidationError

from services.agent_runtime.app.evaluation.real_incident.models import (
    RealIncidentDataset,
)


class RealIncidentDatasetLoadError(
    RuntimeError
):
    """
    Real Incident Dataset could not be safely loaded.
    """


class RealIncidentDatasetLoader:
    """
    Load validated historical Incident datasets from JSON files.

    v1 deliberately supports JSON only:
    - deterministic parser;
    - no YAML object tags;
    - simple auditability;
    - stable export format.

    The loader never modifies Runtime state and never executes an Incident.
    """

    _MAX_FILE_BYTES = (
        10 * 1024 * 1024
    )

    def load(
        self,
        path: str | Path,
    ) -> RealIncidentDataset:
        resolved = Path(
            path
        )

        if resolved.is_symlink():
            raise RealIncidentDatasetLoadError(
                "Real Incident Dataset symlinks are not allowed"
            )

        if (
            not resolved.exists()
            or not resolved.is_file()
        ):
            raise RealIncidentDatasetLoadError(
                "Real Incident Dataset file was not found"
            )

        if (
            resolved.suffix.lower()
            != ".json"
        ):
            raise RealIncidentDatasetLoadError(
                "Real Incident Dataset must be JSON"
            )

        try:
            size = resolved.stat().st_size
        except OSError as exc:
            raise RealIncidentDatasetLoadError(
                "Real Incident Dataset file metadata could not be read"
            ) from exc

        if size <= 0:
            raise RealIncidentDatasetLoadError(
                "Real Incident Dataset file is empty"
            )

        if size > self._MAX_FILE_BYTES:
            raise RealIncidentDatasetLoadError(
                "Real Incident Dataset file exceeds the size limit"
            )

        try:
            raw = resolved.read_text(
                encoding="utf-8"
            )

            payload = json.loads(
                raw
            )

        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise RealIncidentDatasetLoadError(
                "Real Incident Dataset file is invalid"
            ) from exc

        if not isinstance(
            payload,
            dict,
        ):
            raise RealIncidentDatasetLoadError(
                "Real Incident Dataset root must be an object"
            )

        try:
            return RealIncidentDataset.model_validate(
                payload
            )

        except ValidationError as exc:
            raise RealIncidentDatasetLoadError(
                "Real Incident Dataset schema validation failed"
            ) from exc

    def load_directory(
        self,
        directory: str | Path,
    ) -> list[
        RealIncidentDataset
    ]:
        root = Path(
            directory
        )

        if (
            not root.exists()
            or not root.is_dir()
        ):
            raise RealIncidentDatasetLoadError(
                "Real Incident Dataset directory was not found"
            )

        datasets = [
            self.load(
                path
            )
            for path
            in sorted(
                root.glob(
                    "*.json"
                )
            )
        ]

        incident_ids = [
            item.incident_id
            for item
            in datasets
        ]

        if len(
            incident_ids
        ) != len(
            set(
                incident_ids
            )
        ):
            raise RealIncidentDatasetLoadError(
                "Real Incident Dataset directory contains duplicate incident_id values"
            )

        return datasets


__all__ = [
    "RealIncidentDatasetLoader",
    "RealIncidentDatasetLoadError",
]
