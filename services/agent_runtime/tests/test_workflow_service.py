import pytest


from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)


from services.agent_runtime.app.incident.state import (
    IncidentState,
)


from services.agent_runtime.app.incident.store import (
    IncidentStore,
)


from services.agent_runtime.app.incident.service import (
    IncidentService,
)


from services.agent_runtime.app.workflow.service import (
    WorkflowService,
)


from services.agent_runtime.app.workflow.enums import (
    WorkflowEvent,
)



@pytest.mark.asyncio
async def test_workflow_transition_flow(tmp_path):

    store = IncidentStore(
        db_path=(
            tmp_path
            /
            "incident.db"
        )
    )


    incident_service = IncidentService(
        store=store
    )


    workflow = WorkflowService(
        incident_service
    )


    incident = IncidentState()


    await incident_service.create(
        incident,
        reason="test incident",
    )


    #
    # NEW -> ANALYZING
    #
    result = await workflow.transition(
        str(incident.id),
        WorkflowEvent.INCIDENT_CREATED,
        reason="start analysis",
    )


    assert (
        result.status
        ==
        IncidentStatus.ANALYZING
    )


    #
    # ANALYZING -> CONFIRMED
    #
    result = await workflow.transition(
        str(incident.id),
        WorkflowEvent.RCA_COMPLETED,
        reason="root cause confirmed",
    )


    assert (
        result.status
        ==
        IncidentStatus.CONFIRMED
    )


    #
    # CONFIRMED -> HEALING
    #
    result = await workflow.transition(
        str(incident.id),
        WorkflowEvent.ACTION_STARTED,
        reason="start remediation",
    )


    assert (
        result.status
        ==
        IncidentStatus.HEALING
    )


    timeline = (
        incident_service.get_timeline(
            incident.id
        )
    )


    assert len(timeline) >= 4
