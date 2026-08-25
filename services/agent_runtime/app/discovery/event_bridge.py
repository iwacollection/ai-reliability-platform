from common.domain.event import Header, Resource, Signal, StandardEvent
from common.domain.event.enums import (
    EventSource,
    ResourceKind,
    Severity,
    SignalType,
)
from services.agent_runtime.app.discovery.models import DiscoveryFinding


def _event_source(value: str) -> EventSource:
    try:
        return EventSource(value.lower())
    except ValueError:
        return EventSource.CUSTOM


def _resource_kind(value: str) -> ResourceKind:
    normalized = value.lower()
    try:
        return ResourceKind(normalized)
    except ValueError:
        return ResourceKind.CUSTOM


def finding_to_standard_event(finding: DiscoveryFinding) -> StandardEvent:
    """Promote a proactive finding into the platform Standard Event contract."""

    observation = finding.observation
    resource_data = observation.resource
    resource_kind = str(resource_data.get("kind") or observation.kind)

    return StandardEvent(
        header=Header(
            source=_event_source(observation.source),
            occurred_at=observation.observed_at,
        ),
        signal=Signal(
            type=SignalType.EVENT,
            name=finding.rule_id,
            message=finding.summary,
            severity=Severity(finding.severity),
            value=finding.score,
            labels={"discovery": "proactive"},
            metadata={
                "title": finding.title,
                "fingerprint": observation.fingerprint,
                "discovery_score": finding.score,
            },
        ),
        resources=[
            Resource(
                kind=_resource_kind(resource_kind),
                name=str(resource_data.get("name") or "unknown"),
                namespace=resource_data.get("namespace"),
                cluster=resource_data.get("cluster"),
                labels=resource_data.get("labels") or {},
                attributes={
                    key: value
                    for key, value in resource_data.items()
                    if key not in {"kind", "name", "namespace", "cluster", "labels"}
                },
            )
        ],
    )
