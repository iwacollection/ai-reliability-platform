from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import (
    UTC,
    datetime,
)
from pathlib import Path
from typing import Any


def _bootstrap_repo_root() -> Path:
    """
    Make repository packages importable when this file is executed directly.

    `python scripts/dev/<runner>.py` sets sys.path[0] to scripts/dev rather
    than the repository root. Resolve the root from __file__ before importing
    common/services packages.
    """

    start = Path(
        __file__
    ).resolve().parent

    for candidate in (
        start,
        *start.parents,
    ):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            root_text = str(
                candidate
            )

            if root_text not in sys.path:
                sys.path.insert(
                    0,
                    root_text,
                )

            return candidate

    raise RuntimeError(
        "repository_root_not_found"
    )


_BOOTSTRAPPED_REPO_ROOT = (
    _bootstrap_repo_root()
)


from common.domain.event import (
    Header,
    Resource,
    Signal,
    StandardEvent,
)
from common.domain.event.enums import (
    EventSource,
    ResourceKind,
    Severity,
    SignalType,
)

from services.agent_runtime.app.investigation.live_readiness import (
    ProductionReadinessLiveProbe,
)
from services.agent_runtime.app.model.context import (
    AgentContext,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)


REPORT_SCHEMA_VERSION = "v1"

DEFAULT_OUTPUT = (
    "production_live_readiness_report_v1.txt"
)


class RunnerInputError(
    ValueError
):
    pass


def find_repo_root(
    start: Path,
) -> Path:
    for candidate in (
        start,
        *start.parents,
    ):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate

    raise RunnerInputError(
        "repository_root_not_found"
    )


def bounded_scope_text(
    value: str,
    *,
    field: str,
) -> str:
    if (
        not isinstance(
            value,
            str,
        )
        or not value
        or value != value.strip()
        or len(
            value
        )
        > 256
        or "\x00" in value
    ):
        raise RunnerInputError(
            field
            + "_invalid"
        )

    return value


def validate_live_intent(
    *,
    acknowledgement: str | None,
    reason: str | None,
) -> None:
    if (
        acknowledgement
        != ProductionReadinessLiveProbe
        .ACKNOWLEDGEMENT
    ):
        raise RunnerInputError(
            "live_acknowledgement_invalid"
        )

    if (
        not isinstance(
            reason,
            str,
        )
        or not reason.strip()
        or reason != reason.strip()
        or len(
            reason
        )
        > 512
    ):
        raise RunnerInputError(
            "live_reason_invalid"
        )


def output_path(
    *,
    root: Path,
    value: str,
) -> Path:
    if (
        not isinstance(
            value,
            str,
        )
        or not value
        or value != value.strip()
        or "\x00" in value
    ):
        raise RunnerInputError(
            "output_path_invalid"
        )

    relative = Path(
        value
    )

    if (
        relative.is_absolute()
        or ".." in relative.parts
    ):
        raise RunnerInputError(
            "output_path_invalid"
        )

    return (
        root
        / relative
    )


def build_event(
    *,
    cluster: str,
    namespace: str,
    pod: str,
) -> StandardEvent:
    return StandardEvent(
        header=Header(
            source=EventSource.ALERTMANAGER,
            occurred_at=datetime.now(
                UTC
            ),
        ),
        signal=Signal(
            type=SignalType.ALERT,
            name=(
                "ProductionReadinessLiveProbe"
            ),
            severity=Severity.INFO,
            message=(
                "Explicit production read readiness proof"
            ),
        ),
        resources=[
            Resource(
                kind=ResourceKind.POD,
                name=pod,
                namespace=namespace,
                cluster=cluster,
            )
        ],
    )


def base_report(
    *,
    mode: str,
    cluster: str | None,
    namespace: str | None,
    pod: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": (
            REPORT_SCHEMA_VERSION
        ),
        "generated_at": (
            datetime.now(
                UTC
            ).isoformat()
        ),
        "read_only": True,
        "decision_influence": False,
        "automatic_execution": False,
        "mode": mode,
        "scope": {
            "cluster": cluster,
            "namespace": namespace,
            "pod": pod,
        },
        "static_readiness": None,
        "live_readiness": None,
        "issues": [],
    }


def write_report(
    *,
    path: Path,
    report: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description=(
            "Explicit read-only production "
            "multi-cluster readiness runner."
        )
    )

    value.add_argument(
        "--cluster",
        required=True,
        help="Exact Incident cluster identity.",
    )

    value.add_argument(
        "--namespace",
        required=True,
        help="Exact Kubernetes namespace.",
    )

    value.add_argument(
        "--pod",
        required=True,
        help="Exact Kubernetes Pod name.",
    )

    value.add_argument(
        "--live",
        action="store_true",
        help=(
            "Perform the two bounded live reads. "
            "Without this flag the runner is static-only."
        ),
    )

    value.add_argument(
        "--acknowledgement",
        default=None,
        help=(
            "Required only with --live."
        ),
    )

    value.add_argument(
        "--reason",
        default=None,
        help=(
            "Required only with --live. "
            "The reason is not written to the report."
        ),
    )

    value.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=(
            "Repository-relative sanitized report path."
        ),
    )

    return value


