from dataclasses import dataclass


@dataclass
class RCAFeedback:
    incident_id: str
    predicted_root_cause: str
    actual_root_cause: str
    score: float


class FeedbackLearningLoop:
    def calculate_adjustment(self, feedback: RCAFeedback):
        if feedback.predicted_root_cause == feedback.actual_root_cause:
            return {"memory_weight": +0.1, "status": "reinforced"}

        return {"memory_weight": -0.1, "status": "corrected"}
