import hmac
import json

from collections.abc import Callable, Mapping
from hashlib import sha256
from os import environ
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from common.config import get_settings
from common.config.settings import (
    KubernetesPreflightConfig,
    KubernetesProductionExecutionConfig,
)
from services.agent_runtime.app.action.preflight_artifact_models import (
    PreflightArtifactRecord,
    PreflightArtifactStatus,
)


PRODUCTION_PILOT_LIVE_PROBE_GATE_ACKNOWLEDGEMENT = (
    "I_ENABLE_READ_ONLY_OOM_PILOT_LIVE_PROBE_V1"
)

_MAX_TOKEN_BYTES = 16 * 1024
_MAX_RESPONSE_BYTES = 1024 * 1024
_PROBE_ENABLED_ENV = "KUBERNETES_PRODUCTION_LIVE_PROBE_ENABLED"
_PROBE_ACK_ENV = "KUBERNETES_PRODUCTION_LIVE_PROBE_ACKNOWLEDGEMENT"


class ProductionPilotLiveProbeError(RuntimeError):
    """Sanitized fail-closed live probe failure."""

    def __init__(
        self,
        message: str,
        *,
        blocker_code: str,
        calls_made: int,
    ) -> None:
        super().__init__(message)
        self.blocker_code = blocker_code
        self.calls_made = calls_made


class ProductionPilotLiveProbeFactoryError(RuntimeError):
    """The separately gated read-only probe could not be built safely."""


class ProductionPilotLiveProbeResult:
    """Internal successful result containing no credential material."""

    __slots__ = (
        "live_resource_sha256",
        "network_call_count",
    )

    def __init__(
        self,
        *,
        live_resource_sha256: str,
        network_call_count: int,
    ) -> None:
        self.live_resource_sha256 = live_resource_sha256
        self.network_call_count = network_call_count


class _BearerCredential:
    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        self.__value = value

    def authorization_header(self) -> str:
        return f"Bearer {self.__value}"

    def __repr__(self) -> str:
        return "<_BearerCredential redacted>"


