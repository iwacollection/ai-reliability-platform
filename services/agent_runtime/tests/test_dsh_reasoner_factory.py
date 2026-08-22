from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.agent_runtime.app.investigation.dsh_investigation_reasoner import (
    DshInvestigationReasoner,
)
from services.agent_runtime.app.investigation.dsh_reasoner_factory import (
    DshInvestigationReasonerFactoryError,
    create_dsh_investigation_reasoner,
)
from services.agent_runtime.app.investigation.dsh_reasoner_runtime_settings import (
    INVESTIGATION_DSH_REASONER_ACKNOWLEDGEMENT,
    DshInvestigationReasonerRuntimeConfigurationError,
    DshInvestigationReasonerRuntimeSettings,
)


def runtime_artifacts(
    tmp_path: Path,
    *,
    version: str = "0.1.0-rc.7",
):
    node = tmp_path / "node.exe"
    node.write_text(
        "not executed in factory tests",
        encoding="utf-8",
    )

    package_root = (
        tmp_path
        / "node_modules"
        / "@deepseek-ai"
        / "dsh-sdk-jsonrpc-demo"
    )
    entrypoint = (
        package_root
        / "lib"
        / "packaged-bin.js"
    )
    entrypoint.parent.mkdir(
        parents=True,
    )
    entrypoint.write_text(
        "not executed in factory tests",
        encoding="utf-8",
    )
    (
        package_root
        / "package.json"
    ).write_text(
        json.dumps(
            {
                "name": (
                    "@deepseek-ai/"
                    "dsh-sdk-jsonrpc-demo"
                ),
                "version": version,
            }
        ),
        encoding="utf-8",
    )

    cordis = (
        tmp_path
        / "reasoner.cordis.yml"
    )
    cordis.write_text(
        "- id: test\n  name: test\n",
        encoding="utf-8",
    )

    session_root = (
        tmp_path
        / "sessions"
    )
    session_root.mkdir()

    return {
        "runtime_executable": str(node),
        "runtime_entrypoint": str(
            entrypoint
        ),
        "cordis_config_path": str(
            cordis
        ),
        "session_root": str(
            session_root
        ),
    }


def enabled_settings(
    tmp_path: Path,
    *,
    version: str = "0.1.0-rc.7",
):
    return DshInvestigationReasonerRuntimeSettings(
        enabled=True,
        acknowledgement=(
            INVESTIGATION_DSH_REASONER_ACKNOWLEDGEMENT
        ),
        expected_runtime_version=version,
        **runtime_artifacts(
            tmp_path,
            version=version,
        ),
    )


def test_disabled_settings_are_side_effect_free(
    tmp_path: Path,
):
    missing = tmp_path / "missing"
    settings = DshInvestigationReasonerRuntimeSettings(
        runtime_executable=str(
            missing
        ),
        runtime_entrypoint=str(
            missing
        ),
        cordis_config_path=str(
            missing
        ),
        session_root=str(
            missing
        ),
    )

    result = create_dsh_investigation_reasoner(
        settings=settings,
        cwd=missing,
    )

    assert result is None
    assert not missing.exists()


def test_enabled_settings_require_exact_acknowledgement():
    with pytest.raises(
        ValueError,
        match="exact acknowledgement",
    ):
        DshInvestigationReasonerRuntimeSettings(
            enabled=True,
            acknowledgement="wrong",
            runtime_executable="node",
            runtime_entrypoint="entry",
            cordis_config_path="cordis",
            session_root="sessions",
        )


def test_environment_settings_default_disabled():
    settings = (
        DshInvestigationReasonerRuntimeSettings
        .from_environment({})
    )

    assert settings.enabled is False
    assert (
        settings.expected_runtime_version
        == "0.1.0-rc.7"
    )
    assert settings.provider == "deepseek-official"
    assert settings.model == "deepseek-v4-flash"


