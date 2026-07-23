"""
Common enumerations for the Event Domain.

These enums define the shared vocabulary used across the AI Reliability
Platform. Every service should reference these enums instead of using
hard-coded strings.
"""

from enum import StrEnum


class Severity(StrEnum):
    """Alert severity."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class EventStatus(StrEnum):
    """Lifecycle status of an event."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    INVESTIGATING = "investigating"
    HEALING = "healing"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    CLOSED = "closed"


class EventSource(StrEnum):
    """Where the event originates from."""

    ALERTMANAGER = "alertmanager"
    PROMETHEUS = "prometheus"
    GRAFANA = "grafana"
    KUBERNETES = "kubernetes"
    CLOUDWATCH = "cloudwatch"
    DATADOG = "datadog"
    ELK = "elk"
    CUSTOM = "custom"