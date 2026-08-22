from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List


@dataclass
class MemoryFeedback:
    incident_id: str
    feedback_type: str
    accepted: bool
    operator_note: str = ""
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class MemoryRecord:
    key: str
    category: str
    content: Dict
    confidence: float = 0.5
    usage_count: int = 0
    feedback: List[MemoryFeedback] = field(default_factory=list)


class MemoryEvolutionEngine:
    def update_from_feedback(self, memory: MemoryRecord, feedback: MemoryFeedback):
        memory.feedback.append(feedback)
        memory.usage_count += 1

        if feedback.accepted:
            memory.confidence = min(1.0, memory.confidence + 0.05)
        else:
            memory.confidence = max(0.0, memory.confidence - 0.1)

        return memory
