from enum import Enum


class WorkflowEvent(str, Enum):
    """
    Workflow domain events.

    WorkflowEvent describes what happened,
    not the final state.

    Example:

        RCA_COMPLETED

    causes:

        ANALYZING
              |
              v
        WAITING_APPROVAL

    """

    INCIDENT_CREATED = (
        "incident_created"
    )

    ANALYSIS_STARTED = (
        "analysis_started"
    )

    ANALYSIS_COMPLETED = (
        "analysis_completed"
    )

    RCA_COMPLETED = (
        "rca_completed"
    )

    APPROVAL_REQUIRED = (
        "approval_required"
    )

    APPROVAL_GRANTED = (
        "approval_granted"
    )

    APPROVAL_REJECTED = (
        "approval_rejected"
    )

    ACTION_STARTED = (
        "action_started"
    )

    ACTION_COMPLETED = (
        "action_completed"
    )

    ACTION_FAILED = (
        "action_failed"
    )

    VERIFICATION_STARTED = (
        "verification_started"
    )

    VERIFICATION_PASSED = (
        "verification_passed"
    )

    VERIFICATION_FAILED = (
        "verification_failed"
    )

    INCIDENT_RESOLVED = (
        "incident_resolved"
    )

    INCIDENT_FAILED = (
        "incident_failed"
    )