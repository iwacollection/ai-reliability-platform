from __future__ import annotations

import json
from pathlib import Path

from services.agent_runtime.app.investigation.dsh_investigation_reasoner import (
    DshInvestigationReasoner,
    DshInvestigationReasonerConfig,
)
from services.agent_runtime.app.investigation.dsh_reasoner_runtime_settings import (
    DshInvestigationReasonerRuntimeSettings,
)
from services.agent_runtime.app.investigation.dsh_runtime_adapter import (
    DshRuntimeAdapter,
    DshRuntimeConfig,
)


class DshInvestigationReasonerFactoryError(RuntimeError):
    """Enabled DSH Investigation Reasoner cannot be assembled safely."""


def _resolve_file(
    value: str | None,
    *,
    label: str,
) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DshInvestigationReasonerFactoryError(
            f"DSH Investigation Reasoner {label} is missing"
        )

    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise DshInvestigationReasonerFactoryError(
            f"DSH Investigation Reasoner {label} is unavailable"
        )
    return path


def _resolve_directory(
    value: str | None,
    *,
    label: str,
) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DshInvestigationReasonerFactoryError(
            f"DSH Investigation Reasoner {label} is missing"
        )

    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise DshInvestigationReasonerFactoryError(
            f"DSH Investigation Reasoner {label} is unavailable"
        )
    return path


def _read_runtime_version(
    runtime_entrypoint: Path,
) -> str:
    package_json = (
        runtime_entrypoint.parent.parent
        / "package.json"
    )
    if not package_json.is_file():
        raise DshInvestigationReasonerFactoryError(
            "DSH Investigation Reasoner runtime package metadata is unavailable"
        )

    try:
        payload = json.loads(
            package_json.read_text(
                encoding="utf-8",
                errors="strict",
            )
        )
    except Exception:
        raise DshInvestigationReasonerFactoryError(
            "DSH Investigation Reasoner runtime package metadata is invalid"
        ) from None

    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise DshInvestigationReasonerFactoryError(
            "DSH Investigation Reasoner runtime version is invalid"
        )
    return version.strip()


def create_dsh_investigation_reasoner(
    *,
    settings: (
        DshInvestigationReasonerRuntimeSettings
        | None
    ) = None,
    cwd: str | Path,
) -> DshInvestigationReasoner | None:
    """
    Build one DSH-backed Investigation reasoner after explicit enablement.

    Disabled mode returns before touching the filesystem or constructing any
    subprocess adapter. Enabled mode validates the exact runtime artifacts and
    version, but still does not start Node or call any model. A fresh adapter
    is created later for each `decide()` call by DshInvestigationReasoner.
    """

    resolved_settings = (
        settings
        if settings is not None
        else DshInvestigationReasonerRuntimeSettings.from_environment()
    )
    if not isinstance(
        resolved_settings,
        DshInvestigationReasonerRuntimeSettings,
    ):
        raise TypeError(
            "DSH Investigation Reasoner runtime settings are invalid"
        )
    if not resolved_settings.enabled:
        return None

    runtime_executable = _resolve_file(
        resolved_settings.runtime_executable,
        label="runtime executable",
    )
    runtime_entrypoint = _resolve_file(
        resolved_settings.runtime_entrypoint,
        label="runtime entrypoint",
    )
    cordis_config = _resolve_file(
        resolved_settings.cordis_config_path,
        label="Cordis config",
    )
    session_root = _resolve_directory(
        resolved_settings.session_root,
        label="session root",
    )

    working_directory = Path(cwd).expanduser().resolve()
    if not working_directory.is_dir():
        raise DshInvestigationReasonerFactoryError(
            "DSH Investigation Reasoner working directory is unavailable"
        )

    actual_version = _read_runtime_version(
        runtime_entrypoint
    )
    if (
        actual_version
        != resolved_settings.expected_runtime_version
    ):
        raise DshInvestigationReasonerFactoryError(
            "DSH Investigation Reasoner runtime version does not match the configured pin"
        )

    runtime_config = DshRuntimeConfig(
        launch_args=(
            str(runtime_executable),
            str(runtime_entrypoint),
        ),
        cwd=str(working_directory),
        env={
            "DSH_CORDIS_CONFIG": str(
                cordis_config
            ),
            "DSH_CWD": str(
                working_directory
            ),
            "DSH_SESSION_ROOT": str(
                session_root
            ),
        },
        request_timeout_seconds=(
            resolved_settings.request_timeout_seconds
        ),
        turn_timeout_seconds=(
            resolved_settings.turn_timeout_seconds
        ),
        shutdown_timeout_seconds=(
            resolved_settings.shutdown_timeout_seconds
        ),
    )

    def runtime_factory() -> DshRuntimeAdapter:
        return DshRuntimeAdapter(
            runtime_config
        )

    return DshInvestigationReasoner(
        runtime_factory=runtime_factory,
        config=DshInvestigationReasonerConfig(
            cwd=str(working_directory),
            provider=resolved_settings.provider,
            model=resolved_settings.model,
            max_tokens=resolved_settings.max_tokens,
        ),
    )


__all__ = [
    "DshInvestigationReasonerFactoryError",
    "create_dsh_investigation_reasoner",
]
