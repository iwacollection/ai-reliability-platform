from __future__ import annotations

import hashlib
import traceback
from datetime import datetime
from pathlib import Path


OUTPUT_NAME = (
    "multi_cluster_connection_config_current_code_snapshot_v1.txt"
)

ERROR_NAME = (
    "multi_cluster_connection_config_current_code_snapshot_v1_error.txt"
)

CORE_CANDIDATES = (
    "packages/common/src/common/config/settings.py",
    "packages/common/src/common/config/__init__.py",
    "packages/common/src/common/config/loader.py",
    "services/agent_runtime/app/tools/factory.py",
    "services/agent_runtime/app/tools/kubernetes/router.py",
    "services/agent_runtime/app/tools/kubernetes/tool.py",
    "services/agent_runtime/app/runtime/runtime.py",
    "services/agent_runtime/app/action/kubernetes_preflight_factory.py",
    "services/agent_runtime/app/action/kubernetes_production_factory.py",
)

DISCOVERY_TOKENS = (
    "KubernetesPreflightConfig",
    "KubernetesProductionExecutionConfig",
    "get_settings(",
    "BaseSettings",
    "SettingsConfigDict",
    "KUBERNETES_API_URL",
    "KUBERNETES_CLUSTER_NAME",
    "bearer_token_env",
    "bearer_token_file",
    "ca_file",
    "api_url",
    "cluster_name",
    "KubernetesClusterRegistry",
    "create_tool_manager(",
)


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

    raise RuntimeError(
        "Repository root not found."
    )


def normalized_relative(
    path: Path,
    root: Path,
) -> str:
    return str(
        path.relative_to(
            root
        )
    ).replace(
        "\\",
        "/",
    )


def sha256_bytes(
    data: bytes,
) -> str:
    return hashlib.sha256(
        data
    ).hexdigest()


def discover_related_files(
    root: Path,
) -> list[Path]:
    roots = [
        root / "packages" / "common" / "src" / "common",
        root / "services" / "agent_runtime" / "app",
    ]

    discovered = []

    for scan_root in roots:
        if not scan_root.exists():
            continue

        for path in sorted(
            scan_root.rglob(
                "*.py"
            )
        ):
            if "__pycache__" in path.parts:
                continue

            text = path.read_text(
                encoding="utf-8-sig",
                errors="strict",
            )

            if any(
                token in text
                for token in DISCOVERY_TOKENS
            ):
                discovered.append(
                    path
                )

    return discovered


def line_hits(
    text: str,
) -> list[str]:
    hits = []

    for number, row in enumerate(
        text.splitlines(),
        start=1,
    ):
        matched = [
            token
            for token in DISCOVERY_TOKENS
            if token in row
        ]

        if not matched:
            continue

        hits.append(
            (
                f"L{number}: "
                f"tokens={matched} "
                f"text={row}"
            )
        )

    return hits


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    output = root / OUTPUT_NAME
    error = root / ERROR_NAME

    for path in (
        output,
        error,
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    try:
        selected = {}

        for relative in CORE_CANDIDATES:
            path = root / relative

            if path.exists():
                selected[
                    normalized_relative(
                        path,
                        root,
                    )
                ] = path

        for path in discover_related_files(
            root
        ):
            selected[
                normalized_relative(
                    path,
                    root,
                )
            ] = path

        if not selected:
            raise RuntimeError(
                "No multi-cluster connection/config related Python files were found."
            )

        report = [
            "Multi-Cluster Connection Config Current Code Snapshot v1",
            (
                "GeneratedAt: "
                + datetime.now().astimezone().isoformat()
            ),
            "",
            "Purpose:",
            "- capture current validated settings/config loading before adding multi-cluster connection descriptors",
            "- preserve exact SHA256 values for stale-source protection",
            "- discover existing Kubernetes credential-reference patterns so Router config does not invent a second security model",
            "- make no source-code changes",
            "",
            "Router v1 baseline already installed:",
            "- KubernetesClusterRegistry exists",
            "- Runtime can opt into multi-cluster read routing",
            "- cluster selection is exact/fail-closed",
            "- production write plane remains separate",
            "",
            "Connection Config v1 questions:",
            "- where top-level settings models live",
            "- whether settings are env-only, YAML/app.yaml, or merged",
            "- how secret references are represented today",
            "- how CA paths and API URLs are validated",
            "- how disabled-default configuration is expressed",
            "- whether Runtime startup can safely build registry before network access",
            "",
            "Selected files:",
        ]

        for relative in sorted(
            selected
        ):
            report.append(
                f"- {relative}"
            )

        report.append(
            ""
        )

        for relative in sorted(
            selected
        ):
            path = selected[
                relative
            ]

            raw = path.read_bytes()

            text = raw.decode(
                "utf-8-sig",
                errors="strict",
            )

            report.extend(
                [
                    "=" * 120,
                    relative,
                    "=" * 120,
                    (
                        "sha256="
                        + sha256_bytes(
                            raw
                        )
                    ),
                    (
                        "lines="
                        + str(
                            len(
                                text.splitlines()
                            )
                        )
                    ),
                    "",
                    "CONFIG / CREDENTIAL HITS",
                    "-" * 120,
                ]
            )

            hits = line_hits(
                text
            )

            if hits:
                report.extend(
                    hits
                )
            else:
                report.append(
                    "<NONE>"
                )

            report.extend(
                [
                    "",
                    "FULL CURRENT FILE",
                    "-" * 120,
                    text.rstrip(),
                    "",
                ]
            )

        report.extend(
            [
                "=" * 120,
                "RESULT",
                "=" * 120,
                "",
                "PASSED",
                "",
                (
                    "No source file was modified. "
                    "This is a read-only connection/config snapshot."
                ),
            ]
        )

        output.write_text(
            "\n".join(
                report
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print(
            "=" * 72
        )
        print(
            "MULTI-CLUSTER CONNECTION CONFIG SNAPSHOT V1 PASSED"
        )
        print(
            "=" * 72
        )
        print()
        print(
            f"Captured files: {len(selected)}"
        )
        print()
        print(
            "No source file was modified."
        )
        print()
        print(
            "Upload only:"
        )
        print(
            output
        )

        return 0

    except Exception as exc:
        error.write_text(
            "\n".join(
                [
                    (
                        "Multi-Cluster Connection Config "
                        "Current Code Snapshot v1 FAILED"
                    ),
                    (
                        "GeneratedAt: "
                        + datetime.now().astimezone().isoformat()
                    ),
                    "",
                    (
                        f"{type(exc).__name__}: {exc}"
                    ),
                    "",
                    traceback.format_exc(),
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print(
            "=" * 72
        )
        print(
            "MULTI-CLUSTER CONNECTION CONFIG SNAPSHOT V1 FAILED"
        )
        print(
            "=" * 72
        )
        print()
        print(
            "Upload only:"
        )
        print(
            error
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
