from __future__ import annotations

import json
import os
import shutil
import subprocess
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


AFTER_NAME = "production_access_discovery_v1_after.txt"
ERROR_NAME = "production_access_discovery_v1_error.txt"


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate

    raise RuntimeError(
        "Repository root not found. "
        "Run from inside ai-reliability-platform."
    )


def write_text(path: Path, text: str) -> None:
    path.write_text(
        text.replace("\r\n", "\n").replace("\r", "\n"),
        encoding="utf-8",
        newline="\n",
    )


def run(
    command: list[str],
    *,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def section(lines: list[str], title: str) -> None:
    lines.extend(
        [
            "",
            "=" * 120,
            title,
            "=" * 120,
            "",
        ]
    )


def safe_bool(value: Any) -> bool:
    return bool(value)


def inspect_kubeconfig(root: Path) -> dict[str, Any]:
    kubectl = shutil.which("kubectl")

    result: dict[str, Any] = {
        "kubectl_present": bool(kubectl),
        "current_context": None,
        "cluster_server_present": False,
        "cluster_server": None,
        "namespace": None,
        "ca_file_present": False,
        "ca_data_present": False,
        "auth": {
            "token_present": False,
            "token_file_present": False,
            "client_certificate_present": False,
            "client_certificate_data_present": False,
            "client_key_present": False,
            "client_key_data_present": False,
            "exec_auth_present": False,
            "auth_provider_present": False,
            "username_present": False,
            "password_present": False,
        },
        "tool_contract_compatibility": "unknown",
        "notes": [],
    }

    if not kubectl:
        result["notes"].append(
            "kubectl is not available on PATH."
        )
        return result

    version = run(
        [
            kubectl,
            "version",
            "--client",
            "-o",
            "json",
        ],
        cwd=root,
    )

    result["kubectl_client_version_ok"] = (
        version.returncode == 0
    )

    current = run(
        [
            kubectl,
            "config",
            "current-context",
        ],
        cwd=root,
    )

    if current.returncode == 0:
        result["current_context"] = (
            current.stdout.strip()
            or None
        )

    # Deliberately DO NOT use --raw.
    # kubectl config view masks credential material unless --raw is requested.
    view = run(
        [
            kubectl,
            "config",
            "view",
            "--minify",
            "-o",
            "json",
        ],
        cwd=root,
    )

    if view.returncode != 0:
        result["notes"].append(
            "kubectl config view --minify failed."
        )
        result["kubeconfig_view_error_type"] = (
            "kubectl_exit_nonzero"
        )
        return result

    try:
        payload = json.loads(
            view.stdout
        )
    except json.JSONDecodeError:
        result["notes"].append(
            "kubectl config view returned invalid JSON."
        )
        return result

    clusters = payload.get(
        "clusters",
        [],
    )

    contexts = payload.get(
        "contexts",
        [],
    )

    users = payload.get(
        "users",
        [],
    )

    if clusters:
        cluster = (
            clusters[0].get(
                "cluster",
                {},
            )
            or {}
        )

        server = cluster.get(
            "server"
        )

        result[
            "cluster_server_present"
        ] = bool(
            server
        )

        result[
            "cluster_server"
        ] = (
            server
            if isinstance(
                server,
                str,
            )
            else None
        )

        result[
            "ca_file_present"
        ] = bool(
            cluster.get(
                "certificate-authority"
            )
        )

        result[
            "ca_data_present"
        ] = bool(
            cluster.get(
                "certificate-authority-data"
            )
        )

        result[
            "insecure_skip_tls_verify"
        ] = bool(
            cluster.get(
                "insecure-skip-tls-verify",
                False,
            )
        )

    if contexts:
        context = (
            contexts[0].get(
                "context",
                {},
            )
            or {}
        )

        result["namespace"] = (
            context.get(
                "namespace"
            )
            or "default"
        )

    if users:
        user = (
            users[0].get(
                "user",
                {},
            )
            or {}
        )

        auth = result[
            "auth"
        ]

        auth[
            "token_present"
        ] = bool(
            user.get(
                "token"
            )
        )

        auth[
            "token_file_present"
        ] = bool(
            user.get(
                "tokenFile"
            )
            or user.get(
                "token-file"
            )
        )

        auth[
            "client_certificate_present"
        ] = bool(
            user.get(
                "client-certificate"
            )
        )

        auth[
            "client_certificate_data_present"
        ] = bool(
            user.get(
                "client-certificate-data"
            )
        )

        auth[
            "client_key_present"
        ] = bool(
            user.get(
                "client-key"
            )
        )

        auth[
            "client_key_data_present"
        ] = bool(
            user.get(
                "client-key-data"
            )
        )

        auth[
            "exec_auth_present"
        ] = bool(
            user.get(
                "exec"
            )
        )

        auth[
            "auth_provider_present"
        ] = bool(
            user.get(
                "auth-provider"
            )
        )

        auth[
            "username_present"
        ] = bool(
            user.get(
                "username"
            )
        )

        auth[
            "password_present"
        ] = bool(
            user.get(
                "password"
            )
        )

    auth = result[
        "auth"
    ]

    if (
        result[
            "cluster_server_present"
        ]
        and (
            auth[
                "token_present"
            ]
            or auth[
                "token_file_present"
            ]
        )
    ):
        result[
            "tool_contract_compatibility"
        ] = (
            "compatible_with_current_kubernetes_tool"
        )

        result[
            "notes"
        ].append(
            "Current kubeconfig appears to use bearer token/token-file authentication."
        )

    elif (
        result[
            "cluster_server_present"
        ]
        and (
            auth[
                "exec_auth_present"
            ]
            or auth[
                "client_certificate_present"
            ]
            or auth[
                "client_certificate_data_present"
            ]
            or auth[
                "auth_provider_present"
            ]
        )
    ):
        result[
            "tool_contract_compatibility"
        ] = (
            "kubeconfig_auth_not_directly_expressible_by_current_env_contract"
        )

        result[
            "notes"
        ].append(
            "Current kubeconfig uses exec/client-certificate/auth-provider style authentication."
        )

        result[
            "notes"
        ].append(
            "Do not extract or copy secrets manually. Prefer a dedicated read-only ServiceAccount or a future kubeconfig-aware transport."
        )

    else:
        result[
            "tool_contract_compatibility"
        ] = (
            "insufficient_local_kubeconfig_information"
        )

    return result


def inspect_prometheus_environment() -> dict[str, Any]:
    return {
        "prometheus_url_present": bool(
            os.getenv(
                "PROMETHEUS_URL",
                "",
            ).strip()
        ),
        "prometheus_bearer_token_present": bool(
            os.getenv(
                "PROMETHEUS_BEARER_TOKEN",
                "",
            ).strip()
        ),
        "prometheus_allow_mock_fallback": (
            os.getenv(
                "PROMETHEUS_ALLOW_MOCK_FALLBACK"
            )
        ),
        "notes": [
            (
                "No network discovery is attempted for Prometheus. "
                "Its URL must come from your deployment/monitoring configuration."
            )
        ],
    }


def redact_kubernetes_server(
    server: str | None,
) -> str | None:
    # API server URLs are not credentials. Preserve them so the user can
    # recognize which cluster/context is being inspected.
    return server


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after = root / AFTER_NAME
    error = root / ERROR_NAME

    for path in (
        after,
        error,
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    report = [
        "Production Access Discovery v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Purpose:",
        "- inspect local Kubernetes access configuration",
        "- identify kubeconfig authentication style",
        "- compare it with the current KubernetesTool environment contract",
        "- inspect Prometheus environment presence",
        "- perform ZERO Kubernetes/Prometheus/LLM network requests",
        "",
        "Safety:",
        "- kubectl config view is called WITHOUT --raw",
        "- credential values are never printed",
        "- no environment variable is changed",
        "- Recorder remains untouched",
    ]

    try:
        section(
            report,
            "KUBERNETES LOCAL ACCESS DISCOVERY",
        )

        kubernetes = inspect_kubeconfig(
            root
        )

        kubernetes[
            "cluster_server"
        ] = redact_kubernetes_server(
            kubernetes.get(
                "cluster_server"
            )
        )

        report.append(
            json.dumps(
                kubernetes,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )

        section(
            report,
            "PROMETHEUS LOCAL CONFIG DISCOVERY",
        )

        prometheus = (
            inspect_prometheus_environment()
        )

        report.append(
            json.dumps(
                prometheus,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )

        section(
            report,
            "NEXT-STEP CLASSIFICATION",
        )

        compatibility = kubernetes.get(
            "tool_contract_compatibility"
        )

        if compatibility == (
            "compatible_with_current_kubernetes_tool"
        ):
            kubernetes_next = (
                "Kubernetes: local kubeconfig authentication shape is compatible "
                "with the current Tool contract. Next step can map the existing "
                "endpoint/auth into a bounded live-read smoke configuration."
            )

        elif compatibility == (
            "kubeconfig_auth_not_directly_expressible_by_current_env_contract"
        ):
            kubernetes_next = (
                "Kubernetes: current kubeconfig uses an auth method the present "
                "Tool env contract does not directly model. Prefer creating a "
                "least-privilege read-only ServiceAccount, or add a kubeconfig-aware "
                "transport before any production capture."
            )

        else:
            kubernetes_next = (
                "Kubernetes: no locally usable access contract was discovered. "
                "Production endpoint/auth still needs to be provided."
            )

        if prometheus[
            "prometheus_url_present"
        ]:
            prometheus_next = (
                "Prometheus: URL is already present. Next step can validate "
                "fallback/TLS/auth and then perform a bounded query smoke."
            )
        else:
            prometheus_next = (
                "Prometheus: PROMETHEUS_URL is still missing. Obtain the read-only "
                "Prometheus endpoint from the actual monitoring deployment."
            )

        report.extend(
            [
                kubernetes_next,
                "",
                prometheus_next,
                "",
                "Recorder: keep disabled until both production read paths are ready.",
            ]
        )

        section(
            report,
            "RESULT",
        )

        report.append(
            "DISCOVERY=COMPLETED"
        )

        write_text(
            after,
            "\n".join(
                report
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "PRODUCTION ACCESS DISCOVERY COMPLETED"
        )
        print("=" * 72)
        print("")
        print(
            "No production network request was sent."
        )
        print("")
        print("Upload:")
        print(after)

        return 0

    except Exception as exc:
        error_lines = [
            "Production Access Discovery v1 FAILED",
            f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
            "",
            "Exception:",
            f"{type(exc).__name__}: {exc}",
            "",
            "Traceback:",
            traceback.format_exc(),
            "",
            "PARTIAL REPORT",
            "=" * 120,
            *report,
        ]

        write_text(
            error,
            "\n".join(
                error_lines
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "PRODUCTION ACCESS DISCOVERY FAILED"
        )
        print("=" * 72)
        print("")
        print("Upload:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
