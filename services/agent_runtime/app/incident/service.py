from uuid import UUID


from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)


from services.agent_runtime.app.incident.state import (
    IncidentState,
)


from services.agent_runtime.app.incident.store import (
    IncidentStore,
)


from services.agent_runtime.app.incident.timeline import (
    IncidentTimelineEvent,
    IncidentTimelineEventType,
)



class IncidentService:
    """
    Incident lifecycle service.

    Responsibilities:

    - state transition
    - persistence
    - timeline recording

    """



    def __init__(
        self,
        store: IncidentStore,
    ) -> None:

        self.store = store

        self.timeline: list[
            IncidentTimelineEvent
        ] = []



    async def create(
        self,
        incident: IncidentState,
        reason: str = "",
    ) -> IncidentState:
        """
        Create incident.
        """


        await self.store.save(
            incident
        )


        self._append_timeline(

            incident.id,

            IncidentTimelineEventType.CREATED,

            source="incident",

            message=reason,

        )


        return incident



    async def transition(
        self,
        incident: IncidentState,
        status: IncidentStatus,
        reason: str = "",
        source: str = "runtime",
    ) -> IncidentState:
        """
        Change incident status.
        """


        incident.update(
            status=status,
            reason=reason,
        )


        await self.store.update(
            incident
        )


        self._append_timeline(

            incident.id,

            self._status_to_event(
                status
            ),

            source=source,

            message=reason,

        )


        return incident



    def _append_timeline(
        self,
        incident_id: UUID,
        event_type: IncidentTimelineEventType,
        source: str,
        message: str,
    ):


        event = IncidentTimelineEvent(

            incident_id=incident_id,

            event_type=event_type,

            source=source,

            message=message,

        )


        self.timeline.append(
            event
        )



    def get_timeline(
        self,
        incident_id: UUID,
    ) -> list[IncidentTimelineEvent]:
        """
        Get incident timeline.
        """


        return [

            event

            for event

            in self.timeline

            if event.incident_id == incident_id

        ]



    @staticmethod
    def _status_to_event(
        status: IncidentStatus,
    ) -> IncidentTimelineEventType:


        mapping = {

            IncidentStatus.ANALYZING:
                IncidentTimelineEventType.AGENT_STARTED,


            IncidentStatus.CONFIRMED:
                IncidentTimelineEventType.ACTION_APPROVAL_REQUIRED,


            IncidentStatus.HEALING:
                IncidentTimelineEventType.ACTION_EXECUTED,


            IncidentStatus.RESOLVED:
                IncidentTimelineEventType.RESOLVED,


            IncidentStatus.FAILED:
                IncidentTimelineEventType.FAILED,

        }


        return mapping.get(

            status,

            IncidentTimelineEventType.CREATED,

        )
    