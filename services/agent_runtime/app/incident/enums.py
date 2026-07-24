from enum import Enum


class IncidentStatus(str, Enum):
    """
    Incident lifecycle status.
    """

    NEW = "new"

    ANALYZING = "analyzing"

    CONFIRMED = "confirmed"

    HEALING = "healing"

    RESOLVED = "resolved"

    FAILED = "failed"