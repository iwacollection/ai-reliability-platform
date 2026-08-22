from fastapi import APIRouter


router = APIRouter()


_DEMO_INCIDENTS = [
    {
        "id": "inc-001",
        "title": "payment-api OOMKilled",
        "service": "payment-api",
        "severity": "critical",
        "status": "investigating",
        "agent": "investigation-agent",
    },
    {
        "id": "inc-002",
        "title": "gateway latency spike",
        "service": "gateway",
        "severity": "warning",
        "status": "analyzing",
        "agent": "investigation-agent",
    },
]


@router.get("/incidents")
def list_incidents():
    return {
        "items": _DEMO_INCIDENTS,
        "total": len(_DEMO_INCIDENTS),
    }


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: str):
    for incident in _DEMO_INCIDENTS:
        if incident["id"] == incident_id:
            return incident

    return {
        "id": incident_id,
        "status": "unknown",
    }
