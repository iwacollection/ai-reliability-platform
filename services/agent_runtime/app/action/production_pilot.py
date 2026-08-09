from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from re import fullmatch
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from common.config.settings import (
    KUBERNETES_PRODUCTION_WRITE_ACKNOWLEDGEMENT,
    KubernetesPreflightConfig,
    KubernetesProductionExecutionConfig,
)


KUBERNETES_PRODUCTION_RUNBOOK_ACKNOWLEDGEMENT = (
    "I_HAVE_READ_AND_ACCEPT_OOM_PILOT_RUNBOOK_V1"
)
KUBERNETES_PRODUCTION_KILL_SWITCH_ENGAGED = "ENGAGED"
KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED = (
    "DISENGAGED_FOR_OOM_PILOT_V1"
)

_IDENTIFIER_PATTERN = r"[A-Za-z0-9](?:[A-Za-z0-9_.:/-]{0,126}[A-Za-z0-9])?"
_MAX_KILL_SWITCH_BYTES = 256
_MAX_PILOT_WINDOW = timedelta(hours=4)


class KubernetesProductionPilotError(RuntimeError):
    """Base fail-closed error for production pilot controls."""


class KubernetesProductionPilotBlockedError(
    KubernetesProductionPilotError
):
    """The production pilot is not currently authorized to execute."""


class KubernetesProductionPilotConfig(BaseModel):
    """Non-secret operator manifest for the first OOMKilled pilot."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    enabled: bool = False
    pilot_id: str | None = None
    change_ticket: str | None = None
    runbook_version: str | None = None
    runbook_acknowledgement: str | None = None
    kill_switch_file: str | None = None
    authorized_operator_ids: tuple[str, ...] = Field(
        default_factory=tuple,
    )
    starts_at: datetime | None = None
    expires_at: datetime | None = None

    @field_validator(
        "pilot_id",
        "change_ticket",
        "runbook_version",
        mode="before",
    )
    @classmethod
    def validate_identifier(
        cls,
        value: Any,
    ) -> str | None:
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or value != value.strip()
            or fullmatch(
                _IDENTIFIER_PATTERN,
                value,
            )
            is None
        ):
            raise ValueError(
                "Kubernetes production pilot identifier is invalid"
            )
        return value

    @field_validator(
        "runbook_acknowledgement",
        mode="before",
    )
    @classmethod
    def validate_runbook_acknowledgement(
        cls,
        value: Any,
    ) -> str | None:
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or value != value.strip()
            or not value
            or len(value) > 128
        ):
            raise ValueError(
                "Kubernetes production runbook acknowledgement is invalid"
            )
        return value

    @field_validator(
        "kill_switch_file",
        mode="before",
    )
    @classmethod
    def validate_kill_switch_file(
        cls,
        value: Any,
    ) -> str | None:
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or value != value.strip()
            or not value
            or "\x00" in value
            or len(value) > 4096
        ):
            raise ValueError(
                "Kubernetes production kill-switch file is invalid"
            )
        return value

    @field_validator(
        "authorized_operator_ids",
        mode="before",
    )
    @classmethod
    def validate_authorized_operators(
        cls,
        value: Any,
    ) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            raise ValueError(
                "Kubernetes production operators must be a collection"
            )
        try:
            operators = tuple(value)
        except TypeError:
            raise ValueError(
                "Kubernetes production operators must be a collection"
            ) from None

        if any(
            not isinstance(item, str)
            or item != item.strip()
            or fullmatch(
                _IDENTIFIER_PATTERN,
                item,
            )
            is None
            for item in operators
        ):
            raise ValueError(
                "Kubernetes production operator identity is invalid"
            )
        if len(operators) != len(set(operators)):
            raise ValueError(
                "Kubernetes production operators must be unique"
            )
        return operators

    @field_validator(
        "starts_at",
        "expires_at",
        mode="after",
    )
    @classmethod
    def validate_time(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None
        if (
            value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(
                "Kubernetes production pilot time must be timezone-aware"
            )
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_enabled_manifest(
        self,
    ) -> "KubernetesProductionPilotConfig":
        if not self.enabled:
            return self

        if any(
            value is None
            for value in (
                self.pilot_id,
                self.change_ticket,
                self.runbook_version,
                self.kill_switch_file,
                self.starts_at,
                self.expires_at,
            )
        ):
            raise ValueError(
                "Enabled Kubernetes production pilot requires a complete manifest"
            )
        if (
            self.runbook_acknowledgement
            != KUBERNETES_PRODUCTION_RUNBOOK_ACKNOWLEDGEMENT
        ):
            raise ValueError(
                "Enabled Kubernetes production pilot requires the exact runbook acknowledgement"
            )
        if not self.authorized_operator_ids:
            raise ValueError(
                "Enabled Kubernetes production pilot requires authorized operators"
            )
        assert self.starts_at is not None
        assert self.expires_at is not None
        if self.expires_at <= self.starts_at:
            raise ValueError(
                "Kubernetes production pilot window is invalid"
            )
        if self.expires_at - self.starts_at > _MAX_PILOT_WINDOW:
            raise ValueError(
                "Kubernetes production pilot window exceeds four hours"
            )
        return self


class KubernetesProductionKillSwitchSnapshot(BaseModel):
    """Bounded dynamic kill-switch state without path or file content."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    configured: bool
    readable: bool
    valid: bool
    engaged: bool
    state: Literal[
        "unconfigured",
        "unavailable",
        "invalid",
        "engaged",
        "disengaged",
    ]


