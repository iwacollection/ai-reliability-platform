from datetime import UTC, datetime

from common.domain.event import (
    Header,
    Resource,
    Signal,
    StandardEvent,
)
from common.domain.event.enums import (
    EventSource,
    ResourceKind,
    Severity,
    SignalType,
)
from common.domain.raw_event import RawEvent

from services.gateway.app.parser.base import BaseParser


class AlertManagerParser(BaseParser):
    """AlertManager webhook parser."""

    def parse(
        self,
        raw_event: RawEvent,
    ) -> StandardEvent:

        alert = raw_event.payload["alerts"][0]

        labels = alert.get("labels", {})

        signal = Signal(
            type=SignalType.ALERT,
            name=labels.get("alertname", "Unknown"),
            message=alert.get("annotations", {}).get(
                "summary",
                "",
            ),
            severity=Severity(
                labels.get("severity", "info")
            ),
            labels=labels,
        )

        resource = Resource(
            kind=ResourceKind.POD,
            name=labels.get("pod", "unknown"),
            namespace=labels.get("namespace"),
            cluster=labels.get("cluster"),
        )

        header = Header(
            source=EventSource.ALERTMANAGER,
            occurred_at=datetime.now(UTC),
        )

        return StandardEvent(
            header=header,
            signal=signal,
            resources=[resource],
        )