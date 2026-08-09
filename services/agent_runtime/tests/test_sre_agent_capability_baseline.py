from __future__ import annotations

import asyncio
from pathlib import Path

from services.agent_runtime.app.evaluation.capability.baseline import (
    CapabilityLevel,
    build_capability_assessments,
    build_report,
    collect_repository_signals,
    run_behavioral_exams,
)


def repo_root() -> Path:
    return (
        Path(
            __file__
        )
        .resolve()
        .parents[3]
    )


def test_behavioral_exams_all_pass():
    results = asyncio.run(
        run_behavioral_exams()
    )

    assert len(
        results
    ) == 4

    assert all(
        result.passed
        for result in results
    )


def test_current_investigation_capabilities_are_not_scored_as_production():
    root = repo_root()

    signals = (
        collect_repository_signals(
            root
        )
    )

    assessments = (
        build_capability_assessments(
            signals
        )
    )

    by_key = {
        item.key: item
        for item in assessments
    }

    assert (
        by_key[
            "iterative_investigation"
        ].level
        >= CapabilityLevel.L3
    )

    assert (
        by_key[
            "evidence_reasoning"
        ].level
        >= CapabilityLevel.L3
    )

    assert (
        by_key[
            "stop_and_abstain"
        ].level
        >= CapabilityLevel.L3
    )

    assert (
        by_key[
            "production_incident_validation"
        ].level
        < CapabilityLevel.L5
    )


def test_missing_autonomous_capabilities_are_visible():
    root = repo_root()

    report = build_report(
        root
    )

    by_key = {
        item.key: item
        for item in report.assessments
    }

    assert (
        by_key[
            "logs_investigation"
        ].level
        <= CapabilityLevel.L1
    )

    assert (
        by_key[
            "rag_knowledge"
        ].level
        <= CapabilityLevel.L1
    )

    assert (
        by_key[
            "dependency_reasoning"
        ].level
        <= CapabilityLevel.L1
    )


def test_report_has_category_scores_and_recommendations():
    root = repo_root()

    report = build_report(
        root
    )

    categories = {
        item.category
        for item in report.categories
    }

    assert {
        "brain",
        "evidence",
        "knowledge",
        "remediation",
        "evaluation",
    }.issubset(
        categories
    )

    assert (
        0
        <= report.overall_score
        <= 100
    )

    assert report.top_gaps
    assert report.recommended_order


def test_l5_is_reserved_for_production_validation():
    root = repo_root()

    report = build_report(
        root
    )

    if not report.production_validated:
        assert all(
            item.level
            < CapabilityLevel.L5
            for item in report.assessments
        )
