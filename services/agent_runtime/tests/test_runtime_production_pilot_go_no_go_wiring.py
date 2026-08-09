import pytest

from services.agent_runtime.app.runtime.runtime import AgentRuntime
from services.agent_runtime.tests.test_production_pilot import (
    control,
    execution_config,
    pilot_config,
)
from services.agent_runtime.tests.test_production_pilot_go_no_go import (
    live_probe,
)
from services.agent_runtime.tests.test_production_pilot_pre_enable_evidence import (
    EXECUTOR_ID,
)
from services.agent_runtime.tests.test_runtime_kubernetes_production_wiring import (
    budget_service,
    disabled_authentication_service,
    resolver,
)


@pytest.mark.asyncio
async def test_runtime_wires_separately_gated_live_probe_and_shared_store(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)
    calls = []
    expected_probe, client = live_probe(calls)
    expected_control = control(
        pilot=pilot_config(
            authorized_operator_ids=(EXECUTOR_ID,)
        ),
        execution=execution_config(enabled=False),
    )
    expected_budget = budget_service(tmp_path)

    runtime = AgentRuntime(
        authentication_service=disabled_authentication_service(),
        kubernetes_preflight=resolver(),
        production_pilot_control=expected_control,
        production_pilot_budget_service=expected_budget,
        production_pilot_live_probe=expected_probe,
    )

    assert runtime.kubernetes_production_executor is None
    assert runtime.action_runtime.kubernetes_production_executor is None
    assert runtime.production_pilot_live_probe is expected_probe
    assert runtime.production_pilot_final_handoff_rehearsal is not None
    assert runtime.production_pilot_go_no_go_store is not None
    assert runtime.production_pilot_go_no_go is not None
    assert runtime.production_pilot_go_no_go.live_probe is expected_probe
    assert (
        runtime.production_pilot_go_no_go.final_handoff_service
        is runtime.production_pilot_final_handoff_rehearsal
    )
    assert (
        runtime.production_pilot_go_no_go.artifact_service
        is runtime.preflight_artifact_service
    )
    assert (
        runtime.production_pilot_go_no_go.pilot_control
        is runtime.production_pilot_control
    )
    assert (
        runtime.production_pilot_go_no_go.store
        is runtime.production_pilot_go_no_go_store
    )
    assert (
        tmp_path / "data" / "production_pilot_go_no_go.db"
    ).exists()
    assert calls == []
    await client.aclose()


def test_runtime_rejects_invalid_live_probe_before_store_creation(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(
        TypeError,
        match="live probe is invalid",
    ):
        AgentRuntime(
            authentication_service=disabled_authentication_service(),
            production_pilot_live_probe=object(),
        )

    assert not (
        tmp_path / "data" / "production_pilot_go_no_go.db"
    ).exists()