class ProductionPilotLiveReadinessProbe:
    """
    Perform exactly two GET requests using separate identities.

    This component has no PATCH method, no Action Runtime dependency, no
    budget dependency and no Verification dependency. Redirects are disabled.
    """

    def __init__(
        self,
        *,
        api_url: str,
        cluster_name: str,
        namespace: str,
        deployment: str,
        container: str,
        preflight_token: str,
        production_token: str,
        verify_tls: bool | str = True,
        request_timeout_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
        injected_client_tls_verified: bool = False,
    ) -> None:
        normalized_url = _clean_https_origin(api_url)
        for label, value in (
            ("cluster", cluster_name),
            ("namespace", namespace),
            ("deployment", deployment),
            ("container", container),
        ):
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or len(value) > 253
            ):
                raise ProductionPilotLiveProbeFactoryError(
                    f"Production Pilot live probe {label} is invalid"
                )
        if verify_tls is not True and not (
            isinstance(verify_tls, str)
            and bool(verify_tls.strip())
        ):
            raise ProductionPilotLiveProbeFactoryError(
                "Production Pilot live probe requires TLS verification"
            )
        if (
            not isinstance(request_timeout_seconds, (int, float))
            or isinstance(request_timeout_seconds, bool)
            or request_timeout_seconds <= 0
            or request_timeout_seconds > 30
        ):
            raise ProductionPilotLiveProbeFactoryError(
                "Production Pilot live probe timeout is invalid"
            )
        if client is not None and injected_client_tls_verified is not True:
            raise ProductionPilotLiveProbeFactoryError(
                "Injected live probe client requires explicit TLS assurance"
            )
        checked_preflight = _validate_token(preflight_token)
        checked_production = _validate_token(production_token)
        if hmac.compare_digest(
            checked_preflight.encode("utf-8"),
            checked_production.encode("utf-8"),
        ):
            raise ProductionPilotLiveProbeFactoryError(
                "Production and Preflight live probe credentials must differ"
            )

        self.api_url = normalized_url
        self.cluster_name = cluster_name
        self.namespace = namespace
        self.deployment = deployment
        self.container = container
        self.verify_tls = verify_tls
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.client = client
        self._preflight_credential = _BearerCredential(
            checked_preflight
        )
        self._production_credential = _BearerCredential(
            checked_production
        )

    async def probe(
        self,
        record: PreflightArtifactRecord,
    ) -> ProductionPilotLiveProbeResult:
        if not isinstance(record, PreflightArtifactRecord):
            raise TypeError(
                "Production Pilot live probe artifact is invalid"
            )
        if record.status != PreflightArtifactStatus.APPROVAL_BOUND:
            raise ProductionPilotLiveProbeError(
                "Production Pilot live probe artifact is not Approval-bound",
                blocker_code="artifact_not_approval_bound",
                calls_made=0,
            )
        self._require_contract_binding(record)
        path = (
            "/apis/apps/v1/namespaces/"
            + quote(self.namespace, safe="")
            + "/deployments/"
            + quote(self.deployment, safe="")
        )

        if self.client is not None:
            first = await self._get(
                self.client,
                path,
                self._preflight_credential,
                calls_made=1,
            )
            second = await self._get(
                self.client,
                path,
                self._production_credential,
                calls_made=2,
            )
        else:
            async with httpx.AsyncClient(
                timeout=self.request_timeout_seconds,
                verify=self.verify_tls,
                follow_redirects=False,
            ) as client:
                first = await self._get(
                    client,
                    path,
                    self._preflight_credential,
                    calls_made=1,
                )
                second = await self._get(
                    client,
                    path,
                    self._production_credential,
                    calls_made=2,
                )

        first_state = self._validate_deployment(first, record)
        second_state = self._validate_deployment(second, record)
        if first_state != second_state:
            raise ProductionPilotLiveProbeError(
                "Kubernetes live target changed between credential probes",
                blocker_code="live_target_changed_between_probes",
                calls_made=2,
            )
        return ProductionPilotLiveProbeResult(
            live_resource_sha256=_digest_mapping(first_state),
            network_call_count=2,
        )

    async def _get(
        self,
        client: httpx.AsyncClient,
        path: str,
        credential: _BearerCredential,
        *,
        calls_made: int,
    ) -> dict[str, Any]:
        try:
            response = await client.request(
                "GET",
                f"{self.api_url}{path}",
                headers={
                    "Accept": "application/json",
                    "Authorization": credential.authorization_header(),
                },
                timeout=self.request_timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise ProductionPilotLiveProbeError(
                "Kubernetes live readiness request failed",
                blocker_code="kubernetes_live_probe_transport_failed",
                calls_made=calls_made,
            ) from exc
        except Exception as exc:
            raise ProductionPilotLiveProbeError(
                "Kubernetes live readiness client failed safely",
                blocker_code="kubernetes_live_probe_client_failed",
                calls_made=calls_made,
            ) from exc

        status = response.status_code
        if status in {401, 403}:
            raise ProductionPilotLiveProbeError(
                "Kubernetes live readiness authorization failed",
                blocker_code="kubernetes_live_probe_unauthorized",
                calls_made=calls_made,
            )
        if status == 404:
            raise ProductionPilotLiveProbeError(
                "Kubernetes live readiness target was not found",
                blocker_code="kubernetes_live_target_not_found",
                calls_made=calls_made,
            )
        if status < 200 or status >= 300:
            raise ProductionPilotLiveProbeError(
                "Kubernetes live readiness returned an unsafe status",
                blocker_code="kubernetes_live_probe_http_error",
                calls_made=calls_made,
            )
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > _MAX_RESPONSE_BYTES:
                    raise ProductionPilotLiveProbeError(
                        "Kubernetes live readiness response is too large",
                        blocker_code="kubernetes_live_probe_response_too_large",
                        calls_made=calls_made,
                    )
            except ValueError as exc:
                raise ProductionPilotLiveProbeError(
                    "Kubernetes live readiness response length is invalid",
                    blocker_code="kubernetes_live_probe_response_invalid",
                    calls_made=calls_made,
                ) from exc
        raw = getattr(response, "content", b"")
        if isinstance(raw, bytes) and len(raw) > _MAX_RESPONSE_BYTES:
            raise ProductionPilotLiveProbeError(
                "Kubernetes live readiness response is too large",
                blocker_code="kubernetes_live_probe_response_too_large",
                calls_made=calls_made,
            )
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise ProductionPilotLiveProbeError(
                "Kubernetes live readiness response is invalid",
                blocker_code="kubernetes_live_probe_response_invalid",
                calls_made=calls_made,
            ) from exc
        if not isinstance(payload, dict):
            raise ProductionPilotLiveProbeError(
                "Kubernetes live readiness response is not an object",
                blocker_code="kubernetes_live_probe_response_invalid",
                calls_made=calls_made,
            )
        return payload

    def _require_contract_binding(
        self,
        record: PreflightArtifactRecord,
    ) -> None:
        contract = record.artifact.contract
        scope = contract.scope
        if (
            scope.cluster != self.cluster_name
            or scope.namespace != self.namespace
            or scope.name != self.deployment
            or scope.container != self.container
        ):
            raise ProductionPilotLiveProbeError(
                "Production Pilot live probe scope changed",
                blocker_code="live_probe_scope_mismatch",
                calls_made=0,
            )

    def _validate_deployment(
        self,
        payload: Mapping[str, Any],
        record: PreflightArtifactRecord,
    ) -> dict[str, Any]:
        contract = record.artifact.contract
        metadata = payload.get("metadata")
        spec = payload.get("spec")
        if (
            payload.get("apiVersion") != "apps/v1"
            or payload.get("kind") != "Deployment"
            or not isinstance(metadata, Mapping)
            or not isinstance(spec, Mapping)
        ):
            raise ProductionPilotLiveProbeError(
                "Kubernetes live Deployment response is invalid",
                blocker_code="live_deployment_response_invalid",
                calls_made=2,
            )
        required = {
            "namespace": self.namespace,
            "name": self.deployment,
            "uid": str(contract.precondition.workload_uid),
            "resourceVersion": contract.precondition.resource_version,
        }
        if any(metadata.get(key) != value for key, value in required.items()):
            raise ProductionPilotLiveProbeError(
                "Kubernetes live Deployment binding changed",
                blocker_code="live_deployment_binding_changed",
                calls_made=2,
            )
        if metadata.get("deletionTimestamp") is not None:
            raise ProductionPilotLiveProbeError(
                "Kubernetes live Deployment is being deleted",
                blocker_code="live_deployment_deleting",
                calls_made=2,
            )
        generation = metadata.get("generation")
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation != contract.precondition.generation
        ):
            raise ProductionPilotLiveProbeError(
                "Kubernetes live Deployment generation changed",
                blocker_code="live_deployment_generation_changed",
                calls_made=2,
            )
        template = spec.get("template")
        template_spec = (
            template.get("spec")
            if isinstance(template, Mapping)
            else None
        )
        containers = (
            template_spec.get("containers")
            if isinstance(template_spec, Mapping)
            else None
        )
        if not isinstance(containers, list):
            raise ProductionPilotLiveProbeError(
                "Kubernetes live Deployment containers are invalid",
                blocker_code="live_deployment_response_invalid",
                calls_made=2,
            )
        matches = [
            item
            for item in containers
            if isinstance(item, Mapping)
            and item.get("name") == self.container
        ]
        if len(matches) != 1:
            raise ProductionPilotLiveProbeError(
                "Kubernetes live Deployment container changed",
                blocker_code="live_deployment_container_changed",
                calls_made=2,
            )
        resources = matches[0].get("resources")
        limits = (
            resources.get("limits")
            if isinstance(resources, Mapping)
            else None
        )
        memory = limits.get("memory") if isinstance(limits, Mapping) else None
        if memory != contract.memory.current_limit:
            raise ProductionPilotLiveProbeError(
                "Kubernetes live Deployment memory changed",
                blocker_code="live_deployment_memory_changed",
                calls_made=2,
            )
        return {
            "api_version": "apps/v1",
            "kind": "Deployment",
            "cluster": self.cluster_name,
            "namespace": self.namespace,
            "name": self.deployment,
            "uid": required["uid"],
            "resource_version": required["resourceVersion"],
            "generation": generation,
            "container": self.container,
            "memory_limit": memory,
        }


