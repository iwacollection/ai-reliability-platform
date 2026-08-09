from collections.abc import Mapping
from math import isfinite
from typing import Any
from unicodedata import normalize


_MAX_ROOT_CAUSE_LENGTH = 2000


def build_rca_investigation_comparison(
    *,
    rca: Any,
    investigation_snapshot: Any,
    orchestration_snapshot: Any = None,
) -> dict[str, Any]:
    """
    Build one deterministic, read-only comparison between:

    - the existing authoritative RCAAgent output; and
    - the Evidence-driven Investigation Shadow conclusion.

    This function performs no LLM request and has no decision authority.

    It deliberately does not copy raw RCA evidence or Investigation raw
    evidence into the comparison. Only bounded summaries and counts cross
    this evaluation boundary.
    """

    rca_summary = _summarize_rca(
        rca
    )

    investigation_summary = (
        _summarize_investigation(
            investigation_snapshot
        )
    )

    orchestration_failed = (
        isinstance(
            orchestration_snapshot,
            Mapping,
        )
        and orchestration_snapshot.get(
            "status"
        )
        == "failed"
    )

    exact_match = None
    normalized_text_match = None
    confidence_delta = None

    if orchestration_failed:
        comparison_status = (
            "investigation_orchestration_failed"
        )

    elif not rca_summary[
        "available"
    ]:
        comparison_status = (
            "rca_unavailable"
        )

    elif not investigation_summary[
        "available"
    ]:
        comparison_status = (
            "investigation_no_conclusion"
        )

    else:
        rca_root_cause = (
            rca_summary[
                "root_cause"
            ]
        )

        investigation_root_cause = (
            investigation_summary[
                "root_cause"
            ]
        )

        exact_match = (
            rca_root_cause
            == investigation_root_cause
        )

        normalized_text_match = (
            normalize_root_cause(
                rca_root_cause
            )
            == normalize_root_cause(
                investigation_root_cause
            )
        )

        if normalized_text_match:
            comparison_status = (
                "matched"
            )
        else:
            comparison_status = (
                "mismatched"
            )

        rca_confidence = (
            rca_summary[
                "confidence"
            ]
        )

        investigation_confidence = (
            investigation_summary[
                "confidence"
            ]
        )

        if (
            rca_confidence is not None
            and investigation_confidence
            is not None
        ):
            confidence_delta = round(
                (
                    investigation_confidence
                    - rca_confidence
                ),
                6,
            )

    return {
        "schema_version": "v1",
        "shadow_mode": True,
        "read_only": True,
        "decision_influence": False,
        "available": (
            rca_summary[
                "available"
            ]
            and investigation_summary[
                "available"
            ]
            and not orchestration_failed
        ),
        "comparison_status": (
            comparison_status
        ),
        "rca": rca_summary,
        "investigation": (
            investigation_summary
        ),
        "comparison": {
            "exact_match": (
                exact_match
            ),
            "normalized_text_match": (
                normalized_text_match
            ),
            "confidence_delta": (
                confidence_delta
            ),
        },
    }


def normalize_root_cause(
    value: str,
) -> str:
    """
    Deterministic lexical normalization only.

    This is not a semantic equivalence judgment.

    NFKC + casefold is used first. Non-alphanumeric characters are converted
    to spaces, then whitespace is collapsed.
    """

    normalized = normalize(
        "NFKC",
        value,
    ).casefold()

    normalized = "".join(
        character
        if character.isalnum()
        else " "
        for character in normalized
    )

    return " ".join(
        normalized.split()
    )


def _summarize_rca(
    value: Any,
) -> dict[str, Any]:
    if not isinstance(
        value,
        Mapping,
    ):
        return {
            "available": False,
            "root_cause": None,
            "confidence": None,
            "evidence_count": None,
        }

    root_cause = _bounded_text(
        value.get(
            "root_cause"
        )
    )

    confidence = _confidence(
        value.get(
            "confidence"
        )
    )

    evidence = value.get(
        "evidence"
    )

    evidence_count = (
        len(
            evidence
        )
        if isinstance(
            evidence,
            list,
        )
        else None
    )

    return {
        "available": (
            root_cause is not None
        ),
        "root_cause": (
            root_cause
        ),
        "confidence": (
            confidence
        ),
        "evidence_count": (
            evidence_count
        ),
    }


def _summarize_investigation(
    value: Any,
) -> dict[str, Any]:
    empty = {
        "available": False,
        "status": None,
        "stop_reason": None,
        "root_cause": None,
        "confidence": None,
        "evidence_count": 0,
        "trusted_evidence_count": 0,
        "conclusion_evidence_count": 0,
    }

    if not isinstance(
        value,
        Mapping,
    ):
        return empty

    status = _bounded_status(
        value.get(
            "status"
        )
    )

    stop_reason = _bounded_status(
        value.get(
            "stop_reason"
        )
    )

    evidence = value.get(
        "evidence"
    )

    evidence_items = (
        evidence
        if isinstance(
            evidence,
            list,
        )
        else []
    )

    trusted_evidence_count = sum(
        1
        for item in evidence_items
        if isinstance(
            item,
            Mapping,
        )
        and item.get(
            "trusted"
        )
        is True
    )

    conclusion = value.get(
        "conclusion"
    )

    if not isinstance(
        conclusion,
        Mapping,
    ):
        return {
            **empty,
            "status": status,
            "stop_reason": (
                stop_reason
            ),
            "evidence_count": len(
                evidence_items
            ),
            "trusted_evidence_count": (
                trusted_evidence_count
            ),
        }

    root_cause = _bounded_text(
        conclusion.get(
            "root_cause"
        )
    )

    confidence = _confidence(
        conclusion.get(
            "confidence"
        )
    )

    conclusion_evidence_ids = (
        conclusion.get(
            "evidence_ids"
        )
    )

    conclusion_evidence_count = (
        len(
            conclusion_evidence_ids
        )
        if isinstance(
            conclusion_evidence_ids,
            list,
        )
        else 0
    )

    sufficient_conclusion = (
        status == "concluded"
        and stop_reason
        == "sufficient_evidence"
        and root_cause is not None
    )

    return {
        "available": (
            sufficient_conclusion
        ),
        "status": status,
        "stop_reason": stop_reason,
        "root_cause": (
            root_cause
            if sufficient_conclusion
            else None
        ),
        "confidence": (
            confidence
            if sufficient_conclusion
            else None
        ),
        "evidence_count": len(
            evidence_items
        ),
        "trusted_evidence_count": (
            trusted_evidence_count
        ),
        "conclusion_evidence_count": (
            conclusion_evidence_count
            if sufficient_conclusion
            else 0
        ),
    }


def _bounded_text(
    value: Any,
) -> str | None:
    if not isinstance(
        value,
        str,
    ):
        return None

    text = value.strip()

    if not text:
        return None

    return text[
        :_MAX_ROOT_CAUSE_LENGTH
    ]


def _bounded_status(
    value: Any,
) -> str | None:
    if not isinstance(
        value,
        str,
    ):
        return None

    text = value.strip()

    if (
        not text
        or len(text) > 64
    ):
        return None

    return text


def _confidence(
    value: Any,
) -> float | None:
    if isinstance(
        value,
        bool,
    ):
        return None

    try:
        result = float(
            value
        )
    except (
        TypeError,
        ValueError,
    ):
        return None

    if (
        not isfinite(
            result
        )
        or result < 0.0
        or result > 1.0
    ):
        return None

    return result


__all__ = [
    "build_rca_investigation_comparison",
    "normalize_root_cause",
]
