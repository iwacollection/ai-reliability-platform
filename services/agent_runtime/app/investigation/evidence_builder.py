"""Convert production query results into investigation evidence."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Evidence:
    source: str
    category: str
    content: dict
    collected_at: str


class EvidenceBuilder:
    def build(self, source: str, category: str, content: dict) -> Evidence:
        return Evidence(
            source=source,
            category=category,
            content=content,
            collected_at=datetime.now(timezone.utc).isoformat(),
        )
