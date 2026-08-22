from typing import Any


def normalize_mcp_result(server: str, result: dict[str, Any]) -> dict[str, Any]:
    """Convert MCP output into Evidence Collector compatible format."""
    return {
        "source": server,
        "evidence_type": "tool_result",
        "payload": result,
        "confidence": 0.5,
    }
