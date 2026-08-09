import asyncio

from pathlib import Path

import pytest

from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)

from services.agent_runtime.app.incident.state import (
    IncidentState,
)

from services.agent_runtime.app.incident.store import (
    IncidentConflictError,
    IncidentStore,
)


@pytest.mark.asyncio
async def test_incident_persists_across_store_instances(
    tmp_path: Path,
):
    """
    A new IncidentStore instance must be able to read and update
    an incident written by a previous store instance.
    """

    db_path = (
        tmp_path
        / "incidents.db"
    )

    first_store = IncidentStore(
        db_path
    )

    incident = IncidentState()

    created = await first_store.save(
        incident
    )

    second_store = IncidentStore(
        db_path
    )

    loaded = await second_store.get(
        str(
            created.id
        )
    )

    assert loaded is not None

    assert loaded.id == created.id

    assert loaded.status == (
        IncidentStatus.NEW
    )

    assert loaded.created_at == (
        created.created_at
    )

    loaded.update(
        IncidentStatus.ANALYZING,
        reason="Pipeline execution started",
    )

    updated = await second_store.update(
        loaded,
        expected_status=(
            IncidentStatus.NEW
        ),
    )

    assert updated.status == (
        IncidentStatus.ANALYZING
    )

    third_store = IncidentStore(
        db_path
    )

    restored = await third_store.get(
        str(
            incident.id
        )
    )

    assert restored is not None

    assert restored.status == (
        IncidentStatus.ANALYZING
    )

    assert restored.reason == (
        "Pipeline execution started"
    )

    assert restored.updated_at > (
        restored.created_at
    )


@pytest.mark.asyncio
async def test_incident_sqlite_cas_across_store_instances(
    tmp_path: Path,
):
    """
    Two independent IncidentStore instances must not overwrite
    each other's state transition.
    """

    db_path = (
        tmp_path
        / "incidents.db"
    )

    first_store = IncidentStore(
        db_path
    )

    second_store = IncidentStore(
        db_path
    )

    incident = IncidentState()

    await first_store.save(
        incident
    )

    analyzing = await first_store.get(
        str(
            incident.id
        )
    )

    assert analyzing is not None

    analyzing.update(
        IncidentStatus.ANALYZING,
        reason="Pipeline execution started",
    )

    await first_store.update(
        analyzing,
        expected_status=(
            IncidentStatus.NEW
        ),
    )

    confirmed_incident = await first_store.get(
        str(
            incident.id
        )
    )

    failed_incident = await second_store.get(
        str(
            incident.id
        )
    )

    assert confirmed_incident is not None

    assert failed_incident is not None

    confirmed_incident.update(
        IncidentStatus.CONFIRMED,
        reason="Root cause confirmed",
    )

    failed_incident.update(
        IncidentStatus.FAILED,
        reason="Agent execution failed",
    )

    results = await asyncio.gather(
        first_store.update(
            confirmed_incident,
            expected_status=(
                IncidentStatus.ANALYZING
            ),
        ),
        second_store.update(
            failed_incident,
            expected_status=(
                IncidentStatus.ANALYZING
            ),
        ),
        return_exceptions=True,
    )

    successful_updates = [
        result
        for result in results
        if isinstance(
            result,
            IncidentState,
        )
    ]

    conflicts = [
        result
        for result in results
        if isinstance(
            result,
            IncidentConflictError,
        )
    ]

    assert len(
        successful_updates
    ) == 1

    assert len(
        conflicts
    ) == 1

    final_store = IncidentStore(
        db_path
    )

    stored = await final_store.get(
        str(
            incident.id
        )
    )

    assert stored is not None

    assert stored.status in {
        IncidentStatus.CONFIRMED,
        IncidentStatus.FAILED,
    }

    if (
        stored.status
        == IncidentStatus.CONFIRMED
    ):
        assert stored.reason == (
            "Root cause confirmed"
        )

    else:
        assert stored.reason == (
            "Agent execution failed"
        )