async def run(
    args: argparse.Namespace,
) -> tuple[
    dict[str, Any],
    int,
]:
    cluster = bounded_scope_text(
        args.cluster,
        field="cluster",
    )

    namespace = bounded_scope_text(
        args.namespace,
        field="namespace",
    )

    pod = bounded_scope_text(
        args.pod,
        field="pod",
    )

    mode = (
        "live"
        if args.live
        else "static_only"
    )

    report = base_report(
        mode=mode,
        cluster=cluster,
        namespace=namespace,
        pod=pod,
    )

    if args.live:
        validate_live_intent(
            acknowledgement=(
                args.acknowledgement
            ),
            reason=args.reason,
        )

    event = build_event(
        cluster=cluster,
        namespace=namespace,
        pod=pod,
    )

    try:
        runtime = AgentRuntime()

    except Exception:
        report[
            "issues"
        ] = [
            "runtime_initialization_failed"
        ]

        return (
            report,
            3,
        )

    gate = getattr(
        runtime,
        "production_multi_cluster_readiness",
        None,
    )

    if gate is None:
        report[
            "issues"
        ] = [
            "static_readiness_unavailable"
        ]

        return (
            report,
            2,
        )

    try:
        static_report = gate.evaluate_event(
            event
        )

    except Exception:
        report[
            "issues"
        ] = [
            "static_readiness_evaluation_failed"
        ]

        return (
            report,
            2,
        )

    report[
        "static_readiness"
    ] = static_report.snapshot()

    if not static_report.ready:
        report[
            "issues"
        ] = list(
            static_report.issues
        )

        return (
            report,
            2,
        )

    if not args.live:
        return (
            report,
            0,
        )

    context = AgentContext(
        event=event,
        memory=runtime.memory,
        tools=runtime.tools,
        skills=runtime.skills,
        metadata={},
    )

    try:
        live_snapshot = await (
            runtime
            .run_production_multi_cluster_live_readiness(
                context,
                acknowledgement=(
                    args.acknowledgement
                ),
                reason=args.reason,
            )
        )

    except Exception:
        report[
            "issues"
        ] = [
            "live_readiness_execution_failed"
        ]

        return (
            report,
            4,
        )

    report[
        "live_readiness"
    ] = dict(
        live_snapshot
    )

    if (
        live_snapshot.get(
            "ready"
        )
        is not True
    ):
        report[
            "issues"
        ] = list(
            live_snapshot.get(
                "issues",
                [
                    "live_readiness_not_ready"
                ],
            )
        )

        return (
            report,
            4,
        )

    return (
        report,
        0,
    )


def main() -> int:
    args = parser().parse_args()

    try:
        root = find_repo_root(
            Path.cwd().resolve()
        )

        report_path = output_path(
            root=root,
            value=args.output,
        )

        try:
            report, exit_code = asyncio.run(
                run(
                    args
                )
            )

        except RunnerInputError as exc:
            report = base_report(
                mode=(
                    "live"
                    if args.live
                    else "static_only"
                ),
                cluster=(
                    args.cluster
                    if isinstance(
                        args.cluster,
                        str,
                    )
                    else None
                ),
                namespace=(
                    args.namespace
                    if isinstance(
                        args.namespace,
                        str,
                    )
                    else None
                ),
                pod=(
                    args.pod
                    if isinstance(
                        args.pod,
                        str,
                    )
                    else None
                ),
            )

            report[
                "issues"
            ] = [
                str(
                    exc
                )
            ]

            exit_code = 5

        except Exception:
            report = base_report(
                mode=(
                    "live"
                    if args.live
                    else "static_only"
                ),
                cluster=None,
                namespace=None,
                pod=None,
            )

            report[
                "issues"
            ] = [
                "runner_internal_failure"
            ]

            exit_code = 6

        write_report(
            path=report_path,
            report=report,
        )

        print(
            "=" * 72
        )
        print(
            "PRODUCTION LIVE READINESS RUNNER V1"
        )
        print(
            "=" * 72
        )
        print(
            f"mode={report['mode']}"
        )
        print(
            "ready="
            + str(
                (
                    report.get(
                        "live_readiness"
                    )
                    or report.get(
                        "static_readiness"
                    )
                    or {}
                ).get(
                    "ready",
                    False,
                )
            )
        )
        print(
            "report="
            + str(
                report_path
            )
        )

        return exit_code

    except RunnerInputError:
        print(
            "PRODUCTION LIVE READINESS RUNNER V1 INPUT ERROR"
        )

        return 5


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