def create_production_pilot_live_readiness_probe(
    preflight_config: KubernetesPreflightConfig | None = None,
    execution_config: KubernetesProductionExecutionConfig | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    preflight_token_file_reader: Callable[[str], str] | None = None,
    production_token_file_reader: Callable[[str], str] | None = None,
    client: httpx.AsyncClient | None = None,
    injected_client_tls_verified: bool = False,
) -> ProductionPilotLiveReadinessProbe | None:
    """Build the read-only probe only behind its own explicit startup gate."""

    source = environ if environment is None else environment
    if not isinstance(source, Mapping):
        raise ProductionPilotLiveProbeFactoryError(
            "Production Pilot live probe environment is invalid"
        )
    enabled = _parse_enabled(source.get(_PROBE_ENABLED_ENV))
    if not enabled:
        return None
    if source.get(_PROBE_ACK_ENV) != (
        PRODUCTION_PILOT_LIVE_PROBE_GATE_ACKNOWLEDGEMENT
    ):
        raise ProductionPilotLiveProbeFactoryError(
            "Production Pilot live probe gate acknowledgement is invalid"
        )

    if preflight_config is None or execution_config is None:
        settings = get_settings()
        if preflight_config is None:
            preflight_config = settings.remediation.kubernetes_preflight
        if execution_config is None:
            execution_config = (
                settings.remediation.kubernetes_production_execution
            )
    if not isinstance(preflight_config, KubernetesPreflightConfig):
        raise ProductionPilotLiveProbeFactoryError(
            "Production Pilot live probe Preflight configuration is invalid"
        )
    if not isinstance(
        execution_config,
        KubernetesProductionExecutionConfig,
    ):
        raise ProductionPilotLiveProbeFactoryError(
            "Production Pilot live probe Production configuration is invalid"
        )
    if execution_config.enabled:
        raise ProductionPilotLiveProbeFactoryError(
            "Production execution must remain disabled during live probe"
        )
    if (
        not preflight_config.enabled
        or preflight_config.api_url is None
        or preflight_config.cluster_name is None
        or len(preflight_config.allowed_targets) != 1
    ):
        raise ProductionPilotLiveProbeFactoryError(
            "Production Pilot live probe requires one Preflight target"
        )
    preflight_token = _load_token(
        environment_name=preflight_config.bearer_token_env,
        file_name=preflight_config.bearer_token_file,
        source=source,
        reader=preflight_token_file_reader,
        label="Preflight",
    )
    production_token = _load_token(
        environment_name=execution_config.bearer_token_env,
        file_name=execution_config.bearer_token_file,
        source=source,
        reader=production_token_file_reader,
        label="Production",
    )
    if hmac.compare_digest(
        preflight_token.encode("utf-8"),
        production_token.encode("utf-8"),
    ):
        raise ProductionPilotLiveProbeFactoryError(
            "Production and Preflight live probe credentials must differ"
        )
    verify_tls = _validate_ca_file(preflight_config.ca_file)
    target = preflight_config.allowed_targets[0]
    return ProductionPilotLiveReadinessProbe(
        api_url=preflight_config.api_url,
        cluster_name=preflight_config.cluster_name,
        namespace=target.namespace,
        deployment=target.deployment,
        container=target.container,
        preflight_token=preflight_token,
        production_token=production_token,
        verify_tls=verify_tls,
        request_timeout_seconds=min(
            preflight_config.request_timeout_seconds,
            execution_config.request_timeout_seconds,
        ),
        client=client,
        injected_client_tls_verified=(
            injected_client_tls_verified
        ),
    )


