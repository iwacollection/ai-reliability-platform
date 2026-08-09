from collections.abc import Callable, Mapping
from datetime import datetime
from os import environ
from typing import Any

from common.config import get_settings
from common.config.settings import (
    KubernetesPreflightConfig,
    KubernetesProductionExecutionConfig,
)
from services.agent_runtime.app.action.production_pilot import (
    KubernetesProductionPilotConfig,
    KubernetesProductionPilotControl,
)


class KubernetesProductionPilotFactoryError(
    RuntimeError
):
    """The non-secret production pilot manifest is invalid."""


_ENVIRONMENT_NAMES = {
    "enabled": "KUBERNETES_PRODUCTION_PILOT_ENABLED",
    "pilot_id": "KUBERNETES_PRODUCTION_PILOT_ID",
    "change_ticket": "KUBERNETES_PRODUCTION_CHANGE_TICKET",
    "runbook_version": "KUBERNETES_PRODUCTION_RUNBOOK_VERSION",
    "runbook_acknowledgement": (
        "KUBERNETES_PRODUCTION_RUNBOOK_ACKNOWLEDGEMENT"
    ),
    "kill_switch_file": (
        "KUBERNETES_PRODUCTION_KILL_SWITCH_FILE"
    ),
    "authorized_operator_ids": (
        "KUBERNETES_PRODUCTION_AUTHORIZED_OPERATORS"
    ),
    "starts_at": "KUBERNETES_PRODUCTION_PILOT_STARTS_AT",
    "expires_at": "KUBERNETES_PRODUCTION_PILOT_EXPIRES_AT",
}


def _parse_enabled(
    value: str | None,
) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise KubernetesProductionPilotFactoryError(
        "Kubernetes production pilot enabled flag is invalid"
    )


def _optional_exact_text(
    value: Any,
) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
    ):
        raise KubernetesProductionPilotFactoryError(
            "Kubernetes production pilot environment value is invalid"
        )
    return value


def _operator_ids(
    value: str | None,
) -> tuple[str, ...]:
    if value is None:
        return ()
    exact = _optional_exact_text(
        value
    )
    assert exact is not None
    operators = tuple(
        item.strip()
        for item in exact.split(",")
    )
    if (
        not operators
        or any(not item for item in operators)
    ):
        raise KubernetesProductionPilotFactoryError(
            "Kubernetes production operator list is invalid"
        )
    return operators


def create_kubernetes_production_pilot_control(
    preflight_config: KubernetesPreflightConfig | None = None,
    execution_config: KubernetesProductionExecutionConfig | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    clock: Callable[[], datetime] | None = None,
    switch_reader: Callable[[str], str] | None = None,
) -> KubernetesProductionPilotControl:
    """Create one shared, fail-closed dynamic pilot control."""

    if preflight_config is None or execution_config is None:
        settings = get_settings()
        if preflight_config is None:
            preflight_config = (
                settings.remediation.kubernetes_preflight
            )
        if execution_config is None:
            execution_config = (
                settings.remediation
                .kubernetes_production_execution
            )

    if not isinstance(
        preflight_config,
        KubernetesPreflightConfig,
    ):
        raise KubernetesProductionPilotFactoryError(
            "Kubernetes production pilot preflight configuration is invalid"
        )
    if not isinstance(
        execution_config,
        KubernetesProductionExecutionConfig,
    ):
        raise KubernetesProductionPilotFactoryError(
            "Kubernetes production pilot execution configuration is invalid"
        )

    source = environ if environment is None else environment
    if not isinstance(source, Mapping):
        raise KubernetesProductionPilotFactoryError(
            "Kubernetes production pilot environment is invalid"
        )

    try:
        values = {
            key: source.get(name)
            for key, name
            in _ENVIRONMENT_NAMES.items()
        }
        config = KubernetesProductionPilotConfig(
            enabled=_parse_enabled(
                values["enabled"]
            ),
            pilot_id=_optional_exact_text(
                values["pilot_id"]
            ),
            change_ticket=_optional_exact_text(
                values["change_ticket"]
            ),
            runbook_version=_optional_exact_text(
                values["runbook_version"]
            ),
            runbook_acknowledgement=(
                _optional_exact_text(
                    values[
                        "runbook_acknowledgement"
                    ]
                )
            ),
            kill_switch_file=(
                _optional_exact_text(
                    values["kill_switch_file"]
                )
            ),
            authorized_operator_ids=(
                _operator_ids(
                    values[
                        "authorized_operator_ids"
                    ]
                )
            ),
            starts_at=values[
                "starts_at"
            ],
            expires_at=values[
                "expires_at"
            ],
        )
        return KubernetesProductionPilotControl(
            config=config,
            preflight_config=preflight_config,
            execution_config=execution_config,
            clock=clock,
            switch_reader=switch_reader,
        )
    except KubernetesProductionPilotFactoryError:
        raise
    except Exception:
        raise KubernetesProductionPilotFactoryError(
            "Kubernetes production pilot configuration is invalid"
        ) from None


__all__ = [
    "KubernetesProductionPilotFactoryError",
    "create_kubernetes_production_pilot_control",
]
