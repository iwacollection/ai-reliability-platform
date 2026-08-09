from dataclasses import dataclass


from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)


from services.agent_runtime.app.workflow.enums import (
    WorkflowEvent,
)



@dataclass(frozen=True)
class TransitionRule:
    """
    Workflow transition rule.

    Defines:

    current status
        +
    event

        =>

    next status
    """

    current: IncidentStatus

    event: WorkflowEvent

    next: IncidentStatus



class WorkflowTransitionEngine:
    """
    Workflow state transition engine.

    Workflow only controls Incident lifecycle.

    ActionExecutionStatus and VerificationStatus
    are managed by their own domains.
    """

    def __init__(self) -> None:

        self._rules = {

            #
            # Incident creation
            #
            (
                IncidentStatus.NEW,
                WorkflowEvent.INCIDENT_CREATED,
            ):
                IncidentStatus.ANALYZING,


            #
            # RCA completed
            #
            (
                IncidentStatus.ANALYZING,
                WorkflowEvent.RCA_COMPLETED,
            ):
                IncidentStatus.CONFIRMED,


            #
            # Start remediation
            #
            (
                IncidentStatus.CONFIRMED,
                WorkflowEvent.ACTION_STARTED,
            ):
                IncidentStatus.HEALING,


            #
            # Action failure
            #
            (
                IncidentStatus.HEALING,
                WorkflowEvent.ACTION_FAILED,
            ):
                IncidentStatus.FAILED,


            #
            # Verification passed
            #
            (
                IncidentStatus.HEALING,
                WorkflowEvent.VERIFICATION_PASSED,
            ):
                IncidentStatus.RESOLVED,


            #
            # Verification failed
            #
            (
                IncidentStatus.HEALING,
                WorkflowEvent.VERIFICATION_FAILED,
            ):
                IncidentStatus.FAILED,


            #
            # Manual resolution
            #
            (
                IncidentStatus.CONFIRMED,
                WorkflowEvent.INCIDENT_RESOLVED,
            ):
                IncidentStatus.RESOLVED,


            #
            # Manual failure
            #
            (
                IncidentStatus.CONFIRMED,
                WorkflowEvent.INCIDENT_FAILED,
            ):
                IncidentStatus.FAILED,

        }


    def can_transition(
        self,
        current: IncidentStatus,
        event: WorkflowEvent,
    ) -> bool:
        """
        Check whether transition is allowed.
        """

        return (
            current,
            event,
        ) in self._rules



    def transition(
        self,
        current: IncidentStatus,
        event: WorkflowEvent,
    ) -> IncidentStatus:
        """
        Calculate next IncidentStatus.

        Raises:
            ValueError

        when transition is invalid.
        """

        key = (
            current,
            event,
        )

        if key not in self._rules:

            raise ValueError(
                "Invalid workflow transition: "
                f"{current} + {event}"
            )

        return self._rules[key]