class KubernetesProductionPilotReadinessSnapshot(BaseModel):
    """Read-only go/no-go view safe for authenticated API responses."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    ready_for_enablement: bool
    ready_for_execution: bool
    pilot_enabled: bool
    preflight_enabled: bool
    production_execution_enabled: bool
    production_executor_configured: bool
    exact_target_count: int
    exact_single_target: bool
    credential_references_separate: bool
    write_acknowledged: bool
    pilot_id: str | None
    change_ticket: str | None
    runbook_version: str | None
    runbook_acknowledged: bool
    authorized_operator_count: int
    window_state: Literal[
        "not_configured",
        "not_started",
        "active",
        "expired",
        "clock_invalid",
    ]
    checked_at: datetime
    kill_switch: KubernetesProductionKillSwitchSnapshot
    enablement_blockers: tuple[str, ...]
    execution_blockers: tuple[str, ...]


class KubernetesProductionPilotControl:
    """
    Dynamic final authorization gate for the OOMKilled production pilot.

    The kill-switch file is read for every decision. Missing, unreadable,
    oversized, or unexpected content always means engaged.
    """

    def __init__(
        self,
        *,
        config: KubernetesProductionPilotConfig,
        preflight_config: KubernetesPreflightConfig,
        execution_config: KubernetesProductionExecutionConfig,
        clock: Callable[[], datetime] | None = None,
        switch_reader: Callable[[str], str] | None = None,
    ) -> None:
        if not isinstance(config, KubernetesProductionPilotConfig):
            raise TypeError(
                "Kubernetes production pilot configuration is invalid"
            )
        if not isinstance(preflight_config, KubernetesPreflightConfig):
            raise TypeError(
                "Kubernetes production preflight configuration is invalid"
            )
        if not isinstance(
            execution_config,
            KubernetesProductionExecutionConfig,
        ):
            raise TypeError(
                "Kubernetes production execution configuration is invalid"
            )
        self.config = config
        self.preflight_config = preflight_config
        self.execution_config = execution_config
        self._clock = clock or (
            lambda: datetime.now(UTC)
        )
        self._switch_reader = (
            switch_reader
            or self._default_switch_reader
        )

    def snapshot(
        self,
        *,
        production_executor_configured: bool,
    ) -> KubernetesProductionPilotReadinessSnapshot:
        checked_at, window_state = (
            self._window_state()
        )
        kill_switch = self._kill_switch_snapshot()
        target_count = len(
            self.preflight_config.allowed_targets
        )
        exact_single_target = target_count == 1
        execution_reference = self._credential_reference(
            self.execution_config.bearer_token_env,
            self.execution_config.bearer_token_file,
        )
        preflight_reference = self._credential_reference(
            self.preflight_config.bearer_token_env,
            self.preflight_config.bearer_token_file,
        )
        credential_references_separate = (
            execution_reference is not None
            and preflight_reference is not None
            and execution_reference != preflight_reference
        )
        write_acknowledged = (
            self.execution_config.write_acknowledgement
            == KUBERNETES_PRODUCTION_WRITE_ACKNOWLEDGEMENT
        )
        runbook_acknowledged = (
            self.config.runbook_acknowledgement
            == KUBERNETES_PRODUCTION_RUNBOOK_ACKNOWLEDGEMENT
        )

        enablement_blockers: list[str] = []
        if not self.config.enabled:
            enablement_blockers.append(
                "pilot_disabled"
            )
        if not self.preflight_config.enabled:
            enablement_blockers.append(
                "preflight_disabled"
            )
        if not exact_single_target:
            enablement_blockers.append(
                "exact_single_target_required"
            )
        if not credential_references_separate:
            enablement_blockers.append(
                "credential_references_not_separate"
            )
        if not write_acknowledged:
            enablement_blockers.append(
                "write_acknowledgement_missing"
            )
        if not runbook_acknowledged:
            enablement_blockers.append(
                "runbook_acknowledgement_missing"
            )
        if not self.config.authorized_operator_ids:
            enablement_blockers.append(
                "authorized_operator_missing"
            )
        if window_state != "active":
            enablement_blockers.append(
                f"pilot_window_{window_state}"
            )
        if (
            not kill_switch.configured
            or not kill_switch.readable
            or not kill_switch.valid
        ):
            enablement_blockers.append(
                "kill_switch_unavailable"
            )

        execution_blockers = list(
            enablement_blockers
        )
        if not self.execution_config.enabled:
            execution_blockers.append(
                "production_execution_disabled"
            )
        if not production_executor_configured:
            execution_blockers.append(
                "production_executor_unavailable"
            )
        if kill_switch.engaged:
            execution_blockers.append(
                "kill_switch_engaged"
            )

        return KubernetesProductionPilotReadinessSnapshot(
            ready_for_enablement=(
                not enablement_blockers
            ),
            ready_for_execution=(
                not execution_blockers
            ),
            pilot_enabled=self.config.enabled,
            preflight_enabled=(
                self.preflight_config.enabled
            ),
            production_execution_enabled=(
                self.execution_config.enabled
            ),
            production_executor_configured=(
                production_executor_configured
            ),
            exact_target_count=target_count,
            exact_single_target=(
                exact_single_target
            ),
            credential_references_separate=(
                credential_references_separate
            ),
            write_acknowledged=(
                write_acknowledged
            ),
            pilot_id=self.config.pilot_id,
            change_ticket=(
                self.config.change_ticket
            ),
            runbook_version=(
                self.config.runbook_version
            ),
            runbook_acknowledged=(
                runbook_acknowledged
            ),
            authorized_operator_count=len(
                self.config.authorized_operator_ids
            ),
            window_state=window_state,
            checked_at=checked_at,
            kill_switch=kill_switch,
            enablement_blockers=tuple(
                enablement_blockers
            ),
            execution_blockers=tuple(
                execution_blockers
            ),
        )

    def require_execution(
        self,
        *,
        operator_id: str,
        production_executor_configured: bool,
    ) -> KubernetesProductionPilotReadinessSnapshot:
        snapshot = self.snapshot(
            production_executor_configured=(
                production_executor_configured
            )
        )
        if not snapshot.ready_for_execution:
            raise KubernetesProductionPilotBlockedError(
                "Kubernetes production pilot is not ready for execution"
            )
        if (
            not isinstance(operator_id, str)
            or operator_id
            not in self.config.authorized_operator_ids
        ):
            raise KubernetesProductionPilotBlockedError(
                "Authenticated operator is not authorized for this production pilot"
            )
        return snapshot

    def require_enablement(
        self,
    ) -> KubernetesProductionPilotReadinessSnapshot:
        snapshot = self.snapshot(
            production_executor_configured=False
        )
        if not snapshot.ready_for_enablement:
            raise KubernetesProductionPilotBlockedError(
                "Kubernetes production pilot manifest is not ready"
            )
        return snapshot

    def _window_state(
        self,
    ) -> tuple[datetime, str]:
        try:
            checked_at = self._clock()
            if (
                not isinstance(checked_at, datetime)
                or checked_at.tzinfo is None
                or checked_at.utcoffset() is None
            ):
                raise ValueError
            checked_at = checked_at.astimezone(
                UTC
            )
        except Exception:
            return (
                datetime.now(UTC),
                "clock_invalid",
            )

        if (
            self.config.starts_at is None
            or self.config.expires_at is None
        ):
            return checked_at, "not_configured"
        if checked_at < self.config.starts_at:
            return checked_at, "not_started"
        if checked_at >= self.config.expires_at:
            return checked_at, "expired"
        return checked_at, "active"

    def _kill_switch_snapshot(
        self,
    ) -> KubernetesProductionKillSwitchSnapshot:
        file_name = self.config.kill_switch_file
        if file_name is None:
            return KubernetesProductionKillSwitchSnapshot(
                configured=False,
                readable=False,
                valid=False,
                engaged=True,
                state="unconfigured",
            )
        try:
            value = self._switch_reader(
                file_name
            )
        except Exception:
            return KubernetesProductionKillSwitchSnapshot(
                configured=True,
                readable=False,
                valid=False,
                engaged=True,
                state="unavailable",
            )
        if value == KUBERNETES_PRODUCTION_KILL_SWITCH_ENGAGED:
            return KubernetesProductionKillSwitchSnapshot(
                configured=True,
                readable=True,
                valid=True,
                engaged=True,
                state="engaged",
            )
        if value == KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED:
            return KubernetesProductionKillSwitchSnapshot(
                configured=True,
                readable=True,
                valid=True,
                engaged=False,
                state="disengaged",
            )
        return KubernetesProductionKillSwitchSnapshot(
            configured=True,
            readable=True,
            valid=False,
            engaged=True,
            state="invalid",
        )

    @staticmethod
    def _credential_reference(
        environment_name: str | None,
        file_name: str | None,
    ) -> tuple[str, str] | None:
        if environment_name is not None:
            return "env", environment_name
        if file_name is not None:
            return "file", file_name
        return None

    @staticmethod
    def _default_switch_reader(
        file_name: str,
    ) -> str:
        path = Path(file_name)
        try:
            if not path.is_file():
                raise OSError
            if path.stat().st_size > _MAX_KILL_SWITCH_BYTES:
                raise OSError
            value = path.read_text(
                encoding="utf-8"
            ).rstrip("\r\n")
        except OSError:
            raise KubernetesProductionPilotBlockedError(
                "Kubernetes production kill switch is unavailable"
            ) from None
        if (
            not value
            or value != value.strip()
            or "\x00" in value
        ):
            return ""
        return value


class ProductionPilotReadinessService:
    """Read-only adapter around the shared dynamic pilot control."""

    def __init__(
        self,
        *,
        control: KubernetesProductionPilotControl,
        production_executor_configured: bool,
    ) -> None:
        if not isinstance(
            control,
            KubernetesProductionPilotControl,
        ):
            raise TypeError(
                "Production pilot readiness control is invalid"
            )
        self.control = control
        self.production_executor_configured = (
            bool(production_executor_configured)
        )

    def get(
        self,
    ) -> KubernetesProductionPilotReadinessSnapshot:
        return self.control.snapshot(
            production_executor_configured=(
                self.production_executor_configured
            )
        )


__all__ = [
    "KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED",
    "KUBERNETES_PRODUCTION_KILL_SWITCH_ENGAGED",
    "KUBERNETES_PRODUCTION_RUNBOOK_ACKNOWLEDGEMENT",
    "KubernetesProductionKillSwitchSnapshot",
    "KubernetesProductionPilotBlockedError",
    "KubernetesProductionPilotConfig",
    "KubernetesProductionPilotControl",
    "KubernetesProductionPilotError",
    "KubernetesProductionPilotReadinessSnapshot",
    "ProductionPilotReadinessService",
]
