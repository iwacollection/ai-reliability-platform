from typing import Any

from fastapi import APIRouter, Body, Request

from common.domain.raw_event import RawEvent
from services.gateway.app.parser.factory import create_parser_registry
from services.gateway.app.publisher.factory import create_publisher

router = APIRouter()

registry = create_parser_registry()


@router.post(
    "/alertmanager",
    summary="AlertManager Webhook",
)
async def alertmanager_webhook(
    request: Request,
    payload: dict[str, Any] = Body(
        ...,
        examples={
            "default": {
                "summary": "AlertManager Example",
                "value": {
                    "receiver": "default",
                    "alerts": [
                        {
                            "status": "firing",
                            "labels": {
                                "alertname": "PodHighCPU",
                                "severity": "critical",
                                "namespace": "payment",
                                "pod": "payment-api-6df78",
                            },
                            "annotations": {
                                "summary": "CPU usage > 90%"
                            },
                        }
                    ],
                },
            }
        },
    ),
):
    """
    Receive AlertManager webhook.
    """

    raw_event = RawEvent(
        source="alertmanager",
        payload=payload,
        headers=dict(request.headers),
    )

    parser = registry.get("alertmanager")

    event = parser.parse(raw_event)

    publisher = create_publisher()

    await publisher.publish(event)

    return event.model_dump(mode="json")