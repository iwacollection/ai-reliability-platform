from fastapi import APIRouter

router = APIRouter()


@router.get("/incidents/{incident_id}/graph")
def get_investigation_graph(incident_id: str):
    return {
        "incident_id": incident_id,
        "nodes": [
            {"id": "alert", "type": "alert", "label": "Alert"},
            {"id": "event", "type": "kubernetes", "label": "OOMKilled Event"},
            {"id": "metric", "type": "metric", "label": "Memory Usage Increase", "confidence": 0.92},
            {"id": "log", "type": "log", "label": "Heap Exhausted Error"},
            {"id": "rca", "type": "rca", "label": "Memory Leak", "confidence": 0.91},
        ],
        "edges": [
            {"source": "alert", "target": "event", "relation": "triggered_by"},
            {"source": "event", "target": "metric", "relation": "supported_by"},
            {"source": "event", "target": "log", "relation": "supported_by"},
            {"source": "metric", "target": "rca", "relation": "indicates"},
            {"source": "log", "target": "rca", "relation": "indicates"},
        ],
        "traces": [
            {
                "id": "trace-1",
                "stage": "Evidence Collection",
                "tool": "Kubernetes MCP",
                "latency_ms": 120,
                "status": "completed",
            },
            {
                "id": "trace-2",
                "stage": "Prometheus Query",
                "tool": "Prometheus MCP",
                "latency_ms": 230,
                "status": "completed",
            },
        ],
        "rca": {
            "root_cause": "Memory leak caused container OOMKilled",
            "confidence": 0.91,
        },
    }
