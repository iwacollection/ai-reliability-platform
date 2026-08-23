from typing import Any, Dict


class LokiEvidenceCollector:
    """Convert Loki responses into reliability evidence records."""

    def collect(self, result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": "log",
            "source": "loki-mcp",
            "payload": result,
        }
