from fastapi import APIRouter


router = APIRouter()


_EVIDENCE = {
    "inc-001": {
        "timeline": [
            {"time": "14:00", "event": "Alert received", "source": "AlertManager"},
            {"time": "14:01", "event": "Investigation started", "source": "Investigation Agent"},
            {"time": "14:03", "event": "Evidence collected", "source": "Kubernetes MCP"},
        ],
        "items": [
            {"type": "metric", "name": "container_memory_usage", "confidence": 0.92},
            {"type": "event", "name": "OOMKilled", "confidence": 0.95},
        ],
        "rca": {
            "root_cause": "memory pressure caused container OOMKilled",
            "confidence": 0.91,
        },
    }
}


@router.get("/incidents/{incident_id}/evidence")
def get_incident_evidence(incident_id: str):
    return _EVIDENCE.get(
        incident_id,
        {
            "timeline": [],
            "items": [],
            "rca": None,
        },
    )
