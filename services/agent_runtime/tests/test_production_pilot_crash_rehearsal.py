import asyncio
import builtins
import socket
import sqlite3

from pathlib import Path

import pytest
from pydantic import ValidationError

from services.agent_runtime.app.action.production_pilot_crash_rehearsal import (
    ProductionPilotCrashCutPoint,
    ProductionPilotCrashRecoveryRehearsalReport,
    ProductionPilotCrashRecoveryRehearsalService,
)


OPERATOR_ID = "executor-crash-rehearsal"


async def run_report():
    return await (
        ProductionPilotCrashRecoveryRehearsalService()
        .run(
            operator_id=OPERATOR_ID
        )
    )


@pytest.mark.asyncio
async def test_rehearsal_covers_every_durable_cut_point_once():
    report = await run_report()

    assert report.passed is True
    assert report.checkpoint_count == 13
    assert report.passed_checkpoint_count == 13
    assert tuple(
        item.sequence
        for item in report.checkpoints
    ) == tuple(range(1, 14))
    assert {
        item.cut_point
        for item in report.checkpoints
    } == {
        item.value
        for item in ProductionPilotCrashCutPoint
    }


@pytest.mark.asyncio
async def test_rehearsal_never_authorizes_automatic_action_replay():
    report = await run_report()

    assert report.authorizes_enablement is False
    assert report.authorizes_execution is False
    assert report.automatic_action_replay_allowed is False
    assert all(
        item.automatic_action_replay_allowed is False
        and item.production_executor_call_allowed is False
        and item.budget_reset_allowed is False
        for item in report.checkpoints
    )

    first_resume = [
        item
        for item in report.checkpoints
        if item.authenticated_first_resume_allowed
    ]
    assert len(first_resume) == 1
    assert first_resume[0].cut_point == (
        ProductionPilotCrashCutPoint
        .APPROVAL_APPROVED_CEREMONY_READY
        .value
    )


@pytest.mark.asyncio
async def test_post_claim_cut_points_require_safe_recovery():
    report = await run_report()
    by_cut_point = {
        item.cut_point: item
        for item in report.checkpoints
    }

    manual = {
        ProductionPilotCrashCutPoint.ACTION_EXECUTION_CLAIMED.value,
        ProductionPilotCrashCutPoint.CEREMONY_ACTIVATED.value,
        ProductionPilotCrashCutPoint.PILOT_BUDGET_RESERVED.value,
        ProductionPilotCrashCutPoint.PILOT_BUDGET_CONSUMED.value,
        ProductionPilotCrashCutPoint.ACTION_EXECUTION_INDETERMINATE.value,
    }
    assert all(
        by_cut_point[item].manual_reconciliation_required
        for item in manual
    )
    assert all(
        "do_not_retry_resume"
        in by_cut_point[item].guidance
        for item in manual
    )

    verification_only = {
        ProductionPilotCrashCutPoint.ACTION_EXECUTION_SUCCEEDED.value,
        ProductionPilotCrashCutPoint.VERIFICATION_CLAIMED.value,
    }
    assert all(
        by_cut_point[item].verification_recovery_allowed
        for item in verification_only
    )
    assert all(
        by_cut_point[item].execution_state == "succeeded"
        for item in verification_only
    )


@pytest.mark.asyncio
async def test_exact_replay_is_digest_stable():
    service = (
        ProductionPilotCrashRecoveryRehearsalService()
    )

    first = await service.run(
        operator_id=OPERATOR_ID
    )
    second = await service.run(
        operator_id=OPERATOR_ID
    )

    assert first == second
    assert first.report_sha256 == second.report_sha256
    assert first.model_dump(mode="json") == (
        second.model_dump(mode="json")
    )


@pytest.mark.asyncio
async def test_rehearsal_report_digest_and_safe_blueprints_are_immutable():
    report = await run_report()

    tampered = report.model_dump(
        mode="json"
    )
    tampered["report_sha256"] = "0" * 64
    with pytest.raises(
        ValidationError,
        match="report digest is invalid",
    ):
        ProductionPilotCrashRecoveryRehearsalReport.model_validate(
            tampered
        )

    unsafe = report.model_dump(
        mode="json"
    )
    unsafe["checkpoints"][3][
        "manual_reconciliation_required"
    ] = False
    with pytest.raises(
        ValidationError,
        match="checkpoint is unsafe",
    ):
        ProductionPilotCrashRecoveryRehearsalReport.model_validate(
            unsafe
        )


@pytest.mark.asyncio
async def test_rehearsal_is_pure_with_zero_storage_and_network_access(
    monkeypatch,
):
    def forbidden(*args, **kwargs):
        raise AssertionError(
            "Zero-write rehearsal attempted external access"
        )

    monkeypatch.setattr(
        sqlite3,
        "connect",
        forbidden,
    )
    monkeypatch.setattr(
        builtins,
        "open",
        forbidden,
    )
    monkeypatch.setattr(
        Path,
        "open",
        forbidden,
    )
    monkeypatch.setattr(
        socket,
        "create_connection",
        forbidden,
    )

    report = await run_report()

    assert report.synthetic_rehearsal is True
    assert report.live_state_checked is False
    assert report.durable_claim_created is False
    assert report.storage_read_count == 0
    assert report.storage_write_count == 0
    assert report.external_call_count == 0
    assert report.kubernetes_call_count == 0
    assert report.production_executor_call_count == 0
    assert report.verification_call_count == 0
    assert report.budget_reservation_count == 0
    assert report.real_write_attempted is False


def test_invalid_operator_is_rejected_without_io():
    with pytest.raises(
        ValueError,
        match="operator is invalid",
    ):
        asyncio.run(
            ProductionPilotCrashRecoveryRehearsalService().run(
                operator_id=" invalid operator "
            )
        )

