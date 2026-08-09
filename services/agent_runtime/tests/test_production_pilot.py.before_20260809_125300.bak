from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from common.config.settings import (
    KUBERNETES_PRODUCTION_WRITE_ACKNOWLEDGEMENT,
    KubernetesPreflightConfig,
    KubernetesPreflightTargetConfig,
    KubernetesProductionExecutionConfig,
)
from services.agent_runtime.app.action.production_pilot import (
    KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED,
    KUBERNETES_PRODUCTION_KILL_SWITCH_ENGAGED,
    KUBERNETES_PRODUCTION_RUNBOOK_ACKNOWLEDGEMENT,
    KubernetesProductionPilotBlockedError,
    KubernetesProductionPilotConfig,
    KubernetesProductionPilotControl,
)
from services.agent_runtime.app.action.production_pilot_factory import (
    KubernetesProductionPilotFactoryError,
    create_kubernetes_production_pilot_control,
)


NOW = datetime(
    2026,
    8,
    9,
    12,
    0,
    tzinfo=UTC,
)


def preflight_config(
    *,
    targets: int = 1,
) -> KubernetesPreflightConfig:
    return KubernetesPreflightConfig(
        enabled=True,
        api_url="https://kubernetes.test",
        cluster_name="production-a",
        bearer_token_env="K8S_PREFLIGHT_TOKEN",
        allowed_targets=tuple(
            KubernetesPreflightTargetConfig(
                cluster="production-a",
                namespace="payment",
                deployment=(
                    "payment-api"
                    if index == 0
                    else f"payment-api-{index}"
                ),
                container="payment-api",
            )
            for index in range(targets)
        ),
    )


def execution_config(
    *,
    enabled: bool = True,
) -> KubernetesProductionExecutionConfig:
    if not enabled:
        return KubernetesProductionExecutionConfig(
            write_acknowledgement=(
                KUBERNETES_PRODUCTION_WRITE_ACKNOWLEDGEMENT
            ),
            bearer_token_env=(
                "K8S_PRODUCTION_EXECUTION_TOKEN"
            ),
        )
    return KubernetesProductionExecutionConfig(
        enabled=True,
        write_acknowledgement=(
            KUBERNETES_PRODUCTION_WRITE_ACKNOWLEDGEMENT
        ),
        bearer_token_env=(
            "K8S_PRODUCTION_EXECUTION_TOKEN"
        ),
    )


def pilot_config(
    **overrides,
) -> KubernetesProductionPilotConfig:
    values = {
        "enabled": True,
        "pilot_id": "oom-pilot-v1",
        "change_ticket": "CHG-6001",
        "runbook_version": "oom-runbook-v1",
        "runbook_acknowledgement": (
            KUBERNETES_PRODUCTION_RUNBOOK_ACKNOWLEDGEMENT
        ),
        "kill_switch_file": "pilot-switch",
        "authorized_operator_ids": (
            "executor-pilot-1",
        ),
        "starts_at": NOW,
        "expires_at": (
            NOW + timedelta(hours=1)
        ),
    }
    values.update(
        overrides
    )
    return KubernetesProductionPilotConfig(
        **values
    )


def control(
    *,
    switch_value: str = (
        KUBERNETES_PRODUCTION_KILL_SWITCH_ENGAGED
    ),
    now: datetime = (
        NOW + timedelta(minutes=1)
    ),
    pilot: KubernetesProductionPilotConfig | None = None,
    preflight: KubernetesPreflightConfig | None = None,
    execution: KubernetesProductionExecutionConfig | None = None,
) -> KubernetesProductionPilotControl:
    return KubernetesProductionPilotControl(
        config=pilot or pilot_config(),
        preflight_config=(
            preflight or preflight_config()
        ),
        execution_config=(
            execution or execution_config()
        ),
        clock=lambda: now,
        switch_reader=(
            lambda _: switch_value
        ),
    )


def test_default_control_is_fail_closed_without_file_access():
    calls = []
    default = KubernetesProductionPilotControl(
        config=KubernetesProductionPilotConfig(),
        preflight_config=KubernetesPreflightConfig(),
        execution_config=(
            KubernetesProductionExecutionConfig()
        ),
        clock=lambda: NOW,
        switch_reader=(
            lambda path: calls.append(path)
        ),
    )

    snapshot = default.snapshot(
        production_executor_configured=False
    )

    assert snapshot.ready_for_enablement is False
    assert snapshot.ready_for_execution is False
    assert snapshot.kill_switch.engaged is True
    assert snapshot.kill_switch.state == "unconfigured"
    assert "pilot_disabled" in snapshot.enablement_blockers
    assert calls == []