def test_environment_settings_parse_explicit_enablement(
    tmp_path: Path,
):
    paths = runtime_artifacts(
        tmp_path
    )
    settings = (
        DshInvestigationReasonerRuntimeSettings
        .from_environment(
            {
                "AGENT_INVESTIGATION_DSH_REASONER_ENABLED": "true",
                "AGENT_INVESTIGATION_DSH_REASONER_ACKNOWLEDGEMENT": (
                    INVESTIGATION_DSH_REASONER_ACKNOWLEDGEMENT
                ),
                "AGENT_INVESTIGATION_DSH_RUNTIME_EXECUTABLE": (
                    paths["runtime_executable"]
                ),
                "AGENT_INVESTIGATION_DSH_RUNTIME_ENTRYPOINT": (
                    paths["runtime_entrypoint"]
                ),
                "AGENT_INVESTIGATION_DSH_CORDIS_CONFIG_PATH": (
                    paths["cordis_config_path"]
                ),
                "AGENT_INVESTIGATION_DSH_SESSION_ROOT": (
                    paths["session_root"]
                ),
                "AGENT_INVESTIGATION_DSH_MAX_TOKENS": "2048",
                "AGENT_INVESTIGATION_DSH_TURN_TIMEOUT_SECONDS": "45",
            }
        )
    )

    assert settings.enabled is True
    assert settings.max_tokens == 2048
    assert settings.turn_timeout_seconds == 45.0


def test_environment_settings_sanitize_invalid_configuration():
    with pytest.raises(
        DshInvestigationReasonerRuntimeConfigurationError,
        match="configuration is invalid",
    ):
        (
            DshInvestigationReasonerRuntimeSettings
            .from_environment(
                {
                    "AGENT_INVESTIGATION_DSH_REASONER_ENABLED": "true",
                    "AGENT_INVESTIGATION_DSH_REASONER_ACKNOWLEDGEMENT": "wrong",
                }
            )
        )


def test_factory_builds_reasoner_without_starting_runtime(
    tmp_path: Path,
):
    settings = enabled_settings(
        tmp_path
    )

    reasoner = create_dsh_investigation_reasoner(
        settings=settings,
        cwd=tmp_path,
    )

    assert isinstance(
        reasoner,
        DshInvestigationReasoner,
    )
    assert (
        reasoner.config.provider
        == "deepseek-official"
    )
    assert (
        reasoner.config.model
        == "deepseek-v4-flash"
    )

    # Merely building the reasoner does not instantiate or start the
    # subprocess Adapter. The closure is inert until decide().
    runtime = reasoner.runtime_factory()
    assert runtime.running is False
    assert runtime.config.launch_args == (
        settings.runtime_executable,
        settings.runtime_entrypoint,
    )
    assert (
        runtime.config.env[
            "DSH_CORDIS_CONFIG"
        ]
        == settings.cordis_config_path
    )


def test_factory_rejects_runtime_version_mismatch(
    tmp_path: Path,
):
    paths = runtime_artifacts(
        tmp_path,
        version="0.1.0-rc.8",
    )
    settings = DshInvestigationReasonerRuntimeSettings(
        enabled=True,
        acknowledgement=(
            INVESTIGATION_DSH_REASONER_ACKNOWLEDGEMENT
        ),
        expected_runtime_version="0.1.0-rc.7",
        **paths,
    )

    with pytest.raises(
        DshInvestigationReasonerFactoryError,
        match="version does not match",
    ):
        create_dsh_investigation_reasoner(
            settings=settings,
            cwd=tmp_path,
        )


def test_factory_rejects_missing_runtime_artifact(
    tmp_path: Path,
):
    paths = runtime_artifacts(
        tmp_path
    )
    Path(
        paths["runtime_entrypoint"]
    ).unlink()

    settings = DshInvestigationReasonerRuntimeSettings(
        enabled=True,
        acknowledgement=(
            INVESTIGATION_DSH_REASONER_ACKNOWLEDGEMENT
        ),
        **paths,
    )

    with pytest.raises(
        DshInvestigationReasonerFactoryError,
        match="runtime entrypoint is unavailable",
    ):
        create_dsh_investigation_reasoner(
            settings=settings,
            cwd=tmp_path,
        )
