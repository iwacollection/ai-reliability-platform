import asyncio

from services.gateway.app.parser.factory import create_parser_registry
from common.domain.raw_event import RawEvent
from services.gateway.app.publisher.runtime import RuntimePublisher


payload = {
    "receiver": "default",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "PodHighCPU",
                "severity": "critical",
                "namespace": "payment",
                "pod": "payment-api",
            },
            "annotations": {
                "summary": "CPU usage > 90%"
            },
        }
    ],
}


async def main():

    registry = create_parser_registry()

    parser = registry.get(
        "alertmanager"
    )

    event = parser.parse(
        RawEvent(
            source="alertmanager",
            payload=payload,
            headers={},
        )
    )

    print(
        event.model_dump(
            mode="json"
        )
    )


    publisher = RuntimePublisher()

    await publisher.publish(
        event
    )


if __name__ == "__main__":
    asyncio.run(main())