def test_engaged_switch_is_enablement_ready_but_not_execution_ready():
    snapshot = control().snapshot(
        production_executor_configured=True
    )

    assert snapshot.ready_for_enablement is True
    assert snapshot.ready_for_execution is False
    assert snapshot.kill_switch.state == "engaged"
    assert snapshot.execution_blockers == (
        "kill_switch_engaged",
    )


def test_disengaged_switch_and_authorized_operator_are_required():
    ready = control(
        switch_value=(
            KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED
        )
    )
    snapshot = ready.require_execution(
        operator_id="executor-pilot-1",
        production_executor_configured=True,
    )

    assert snapshot.ready_for_execution is True

    with pytest.raises(
        KubernetesProductionPilotBlockedError,
        match="operator",
    ):
        ready.require_execution(
            operator_id="executor-not-authorized",
            production_executor_configured=True,
        )


@pytest.mark.parametrize(
    ("switch_value", "state"),
    [
        ("", "invalid"),
        ("DISENGAGED", "invalid"),
        ("anything-else", "invalid"),
    ],
)
def test_unexpected_kill_switch_content_is_engaged(
    switch_value,
    state,
):
    snapshot = control(
        switch_value=switch_value
    ).snapshot(
        production_executor_configured=True
    )

    assert snapshot.kill_switch.state == state
    assert snapshot.kill_switch.engaged is True
    assert snapshot.ready_for_execution is False


def test_pilot_window_and_single_target_are_bounded():
    expired = control(
        now=NOW + timedelta(hours=2)
    ).snapshot(
        production_executor_configured=True
    )
    assert expired.window_state == "expired"
    assert expired.ready_for_enablement is False

    multiple = control(
        preflight=preflight_config(
            targets=2
        )
    ).snapshot(
        production_executor_configured=True
    )
    assert multiple.exact_single_target is False
    assert (
        "exact_single_target_required"
        in multiple.enablement_blockers
    )


def test_enabled_manifest_requires_runbook_window_and_operators():
    with pytest.raises(
        ValidationError
    ):
        pilot_config(
            runbook_acknowledgement="ACKNOWLEDGED"
        )

    with pytest.raises(
        ValidationError
    ):
        pilot_config(
            authorized_operator_ids=()
        )

    with pytest.raises(
        ValidationError
    ):
        pilot_config(
            expires_at=(
                NOW + timedelta(hours=5)
            )
        )


def test_factory_parses_only_bounded_non_secret_manifest():
    environment = {
        "KUBERNETES_PRODUCTION_PILOT_ENABLED": "true",
        "KUBERNETES_PRODUCTION_PILOT_ID": "oom-pilot-v1",
        "KUBERNETES_PRODUCTION_CHANGE_TICKET": "CHG-6002",
        "KUBERNETES_PRODUCTION_RUNBOOK_VERSION": "oom-runbook-v1",
        "KUBERNETES_PRODUCTION_RUNBOOK_ACKNOWLEDGEMENT": (
            KUBERNETES_PRODUCTION_RUNBOOK_ACKNOWLEDGEMENT
        ),
        "KUBERNETES_PRODUCTION_KILL_SWITCH_FILE": "pilot-switch",
        "KUBERNETES_PRODUCTION_AUTHORIZED_OPERATORS": (
            "executor-pilot-1,executor-pilot-2"
        ),
        "KUBERNETES_PRODUCTION_PILOT_STARTS_AT": (
            NOW.isoformat()
        ),
        "KUBERNETES_PRODUCTION_PILOT_EXPIRES_AT": (
            (
                NOW + timedelta(hours=1)
            ).isoformat()
        ),
    }
    created = create_kubernetes_production_pilot_control(
        preflight_config(),
        execution_config(),
        environment=environment,
        clock=lambda: (
            NOW + timedelta(minutes=1)
        ),
        switch_reader=(
            lambda _: (
                KUBERNETES_PRODUCTION_KILL_SWITCH_ENGAGED
            )
        ),
    )

    snapshot = created.snapshot(
        production_executor_configured=True
    )
    assert snapshot.ready_for_enablement is True
    assert snapshot.authorized_operator_count == 2
    serialized = snapshot.model_dump_json().lower()
    assert "kill_switch_file" not in serialized
    assert "bearer" not in serialized
    assert "token" not in serialized


def test_factory_rejects_invalid_enabled_flag_without_echoing_value():
    invalid = "true-with-secret-like-suffix"
    with pytest.raises(
        KubernetesProductionPilotFactoryError
    ) as info:
        create_kubernetes_production_pilot_control(
            preflight_config(),
            execution_config(),
            environment={
                "KUBERNETES_PRODUCTION_PILOT_ENABLED": invalid,
            },
        )
    assert invalid not in str(
        info.value
    )
