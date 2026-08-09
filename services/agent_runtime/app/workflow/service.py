from services.agent_runtime.app.workflow.enums import (
    WorkflowEvent,
)


from services.agent_runtime.app.workflow.transition import (
    WorkflowTransitionEngine,
)


from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)


from services.agent_runtime.app.incident.timeline import (
    IncidentTimelineEventType,
)



class WorkflowService:
    """
    Workflow orchestration service.

    Responsible for:

    - validate workflow transition
    - delegate incident mutation
    - append workflow timeline

    """

    def __init__(
        self,
        incident_service,
    ) -> None:

        self.incident_service = (
            incident_service
        )

        self.transition_engine = (
            WorkflowTransitionEngine()
        )


    async def transition(
        self,
        incident_id: str,
        event: WorkflowEvent,
        reason: str = "",
        source: str = "workflow",
    ):

        #
        # 1.
        # Load incident
        #
        incident = (
            await self.incident_service.store.get(
                incident_id
            )
        )


        if incident is None:

            raise ValueError(
                f"Incident not found: {incident_id}"
            )


        #
        # 2.
        # Calculate next status
        #
        next_status = (
            self.transition_engine.transition(
                incident.status,
                event,
            )
        )


        #
        # 3.
        # Delegate state mutation
        #
        await self.incident_service.transition(
            incident=incident,
            status=next_status,
            reason=reason,
            source=source,
        )


        #
        # 4.
        # Return latest state
        #
        return incident



    @staticmethod
    def _event_to_timeline(
        event: WorkflowEvent,
    ) -> IncidentTimelineEventType:

        mapping = {

            WorkflowEvent.INCIDENT_CREATED:
                IncidentTimelineEventType.CREATED,


            WorkflowEvent.RCA_COMPLETED:
                IncidentTimelineEventType.RCA_COMPLETED,


            WorkflowEvent.ACTION_STARTED:
                IncidentTimelineEventType.ACTION_CREATED,


            WorkflowEvent.ACTION_COMPLETED:
                IncidentTimelineEventType.ACTION_EXECUTED,


            WorkflowEvent.VERIFICATION_STARTED:
                IncidentTimelineEventType.VERIFICATION_STARTED,


            WorkflowEvent.VERIFICATION_PASSED:
                IncidentTimelineEventType.RESOLVED,


            WorkflowEvent.VERIFICATION_FAILED:
                IncidentTimelineEventType.FAILED,

        }


        return mapping.get(
            event,
            IncidentTimelineEventType.CREATED,
        )
