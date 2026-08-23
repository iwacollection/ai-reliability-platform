from typing import Dict, List


class RCARankingEngine:
    """Rank RCA hypotheses using multi signal evidence."""

    def rank(self, hypotheses: List[Dict], evidence_count: int) -> List[Dict]:
        results = []

        for item in hypotheses:
            score = float(item.get("score", 0))
            score += min(evidence_count * 5, 30)

            results.append({
                **item,
                "confidence": min(score, 100),
            })

        return sorted(
            results,
            key=lambda x: x["confidence"],
            reverse=True,
        )
