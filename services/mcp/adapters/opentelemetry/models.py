from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class TraceQueryRequest:
    """Distributed trace query request."""

    trace_id: Optional[str] = None
    service_name: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    limit: int = 100


@dataclass
class TraceResponse:
    success: bool
    data: Dict[str, Any]
    error: Optional[str] = None