def _parse_enabled(value: str | None) -> bool:
    if value is None:
        return False
    if value == "true":
        return True
    if value == "false":
        return False
    raise ProductionPilotLiveProbeFactoryError(
        "Production Pilot live probe enabled flag is invalid"
    )


def _load_token(
    *,
    environment_name: str | None,
    file_name: str | None,
    source: Mapping[str, str],
    reader: Callable[[str], str] | None,
    label: str,
) -> str:
    if environment_name is not None and file_name is None:
        value = source.get(environment_name)
        if value is None:
            raise ProductionPilotLiveProbeFactoryError(
                f"Production Pilot {label} credential is unavailable"
            )
        return _validate_token(value)
    if file_name is not None and environment_name is None:
        try:
            value = (
                reader(file_name)
                if reader is not None
                else _read_token_file(file_name)
            )
            return _validate_token(value)
        except ProductionPilotLiveProbeFactoryError:
            raise
        except Exception:
            raise ProductionPilotLiveProbeFactoryError(
                f"Production Pilot {label} credential is unavailable"
            ) from None
    raise ProductionPilotLiveProbeFactoryError(
        f"Production Pilot {label} credential reference is invalid"
    )


def _read_token_file(path: str) -> str:
    token_path = Path(path)
    try:
        if (
            not token_path.is_file()
            or token_path.stat().st_size <= 0
            or token_path.stat().st_size > _MAX_TOKEN_BYTES
        ):
            raise ProductionPilotLiveProbeFactoryError(
                "Production Pilot token file is unavailable"
            )
        return token_path.read_text(encoding="utf-8")
    except ProductionPilotLiveProbeFactoryError:
        raise
    except OSError:
        raise ProductionPilotLiveProbeFactoryError(
            "Production Pilot token file is unavailable"
        ) from None


