from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class ValidationEvidence:
    run_id: str
    scenario: str
    status: str
    metrics: dict = field(default_factory=dict)
    summary: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
