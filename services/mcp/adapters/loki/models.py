from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class LokiQueryRequest:
    """Loki LogQL query request."""

    query: str
    start: Optional[str] = None
    end: Optional[str] = None
    limit: int = 100


@dataclass
class LokiQueryResponse:
    success: bool
    data: Dict[str, Any]
    error: Optional[str] = None
