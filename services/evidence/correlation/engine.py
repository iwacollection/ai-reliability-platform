from .models import CorrelationResult, EvidenceNode, EvidenceRelation


class EvidenceCorrelationEngine:
    """Correlates multi signal evidence into causal relationships."""

    def correlate(self, evidences: list[EvidenceNode]) -> CorrelationResult:
        relations = []

        for left in evidences:
            for right in evidences:
                if left.evidence_id == right.evidence_id:
                    continue

                if left.evidence_type == "metric" and right.evidence_type == "event":
                    relations.append(
                        EvidenceRelation(
                            left.evidence_id,
                            right.evidence_id,
                            "supports",
                            0.8,
                        )
                    )

                if left.evidence_type == "log" and right.evidence_type == "trace":
                    relations.append(
                        EvidenceRelation(
                            left.evidence_id,
                            right.evidence_id,
                            "correlated_with",
                            0.7,
                        )
                    )

        return CorrelationResult(
            nodes=evidences,
            relations=relations,
        )
