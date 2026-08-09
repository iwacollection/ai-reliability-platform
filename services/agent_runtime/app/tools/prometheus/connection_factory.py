from __future__ import annotations

from collections.abc import (
    Callable,
    Mapping,
)
from os import environ
from pathlib import Path
from typing import Any

from common.config import (
    get_settings,
)
from common.config.settings import (
    PrometheusReadEndpointConfig,
    PrometheusReadMultiClusterConfig,
)

from services.agent_runtime.app.tools.prometheus.router import (
    PrometheusClusterRegistry,
    PrometheusClusterRoutingError,
)
from services.agent_runtime.app.tools.prometheus.tool import (
    PrometheusConfigurationError,
    PrometheusTool,
)


class PrometheusReadConnectionFactoryConfigurationError(
    RuntimeError
):
    """
    Read-only multi-cluster Prometheus connections cannot be assembled safely.
    """


_MAX_TOKEN_FILE_BYTES = (
    16
    * 1024
)


def _resolve_config(
    config: (
        PrometheusReadMultiClusterConfig
        | None
    ),
) -> PrometheusReadMultiClusterConfig:
    resolved = (
        get_settings()
        .connections
        .prometheus_read
        if config is None
        else config
    )

    if not isinstance(
        resolved,
        PrometheusReadMultiClusterConfig,
    ):
        raise PrometheusReadConnectionFactoryConfigurationError(
            "Prometheus read connection factory requires validated configuration"
        )

    return resolved


def _resolve_environment(
    environment: (
        Mapping[
            str,
            str,
        ]
        | None
    ),
) -> Mapping[
    str,
    str,
]:
    resolved = (
        environ
        if environment is None
        else environment
    )

    if not isinstance(
        resolved,
        Mapping,
    ):
        raise PrometheusReadConnectionFactoryConfigurationError(
            "Prometheus read credential source must be a mapping"
        )

    return resolved


def _default_token_file_reader(
    path: str,
) -> str:
    token_path = Path(
        path
    )

    try:
        if not token_path.is_file():
            raise PrometheusReadConnectionFactoryConfigurationError(
                "Prometheus read token file is unavailable"
            )

        if (
            token_path.stat().st_size
            > _MAX_TOKEN_FILE_BYTES
        ):
            raise PrometheusReadConnectionFactoryConfigurationError(
                "Prometheus read token file is too large"
            )

        return token_path.read_text(
            encoding="utf-8"
        )

    except PrometheusReadConnectionFactoryConfigurationError:
        raise

    except OSError:
        raise PrometheusReadConnectionFactoryConfigurationError(
            "Prometheus read token file is unavailable"
        ) from None


