from dataclasses import dataclass


@dataclass
class EvidenceScore:
    evidence_id: str
    source_reliability: float
    freshness: float
    relevance: float

    @property
    def confidence(self) -> float:
        return round(
            self.source_reliability
            * self.freshness
            * self.relevance,
            4,
        )


class EvidenceConfidenceScorer:
    def score(
        self,
        evidence_id: str,
        source_reliability: float,
        freshness: float,
        relevance: float,
    ) -> EvidenceScore:
        return EvidenceScore(
            evidence_id=evidence_id,
            source_reliability=source_reliability,
            freshness=freshness,
            relevance=relevance,
        )
