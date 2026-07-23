import json
from pathlib import Path
from pprint import pprint

from common.domain.raw_event import RawEvent
from services.gateway.app.parser.alertmanager import AlertManagerParser


def test_alertmanager_parser():
    json_path = (
        Path(__file__).parent
        / "data"
        / "alertmanager.json"
    )

    payload = json.loads(json_path.read_text(encoding="utf-8"))

    raw_event = RawEvent(
        source="alertmanager",
        payload=payload,
    )

    parser = AlertManagerParser()

    event = parser.parse(raw_event)

    pprint(event.model_dump())

    assert event.signal.name == "PodHighCPU"
    assert event.signal.severity.value == "critical"
    assert event.resources[0].name == "payment-api-6df78"