def _validate_token(
    value: Any,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise PrometheusReadConnectionFactoryConfigurationError(
            "Prometheus read credential is invalid"
        )

    normalized = value.rstrip(
        "\r\n"
    )

    if (
        not normalized
        or normalized
        != normalized.strip()
        or len(
            normalized
        )
        < 16
        or len(
            normalized.encode(
                "utf-8"
            )
        )
        > _MAX_TOKEN_FILE_BYTES
        or "\x00"
        in normalized
    ):
        raise PrometheusReadConnectionFactoryConfigurationError(
            "Prometheus read credential is invalid"
        )

    return normalized


def _load_bearer_token(
    config: PrometheusReadEndpointConfig,
    *,
    environment: (
        Mapping[
            str,
            str,
        ]
        | None
    ),
    token_file_reader: (
        Callable[
            [
                str,
            ],
            str,
        ]
        | None
    ),
) -> str:
    if config.authentication == "none":
        return ""

    if (
        config.bearer_token_env
        is not None
    ):
        source = _resolve_environment(
            environment
        )

        value = source.get(
            config.bearer_token_env
        )

        if value is None:
            raise PrometheusReadConnectionFactoryConfigurationError(
                "Prometheus read credential environment variable is missing: "
                + config.bearer_token_env
            )

        return _validate_token(
            value
        )

    if (
        config.bearer_token_file
        is not None
    ):
        reader = (
            token_file_reader
            or _default_token_file_reader
        )

        try:
            value = reader(
                config.bearer_token_file
            )

        except PrometheusReadConnectionFactoryConfigurationError:
            raise

        except Exception:
            raise PrometheusReadConnectionFactoryConfigurationError(
                "Prometheus read token file is unavailable"
            ) from None

        return _validate_token(
            value
        )

    raise PrometheusReadConnectionFactoryConfigurationError(
        "Prometheus read bearer authentication has no configured credential source"
    )


def _validate_ca_file(
    path: str | None,
) -> str | None:
    if path is None:
        return None

    try:
        if not Path(
            path
        ).is_file():
            raise PrometheusReadConnectionFactoryConfigurationError(
                "Prometheus read CA file is unavailable"
            )

    except OSError:
        raise PrometheusReadConnectionFactoryConfigurationError(
            "Prometheus read CA file is unavailable"
        ) from None

    return path


def create_prometheus_cluster_registry(
    config: (
        PrometheusReadMultiClusterConfig
        | None
    ) = None,
    *,
    environment: (
        Mapping[
            str,
            str,
        ]
        | None
    ) = None,
    token_file_reader: (
        Callable[
            [
                str,
            ],
            str,
        ]
        | None
    ) = None,
) -> PrometheusClusterRegistry | None:
    """
    Build read-only multi-cluster metrics routing from validated non-secret config.

    Disabled configuration returns None before reading environment variables,
    token files, or CA files.

    Enabled configuration resolves endpoint-local credential references,
    constructs hardened PrometheusTool objects, and binds each Incident cluster
    to an explicit endpoint through PrometheusClusterRegistry. Several clusters
    may intentionally share one endpoint object. No HTTP request is made here.
    """

    resolved = _resolve_config(
        config
    )

    if not resolved.enabled:
        return None

    endpoint_tools: dict[
        str,
        PrometheusTool,
    ] = {}

    try:
        for endpoint in resolved.endpoints:
            token = _load_bearer_token(
                endpoint,
                environment=environment,
                token_file_reader=(
                    token_file_reader
                ),
            )

            ca_file = _validate_ca_file(
                endpoint.ca_file
            )

            tool = PrometheusTool(
                base_url=endpoint.base_url,
                timeout_seconds=(
                    endpoint.request_timeout_seconds
                ),
                verify_tls=True,
                bearer_token=token,
                allow_mock_fallback=False,
                ca_file=ca_file,
            )

            if (
                tool.base_url
                != endpoint.base_url
                or tool.verify_tls
                is not True
                or tool.allow_mock_fallback
                is not False
                or tool.ca_file
                != ca_file
            ):
                raise PrometheusReadConnectionFactoryConfigurationError(
                    "Prometheus read Tool did not retain the validated connection boundary"
                )

            endpoint_tools[
                endpoint.endpoint_name
            ] = tool

        bindings = {
            binding.cluster_name: endpoint_tools[
                binding.endpoint_name
            ]
            for binding in resolved.cluster_bindings
        }

        registry = (
            PrometheusClusterRegistry(
                bindings
            )
        )

    except PrometheusReadConnectionFactoryConfigurationError:
        raise

    except (
        PrometheusClusterRoutingError,
        PrometheusConfigurationError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise PrometheusReadConnectionFactoryConfigurationError(
            "Prometheus read cluster registry configuration is invalid"
        ) from None

    if (
        registry.count
        != len(
            resolved.cluster_bindings
        )
    ):
        raise PrometheusReadConnectionFactoryConfigurationError(
            "Prometheus read cluster registry lost configured bindings"
        )

    return registry


__all__ = [
    "PrometheusReadConnectionFactoryConfigurationError",
    "create_prometheus_cluster_registry",
]