def _validate_token(value: Any) -> str:
    if not isinstance(value, str):
        raise ProductionPilotLiveProbeFactoryError(
            "Production Pilot credential is invalid"
        )
    normalized = value.rstrip("\r\n")
    if (
        not normalized
        or normalized != normalized.strip()
        or len(normalized) < 16
        or len(normalized.encode("utf-8")) > _MAX_TOKEN_BYTES
        or "\x00" in normalized
    ):
        raise ProductionPilotLiveProbeFactoryError(
            "Production Pilot credential is invalid"
        )
    return normalized


def _validate_ca_file(path: str | None) -> bool | str:
    if path is None:
        return True
    try:
        ca_path = Path(path)
        if (
            not ca_path.is_file()
            or ca_path.stat().st_size <= 0
            or ca_path.stat().st_size > _MAX_RESPONSE_BYTES
        ):
            raise ProductionPilotLiveProbeFactoryError(
                "Production Pilot CA file is unavailable"
            )
    except OSError:
        raise ProductionPilotLiveProbeFactoryError(
            "Production Pilot CA file is unavailable"
        ) from None
    return path


def _clean_https_origin(value: Any) -> str:
    if not isinstance(value, str):
        raise ProductionPilotLiveProbeFactoryError(
            "Production Pilot live probe URL is invalid"
        )
    normalized = value.rstrip("/")
    parsed = urlparse(normalized)
    if (
        not normalized
        or value != value.strip()
        or parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ProductionPilotLiveProbeFactoryError(
            "Production Pilot live probe requires a clean HTTPS origin"
        )
    return normalized


def _digest_mapping(values: dict[str, Any]) -> str:
    canonical = json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return sha256(
        canonical.encode("utf-8")
    ).hexdigest()


__all__ = [
    "PRODUCTION_PILOT_LIVE_PROBE_GATE_ACKNOWLEDGEMENT",
    "ProductionPilotLiveProbeError",
    "ProductionPilotLiveProbeFactoryError",
    "ProductionPilotLiveProbeResult",
    "ProductionPilotLiveReadinessProbe",
    "create_production_pilot_live_readiness_probe",
]
