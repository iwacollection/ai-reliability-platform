import os
import ssl
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from services.agent_runtime.app.tools.base import (
    BaseTool,
)


class PrometheusToolError(RuntimeError):
    """
    Base error raised by PrometheusTool.
    """


class PrometheusConfigurationError(
    PrometheusToolError
):
    """
    PrometheusTool configuration is invalid or unavailable.
    """


class PrometheusQueryError(
    PrometheusToolError
):
    """
    Prometheus rejected or failed an instant query.
    """


class PrometheusTool(BaseTool):
    """
    Read-only Prometheus instant-query tool.

    Live mode is enabled when PROMETHEUS_URL or base_url is set.

    A temporary mock fallback is retained for compatibility with the
    existing runtime test. Its response is explicitly marked as mock and
    production_signal=False, so VerificationEvidenceCollector rejects it.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        verify_tls: bool | None = None,
        bearer_token: str | None = None,
        allow_mock_fallback: bool | None = None,
        client: httpx.AsyncClient | None = None,
        ca_file: str | None = None,
    ) -> None:
        configured_url = (
            base_url
            if base_url is not None
            else os.getenv("PROMETHEUS_URL")
        )

        self.base_url = (
            configured_url.rstrip("/")
            if configured_url
            else None
        )

        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else self._read_positive_float(
                "PROMETHEUS_TIMEOUT_SECONDS",
                default=5.0,
            )
        )

        self.verify_tls = (
            verify_tls
            if verify_tls is not None
            else self._read_bool(
                "PROMETHEUS_VERIFY_TLS",
                default=True,
            )
        )

        self.bearer_token = (
            bearer_token
            if bearer_token is not None
            else os.getenv(
                "PROMETHEUS_BEARER_TOKEN"
            )
        )

        self.allow_mock_fallback = (
            allow_mock_fallback
            if allow_mock_fallback is not None
            else self._read_bool(
                "PROMETHEUS_ALLOW_MOCK_FALLBACK",
                default=True,
            )
        )

        self.client = client
        self.ca_file = ca_file

        if (
            self.ca_file is not None
            and not self.verify_tls
        ):
            raise PrometheusConfigurationError(
                "Prometheus CA file requires TLS verification"
            )

        if self.timeout_seconds <= 0:
            raise PrometheusConfigurationError(
                "Prometheus timeout must be positive"
            )

    @property
    def name(self) -> str:
        return "prometheus"

    async def execute(
        self,
        query: str,
        time: datetime | str | float | int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        normalized_query = query.strip()

        if not normalized_query:
            raise PrometheusQueryError(
                "Prometheus query cannot be empty"
            )

        if self.base_url is None:
            if not self.allow_mock_fallback:
                raise PrometheusConfigurationError(
                    "PROMETHEUS_URL is not configured"
                )

            return self._mock_response(
                normalized_query
            )

        params: dict[str, Any] = {
            "query": normalized_query
        }

        if time is not None:
            params["time"] = self._normalize_time(
                time
            )

        payload = await self._query(
            params=params
        )

        data = payload.get("data")

        if not isinstance(data, Mapping):
            raise PrometheusQueryError(
                "Prometheus response data is invalid"
            )

        result_type = data.get(
            "resultType"
        )

        if result_type not in {
            "vector",
            "matrix",
            "scalar",
            "string",
        }:
            raise PrometheusQueryError(
                "Prometheus resultType is invalid"
            )

        result = data.get("result")
        observed_at = self._extract_observed_at(
            result_type=result_type,
            result=result,
        )
        has_samples = observed_at is not None

        return {
            "success": True,
            "source": "prometheus",
            "mode": "read_only",
            "production_signal": has_samples,
            "observed_at": (
                observed_at.isoformat()
                if observed_at
                else datetime.now(UTC).isoformat()
            ),
            "query": normalized_query,
            "data": dict(data),
            "warnings": list(
                payload.get("warnings") or []
            ),
        }

    async def _query(
        self,
        params: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            if self.client is not None:
                response = await self.client.get(
                    self._query_url,
                    params=dict(params),
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self.timeout_seconds,
                    verify=self._tls_verify_value,
                    headers=self._headers,
                ) as client:
                    response = await client.get(
                        self._query_url,
                        params=dict(params),
                    )

            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise PrometheusQueryError(
                "Prometheus query timed out"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise PrometheusQueryError(
                "Prometheus returned HTTP "
                f"{exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise PrometheusQueryError(
                "Prometheus request failed"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise PrometheusQueryError(
                "Prometheus returned invalid JSON"
            ) from exc

        if not isinstance(payload, dict):
            raise PrometheusQueryError(
                "Prometheus response is not an object"
            )

        if payload.get("status") != "success":
            error_type = payload.get(
                "errorType",
                "unknown",
            )
            error_message = payload.get(
                "error",
                "query failed",
            )
            raise PrometheusQueryError(
                "Prometheus API error "
                f"[{error_type}]: {error_message}"
            )

        return payload

    @property
    def _tls_verify_value(
        self,
    ) -> bool | ssl.SSLContext:
        if self.ca_file is None:
            return self.verify_tls

        try:
            return ssl.create_default_context(
                cafile=self.ca_file
            )

        except (
            OSError,
            ssl.SSLError,
        ) as exc:
            raise PrometheusConfigurationError(
                "Prometheus CA file is invalid"
            ) from exc

    @property
    def _query_url(self) -> str:
        if self.base_url is None:
            raise PrometheusConfigurationError(
                "PROMETHEUS_URL is not configured"
            )

        return (
            f"{self.base_url}/api/v1/query"
        )

    @property
    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json"
        }

        if self.bearer_token:
            headers["Authorization"] = (
                f"Bearer {self.bearer_token}"
            )

        return headers

    @staticmethod
    def _mock_response(
        query: str,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "query": query,
            "metrics": {
                "cpu_usage": 92,
                "memory_usage": 65,
                "requests_per_second": 1200,
            },
            "source": "mock_prometheus",
            "mode": "mock",
            "production_signal": False,
            "observed_at": (
                datetime.now(UTC).isoformat()
            ),
        }

    @classmethod
    def _extract_observed_at(
        cls,
        result_type: str,
        result: Any,
    ) -> datetime | None:
        timestamps: list[float] = []

        if result_type in {
            "scalar",
            "string",
        }:
            timestamp = cls._sample_timestamp(
                result
            )
            if timestamp is not None:
                timestamps.append(timestamp)

        elif result_type == "vector":
            if not isinstance(result, list):
                return None

            for item in result:
                if not isinstance(item, Mapping):
                    continue

                timestamp = cls._sample_timestamp(
                    item.get("value")
                )
                if timestamp is not None:
                    timestamps.append(timestamp)

        elif result_type == "matrix":
            if not isinstance(result, list):
                return None

            for item in result:
                if not isinstance(item, Mapping):
                    continue

                values = item.get("values")
                if not isinstance(values, list) or not values:
                    continue

                timestamp = cls._sample_timestamp(
                    values[-1]
                )
                if timestamp is not None:
                    timestamps.append(timestamp)

        if not timestamps:
            return None

        return datetime.fromtimestamp(
            min(timestamps),
            tz=UTC,
        )

    @staticmethod
    def _sample_timestamp(
        sample: Any,
    ) -> float | None:
        if (
            not isinstance(sample, list)
            or len(sample) < 2
        ):
            return None

        try:
            return float(sample[0])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_time(
        value: datetime | str | float | int,
    ) -> str | float | int:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise PrometheusQueryError(
                    "Prometheus query time must be "
                    "timezone-aware"
                )

            return value.astimezone(
                UTC
            ).isoformat()

        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise PrometheusQueryError(
                    "Prometheus query time cannot be empty"
                )
            return normalized

        return value

    @staticmethod
    def _read_bool(
        name: str,
        default: bool,
    ) -> bool:
        raw = os.getenv(name)

        if raw is None:
            return default

        normalized = raw.strip().lower()

        if normalized in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return True

        if normalized in {
            "0",
            "false",
            "no",
            "off",
        }:
            return False

        raise PrometheusConfigurationError(
            f"{name} must be a boolean"
        )

    @staticmethod
    def _read_positive_float(
        name: str,
        default: float,
    ) -> float:
        raw = os.getenv(name)

        if raw is None:
            return default

        try:
            value = float(raw)
        except ValueError as exc:
            raise PrometheusConfigurationError(
                f"{name} must be a number"
            ) from exc

        if value <= 0:
            raise PrometheusConfigurationError(
                f"{name} must be positive"
            )

        return value
