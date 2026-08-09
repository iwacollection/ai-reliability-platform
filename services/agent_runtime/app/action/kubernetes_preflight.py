import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from re import fullmatch
from typing import Any
from urllib.parse import quote, urlparse
from uuid import UUID, uuid4

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
    ActionType,
)
from services.agent_runtime.app.action.safety_models import (
    KubernetesMutationPrecondition,
    KubernetesServerDryRunProof,
    KubernetesWorkloadScope,
    MemoryLimitChange,
    ProductionActionSafetyContract,
    memory_quantity_bytes,
)


class KubernetesPreflightError(RuntimeError):
    """Base error for trusted Kubernetes remediation preflight."""


class KubernetesPreflightConfigurationError(KubernetesPreflightError):
    """Preflight is disabled or its connection configuration is unsafe."""


class KubernetesPreflightPolicyError(KubernetesPreflightError):
    """The requested target is outside the exact production allowlist."""


class KubernetesPreflightAuthorizationError(KubernetesPreflightError):
    """Kubernetes rejected the preflight identity."""


class KubernetesPreflightResourceNotFoundError(KubernetesPreflightError):
    """A Pod, ReplicaSet, or Deployment in the owner chain was not found."""


class KubernetesPreflightConflictError(KubernetesPreflightError):
    """The Kubernetes object changed while preflight was being prepared."""


class KubernetesPreflightResponseError(KubernetesPreflightError):
    """Kubernetes returned an invalid or unsafe object."""


_DNS_LABEL_PATTERN = r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?"
_DNS_SUBDOMAIN_PATTERN = (
    rf"{_DNS_LABEL_PATTERN}(?:\.{_DNS_LABEL_PATTERN})*"
)
_CLUSTER_PATTERN = r"[A-Za-z0-9](?:[A-Za-z0-9_.:-]{0,126}[A-Za-z0-9])?"
_FIELD_MANAGER_PATTERN = r"[A-Za-z0-9](?:[A-Za-z0-9_.:/-]{0,126}[A-Za-z0-9])?"
_CONTRACT_ANNOTATION = "ai-reliability-platform/safety-contract-id"
_POLICY_ANNOTATION = "ai-reliability-platform/safety-policy-version"
_CONTENT_TYPE = "application/strategic-merge-patch+json"


class _BearerCredential:
    """Minimal in-memory credential wrapper with a non-secret representation."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def authorization_header(self) -> str:
        return f"Bearer {self._value}"

    def __repr__(self) -> str:
        return "<KubernetesBearerCredential redacted>"


def _required_text(
    value: Any,
    *,
    label: str,
    max_length: int,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")

    if value != value.strip() or not value:
        raise ValueError(f"{label} must be non-empty without surrounding whitespace")

    if len(value) > max_length:
        raise ValueError(f"{label} is too long")

    return value


def _dns_label(value: Any, *, label: str) -> str:
    normalized = _required_text(
        value,
        label=label,
        max_length=63,
    )
    if fullmatch(_DNS_LABEL_PATTERN, normalized) is None:
        raise ValueError(f"{label} must be a Kubernetes DNS label")
    return normalized


def _dns_subdomain(value: Any, *, label: str) -> str:
    normalized = _required_text(
        value,
        label=label,
        max_length=253,
    )
    if fullmatch(_DNS_SUBDOMAIN_PATTERN, normalized) is None:
        raise ValueError(f"{label} must be a Kubernetes DNS subdomain")
    return normalized


class KubernetesPreflightRequest(BaseModel):
    """Trusted request to resolve one OOMKilled Pod to an allowed workload."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    incident_id: UUID
    cluster: str
    namespace: str
    pod_name: str
    container: str | None = None
    reason: str = "OOMKilled remediation requires a bounded memory increase"

    @field_validator("cluster", mode="before")
    @classmethod
    def validate_cluster(cls, value: Any) -> str:
        normalized = _required_text(
            value,
            label="Kubernetes cluster",
            max_length=128,
        )
        if fullmatch(_CLUSTER_PATTERN, normalized) is None:
            raise ValueError("Kubernetes cluster identifier is invalid")
        return normalized

    @field_validator("namespace", mode="before")
    @classmethod
    def validate_namespace(cls, value: Any) -> str:
        return _dns_label(value, label="Kubernetes namespace")

    @field_validator("pod_name", mode="before")
    @classmethod
    def validate_pod_name(cls, value: Any) -> str:
        return _dns_subdomain(value, label="Kubernetes Pod name")

    @field_validator("container", mode="before")
    @classmethod
    def validate_container(cls, value: Any) -> str | None:
        if value is None:
            return None
        return _dns_label(value, label="Kubernetes container name")

    @field_validator("reason", mode="before")
    @classmethod
    def validate_reason(cls, value: Any) -> str:
        return _required_text(
            value,
            label="Preflight reason",
            max_length=1000,
        )


class KubernetesPreflightPolicy(BaseModel):
    """Exact allowlist and bounded mutation policy for OOM Pilot v1."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    enabled: bool = False
    allowed_targets: tuple[KubernetesWorkloadScope, ...] = Field(
        default_factory=tuple,
    )
    increase_percent: int = Field(default=25, ge=1, le=25)
    contract_ttl_seconds: int = Field(default=600, ge=60, le=900)
    request_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    field_manager: str = "ai-reliability-platform"
    policy_version: str = "oom-memory-increase-v1"

    @field_validator("field_manager", mode="before")
    @classmethod
    def validate_field_manager(cls, value: Any) -> str:
        normalized = _required_text(
            value,
            label="Kubernetes field manager",
            max_length=128,
        )
        if fullmatch(_FIELD_MANAGER_PATTERN, normalized) is None:
            raise ValueError("Kubernetes field manager is invalid")
        return normalized

    @field_validator("policy_version", mode="before")
    @classmethod
    def validate_policy_version(cls, value: Any) -> str:
        return _required_text(
            value,
            label="Preflight policy version",
            max_length=64,
        )

    @model_validator(mode="after")
    def validate_allowlist(self) -> "KubernetesPreflightPolicy":
        if self.enabled and not self.allowed_targets:
            raise ValueError("Enabled Kubernetes preflight requires an allowlist")

        keys = [
            (
                item.cluster,
                item.namespace,
                item.kind.value,
                item.name,
                item.container,
            )
            for item in self.allowed_targets
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("Kubernetes preflight targets must be unique")

        return self

    def require_request_allowed(
        self,
        request: KubernetesPreflightRequest,
    ) -> None:
        if not self.enabled:
            raise KubernetesPreflightConfigurationError(
                "Kubernetes remediation preflight is disabled"
            )

        candidates = [
            item
            for item in self.allowed_targets
            if item.cluster == request.cluster
            and item.namespace == request.namespace
            and (
                request.container is None
                or item.container == request.container
            )
        ]

        if not candidates:
            raise KubernetesPreflightPolicyError(
                "Kubernetes request is outside the configured allowlist"
            )


class KubernetesPreflightArtifact(BaseModel):
    """Immutable preflight output; the canonical patch is digest-bound."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    contract: ProductionActionSafetyContract
    plan: ActionPlan
    patch_json: str
    dry_run_generation: int = Field(ge=1)
    source_pod_uid: str
    source_replicaset_uid: str
    source_restart_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_patch_binding(self) -> "KubernetesPreflightArtifact":
        try:
            patch = json.loads(self.patch_json)
        except (TypeError, ValueError) as exc:
            raise ValueError("Preflight patch must be valid JSON") from exc

        if not isinstance(patch, dict):
            raise ValueError("Preflight patch must be a JSON object")

        digest = sha256(self.patch_json.encode("utf-8")).hexdigest()
        if digest != self.contract.dry_run.patch_sha256:
            raise ValueError("Preflight patch does not match its safety contract")

        return self

    @property
    def patch(self) -> dict[str, Any]:
        return json.loads(self.patch_json)


class KubernetesPreflightResolver:
    """
    Resolve an OOMKilled Pod to an allowlisted Deployment and ask the API
    server to validate the exact memory patch with dryRun=All.

    This component never sends a mutating request without dryRun=All and is not
    wired into ActionRuntime. It only prepares an immutable safety artifact.
    """

    def __init__(
        self,
        *,
        api_url: str,
        cluster_name: str,
        policy: KubernetesPreflightPolicy,
        bearer_token: str | None = None,
        verify_tls: bool | str = True,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
        contract_id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        normalized_url = _required_text(
            api_url,
            label="Kubernetes API URL",
            max_length=2048,
        ).rstrip("/")
        parsed = urlparse(normalized_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise KubernetesPreflightConfigurationError(
                "Trusted Kubernetes preflight requires a clean HTTPS API URL"
            )

        normalized_cluster = _required_text(
            cluster_name,
            label="Kubernetes cluster",
            max_length=128,
        )
        if fullmatch(_CLUSTER_PATTERN, normalized_cluster) is None:
            raise KubernetesPreflightConfigurationError(
                "Kubernetes cluster identifier is invalid"
            )

        if not isinstance(policy, KubernetesPreflightPolicy):
            raise KubernetesPreflightConfigurationError(
                "Kubernetes preflight policy is invalid"
            )

        if verify_tls is not True and not isinstance(verify_tls, str):
            raise KubernetesPreflightConfigurationError(
                "Trusted Kubernetes preflight requires TLS verification"
            )

        if isinstance(verify_tls, str) and not verify_tls.strip():
            raise KubernetesPreflightConfigurationError(
                "Kubernetes CA path cannot be empty"
            )

        if bearer_token is not None:
            if (
                not isinstance(bearer_token, str)
                or not bearer_token
                or bearer_token != bearer_token.strip()
            ):
                raise KubernetesPreflightConfigurationError(
                    "Kubernetes bearer token is invalid"
                )

        if client is None and bearer_token is None:
            raise KubernetesPreflightConfigurationError(
                "Kubernetes bearer token is required without an injected client"
            )

        self.api_url = normalized_url
        self.cluster_name = normalized_cluster
        self.policy = policy
        self._bearer_credential = (
            _BearerCredential(bearer_token)
            if bearer_token is not None
            else None
        )
        self.verify_tls = verify_tls
        self.client = client
        self._clock = clock or (lambda: datetime.now(UTC))
        self._contract_id_factory = contract_id_factory or uuid4

    async def prepare(
        self,
        request: KubernetesPreflightRequest,
    ) -> KubernetesPreflightArtifact:
        if not isinstance(request, KubernetesPreflightRequest):
            raise KubernetesPreflightPolicyError(
                "Trusted preflight requires KubernetesPreflightRequest"
            )

        if request.cluster != self.cluster_name:
            raise KubernetesPreflightPolicyError(
                "Requested cluster does not match the configured connection"
            )

        self.policy.require_request_allowed(request)
        prepared_at = self._now()

        pod, _ = await self._request_json(
            "GET",
            self._pod_path(request.namespace, request.pod_name),
        )
        pod_metadata = self._require_object(
            pod,
            api_version="v1",
            kind="Pod",
            namespace=request.namespace,
            name=request.pod_name,
        )
        self._require_not_deleting(pod_metadata, "Pod")
        replica_owner = self._controller_owner(
            pod_metadata,
            expected_api_version="apps/v1",
            expected_kind="ReplicaSet",
        )

        replica_set, _ = await self._request_json(
            "GET",
            self._replica_set_path(
                request.namespace,
                replica_owner["name"],
            ),
        )
        replica_metadata = self._require_object(
            replica_set,
            api_version="apps/v1",
            kind="ReplicaSet",
            namespace=request.namespace,
            name=replica_owner["name"],
        )
        self._require_uid_match(
            replica_metadata,
            replica_owner["uid"],
            "ReplicaSet",
        )
        self._require_not_deleting(replica_metadata, "ReplicaSet")
        deployment_owner = self._controller_owner(
            replica_metadata,
            expected_api_version="apps/v1",
            expected_kind="Deployment",
        )

        deployment, _ = await self._request_json(
            "GET",
            self._deployment_path(
                request.namespace,
                deployment_owner["name"],
            ),
        )
        deployment_metadata = self._require_object(
            deployment,
            api_version="apps/v1",
            kind="Deployment",
            namespace=request.namespace,
            name=deployment_owner["name"],
        )
        self._require_uid_match(
            deployment_metadata,
            deployment_owner["uid"],
            "Deployment",
        )
        self._require_not_deleting(deployment_metadata, "Deployment")

        scope = self._select_scope(
            request=request,
            deployment_name=deployment_owner["name"],
            deployment=deployment,
        )
        pod_uid = self._required_metadata_text(
            pod_metadata,
            "uid",
            "Pod UID",
        )
        replica_uid = self._required_metadata_text(
            replica_metadata,
            "uid",
            "ReplicaSet UID",
        )
        restart_count = self._require_oom_killed(
            pod,
            scope.container,
        )
        current_limit = self._memory_limit(
            deployment,
            scope.container,
        )
        desired_limit = self._desired_memory_limit(current_limit)
        memory = MemoryLimitChange(
            current_limit=current_limit,
            desired_limit=desired_limit,
            max_increase_percent=self.policy.increase_percent,
        )
        precondition = self._precondition(deployment_metadata)
        contract_id = self._contract_id_factory()
        patch = self._build_patch(
            scope=scope,
            precondition=precondition,
            desired_limit=desired_limit,
            contract_id=contract_id,
        )
        patch_json = self._canonical_json(patch)
        patch_digest = sha256(patch_json.encode("utf-8")).hexdigest()

        dry_run, response_headers = await self._request_json(
            "PATCH",
            self._deployment_path(scope.namespace, scope.name),
            params={
                "dryRun": "All",
                "fieldManager": self.policy.field_manager,
            },
            content=patch_json.encode("utf-8"),
            content_type=_CONTENT_TYPE,
        )
        dry_run_generation = self._validate_dry_run_response(
            dry_run,
            scope=scope,
            precondition=precondition,
            desired_limit=desired_limit,
            contract_id=contract_id,
        )
        warnings = tuple(response_headers.get_list("warning"))
        proof = KubernetesServerDryRunProof(
            validated_at=prepared_at,
            workload_uid=precondition.workload_uid,
            resource_version=precondition.resource_version,
            generation=precondition.generation,
            patch_sha256=patch_digest,
            field_manager=self.policy.field_manager,
            warnings=warnings,
        )
        contract = ProductionActionSafetyContract(
            contract_id=contract_id,
            incident_id=request.incident_id,
            scope=scope,
            precondition=precondition,
            memory=memory,
            dry_run=proof,
            policy_version=self.policy.policy_version,
            prepared_at=prepared_at,
            expires_at=(
                prepared_at
                + timedelta(seconds=self.policy.contract_ttl_seconds)
            ),
        )
        plan = contract.bind_plan(
            ActionPlan(
                type=ActionType.INCREASE_MEMORY_LIMIT,
                target=scope.name,
                namespace=scope.namespace,
                cluster=scope.cluster,
                risk=ActionRisk.MEDIUM,
                approved=False,
                metadata={
                    "reason": request.reason,
                    "source_pod": request.pod_name,
                    "preflight_mode": "kubernetes_server_dry_run",
                },
            )
        )

        return KubernetesPreflightArtifact(
            contract=contract,
            plan=plan,
            patch_json=patch_json,
            dry_run_generation=dry_run_generation,
            source_pod_uid=pod_uid,
            source_replicaset_uid=replica_uid,
            source_restart_count=restart_count,
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[dict[str, Any], httpx.Headers]:
        headers = {"Accept": "application/json"}
        if self._bearer_credential is not None:
            headers["Authorization"] = (
                self._bearer_credential.authorization_header()
            )
        if content_type:
            headers["Content-Type"] = content_type

        url = f"{self.api_url}{path}"

        try:
            if self.client is not None:
                response = await self.client.request(
                    method,
                    url,
                    params=params,
                    content=content,
                    headers=headers,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self.policy.request_timeout_seconds,
                    verify=self.verify_tls,
                ) as client:
                    response = await client.request(
                        method,
                        url,
                        params=params,
                        content=content,
                        headers=headers,
                    )

            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise KubernetesPreflightResponseError(
                "Kubernetes preflight request timed out"
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                raise KubernetesPreflightAuthorizationError(
                    "Kubernetes preflight authorization failed"
                ) from exc
            if status == 404:
                raise KubernetesPreflightResourceNotFoundError(
                    "Kubernetes preflight resource was not found"
                ) from exc
            if status == 409:
                raise KubernetesPreflightConflictError(
                    "Kubernetes object changed during preflight"
                ) from exc
            raise KubernetesPreflightResponseError(
                f"Kubernetes preflight returned HTTP {status}"
            ) from exc
        except httpx.RequestError as exc:
            raise KubernetesPreflightResponseError(
                "Kubernetes preflight request failed"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise KubernetesPreflightResponseError(
                "Kubernetes preflight returned invalid JSON"
            ) from exc

        if not isinstance(payload, dict):
            raise KubernetesPreflightResponseError(
                "Kubernetes preflight response is not an object"
            )

        if payload.get("kind") == "Status" and payload.get("status") == "Failure":
            raise KubernetesPreflightResponseError(
                "Kubernetes preflight returned a failure object"
            )

        return payload, response.headers

    def _select_scope(
        self,
        *,
        request: KubernetesPreflightRequest,
        deployment_name: str,
        deployment: Mapping[str, Any],
    ) -> KubernetesWorkloadScope:
        container_names = self._deployment_container_names(deployment)
        candidates = [
            item
            for item in self.policy.allowed_targets
            if item.cluster == request.cluster
            and item.namespace == request.namespace
            and item.name == deployment_name
            and item.container in container_names
            and (
                request.container is None
                or item.container == request.container
            )
        ]

        if not candidates:
            raise KubernetesPreflightPolicyError(
                "Resolved Deployment container is outside the allowlist"
            )
        if len(candidates) != 1:
            raise KubernetesPreflightPolicyError(
                "Resolved Deployment container is ambiguous"
            )

        return candidates[0]

    def _desired_memory_limit(self, current_limit: str) -> str:
        current_bytes = memory_quantity_bytes(current_limit)
        current_mib = current_bytes // (2**20)
        increase_mib = (
            current_mib * self.policy.increase_percent // 100
        )
        if increase_mib < 1:
            raise KubernetesPreflightPolicyError(
                "Configured memory increase is not representable in whole Mi"
            )
        return f"{current_mib + increase_mib}Mi"

    def _build_patch(
        self,
        *,
        scope: KubernetesWorkloadScope,
        precondition: KubernetesMutationPrecondition,
        desired_limit: str,
        contract_id: UUID,
    ) -> dict[str, Any]:
        annotations = {
            _CONTRACT_ANNOTATION: str(contract_id),
            _POLICY_ANNOTATION: self.policy.policy_version,
        }
        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": scope.name,
                "namespace": scope.namespace,
                "resourceVersion": precondition.resource_version,
            },
            "spec": {
                "template": {
                    "metadata": {"annotations": annotations},
                    "spec": {
                        "containers": [
                            {
                                "name": scope.container,
                                "resources": {
                                    "limits": {"memory": desired_limit}
                                },
                            }
                        ]
                    },
                }
            },
        }

    def _validate_dry_run_response(
        self,
        payload: Mapping[str, Any],
        *,
        scope: KubernetesWorkloadScope,
        precondition: KubernetesMutationPrecondition,
        desired_limit: str,
        contract_id: UUID,
    ) -> int:
        metadata = self._require_object(
            payload,
            api_version="apps/v1",
            kind="Deployment",
            namespace=scope.namespace,
            name=scope.name,
        )
        self._require_uid_match(
            metadata,
            str(precondition.workload_uid),
            "dry-run Deployment",
        )
        resource_version = self._required_metadata_text(
            metadata,
            "resourceVersion",
            "dry-run resourceVersion",
        )
        if resource_version != precondition.resource_version:
            raise KubernetesPreflightConflictError(
                "Dry-run resourceVersion does not match the preflight read"
            )

        generation = self._required_generation(metadata)
        if generation not in {
            precondition.generation,
            precondition.generation + 1,
        }:
            raise KubernetesPreflightResponseError(
                "Dry-run generation is outside the expected update boundary"
            )

        if self._memory_limit(payload, scope.container) != desired_limit:
            raise KubernetesPreflightResponseError(
                "Dry-run response did not contain the requested memory limit"
            )

        template_metadata = self._mapping_path(
            payload,
            "spec",
            "template",
            "metadata",
        )
        annotations = template_metadata.get("annotations")
        if not isinstance(annotations, Mapping):
            raise KubernetesPreflightResponseError(
                "Dry-run response omitted safety annotations"
            )
        if annotations.get(_CONTRACT_ANNOTATION) != str(contract_id):
            raise KubernetesPreflightResponseError(
                "Dry-run response changed the safety contract annotation"
            )
        if annotations.get(_POLICY_ANNOTATION) != self.policy.policy_version:
            raise KubernetesPreflightResponseError(
                "Dry-run response changed the safety policy annotation"
            )

        return generation

    @staticmethod
    def _controller_owner(
        metadata: Mapping[str, Any],
        *,
        expected_api_version: str,
        expected_kind: str,
    ) -> dict[str, str]:
        references = metadata.get("ownerReferences")
        if not isinstance(references, list):
            references = []
        controllers = [
            item
            for item in references
            if isinstance(item, Mapping) and item.get("controller") is True
        ]
        if len(controllers) != 1:
            raise KubernetesPreflightResponseError(
                f"Kubernetes object must have exactly one {expected_kind} controller"
            )

        controller = controllers[0]
        if controller.get("apiVersion") != expected_api_version:
            raise KubernetesPreflightPolicyError(
                "Kubernetes owner chain uses an unsupported API version"
            )
        if controller.get("kind") != expected_kind:
            raise KubernetesPreflightPolicyError(
                f"Kubernetes owner chain must resolve through {expected_kind}"
            )

        name = controller.get("name")
        uid = controller.get("uid")
        if not isinstance(name, str) or not name:
            raise KubernetesPreflightResponseError(
                f"{expected_kind} owner name is invalid"
            )
        try:
            _dns_subdomain(name, label=f"{expected_kind} owner name")
        except ValueError as exc:
            raise KubernetesPreflightResponseError(
                f"{expected_kind} owner name is invalid"
            ) from exc
        if not isinstance(uid, str) or not uid:
            raise KubernetesPreflightResponseError(
                f"{expected_kind} owner UID is invalid"
            )
        return {"name": name, "uid": uid}

    @classmethod
    def _require_object(
        cls,
        payload: Mapping[str, Any],
        *,
        api_version: str,
        kind: str,
        namespace: str,
        name: str,
    ) -> Mapping[str, Any]:
        if payload.get("apiVersion") != api_version or payload.get("kind") != kind:
            raise KubernetesPreflightResponseError(
                f"Kubernetes response is not an {api_version} {kind}"
            )
        metadata = payload.get("metadata")
        if not isinstance(metadata, Mapping):
            raise KubernetesPreflightResponseError(
                f"Kubernetes {kind} metadata is invalid"
            )
        if metadata.get("name") != name or metadata.get("namespace") != namespace:
            raise KubernetesPreflightResponseError(
                f"Kubernetes {kind} identity does not match the request"
            )
        return metadata

    @staticmethod
    def _require_not_deleting(metadata: Mapping[str, Any], label: str) -> None:
        if metadata.get("deletionTimestamp") is not None:
            raise KubernetesPreflightPolicyError(
                f"Kubernetes {label} is being deleted"
            )

    @staticmethod
    def _require_uid_match(
        metadata: Mapping[str, Any],
        expected_uid: str,
        label: str,
    ) -> None:
        if metadata.get("uid") != expected_uid:
            raise KubernetesPreflightConflictError(
                f"Kubernetes {label} UID changed during owner resolution"
            )

    @staticmethod
    def _required_metadata_text(
        metadata: Mapping[str, Any],
        key: str,
        label: str,
    ) -> str:
        value = metadata.get(key)
        if not isinstance(value, str) or not value:
            raise KubernetesPreflightResponseError(f"{label} is invalid")
        return value

    @classmethod
    def _precondition(
        cls,
        metadata: Mapping[str, Any],
    ) -> KubernetesMutationPrecondition:
        uid_text = cls._required_metadata_text(
            metadata,
            "uid",
            "Deployment UID",
        )
        try:
            uid = UUID(uid_text)
        except ValueError as exc:
            raise KubernetesPreflightResponseError(
                "Deployment UID is not a UUID"
            ) from exc
        return KubernetesMutationPrecondition(
            workload_uid=uid,
            resource_version=cls._required_metadata_text(
                metadata,
                "resourceVersion",
                "Deployment resourceVersion",
            ),
            generation=cls._required_generation(metadata),
        )

    @staticmethod
    def _required_generation(metadata: Mapping[str, Any]) -> int:
        generation = metadata.get("generation")
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
        ):
            raise KubernetesPreflightResponseError(
                "Deployment generation is invalid"
            )
        return generation

    @classmethod
    def _deployment_container_names(
        cls,
        deployment: Mapping[str, Any],
    ) -> set[str]:
        containers = cls._mapping_path(
            deployment,
            "spec",
            "template",
            "spec",
        ).get("containers")
        if not isinstance(containers, list) or not containers:
            raise KubernetesPreflightResponseError(
                "Deployment has no regular containers"
            )
        names = []
        for item in containers:
            if not isinstance(item, Mapping):
                raise KubernetesPreflightResponseError(
                    "Deployment container is invalid"
                )
            name = item.get("name")
            if not isinstance(name, str) or not name:
                raise KubernetesPreflightResponseError(
                    "Deployment container name is invalid"
                )
            names.append(name)
        if len(names) != len(set(names)):
            raise KubernetesPreflightResponseError(
                "Deployment contains duplicate container names"
            )
        return set(names)

    @classmethod
    def _memory_limit(
        cls,
        workload: Mapping[str, Any],
        container_name: str,
    ) -> str:
        containers = cls._mapping_path(
            workload,
            "spec",
            "template",
            "spec",
        ).get("containers")
        if not isinstance(containers, list):
            raise KubernetesPreflightResponseError(
                "Deployment containers are invalid"
            )
        matches = [
            item
            for item in containers
            if isinstance(item, Mapping) and item.get("name") == container_name
        ]
        if len(matches) != 1:
            raise KubernetesPreflightResponseError(
                "Deployment container identity is ambiguous"
            )
        resources = matches[0].get("resources")
        limits = resources.get("limits") if isinstance(resources, Mapping) else None
        memory = limits.get("memory") if isinstance(limits, Mapping) else None
        if not isinstance(memory, str):
            raise KubernetesPreflightPolicyError(
                "Deployment container has no explicit memory limit"
            )
        try:
            memory_quantity_bytes(memory)
        except ValueError as exc:
            raise KubernetesPreflightPolicyError(
                "Deployment memory limit is outside the Pilot Mi/Gi boundary"
            ) from exc
        return memory

    @classmethod
    def _require_oom_killed(
        cls,
        pod: Mapping[str, Any],
        container_name: str,
    ) -> int:
        spec = pod.get("spec")
        spec_containers = spec.get("containers") if isinstance(spec, Mapping) else None
        if not isinstance(spec_containers, list):
            raise KubernetesPreflightResponseError("Pod containers are invalid")
        if sum(
            1
            for item in spec_containers
            if isinstance(item, Mapping) and item.get("name") == container_name
        ) != 1:
            raise KubernetesPreflightPolicyError(
                "Selected container is not present in the source Pod"
            )

        status = pod.get("status")
        statuses = status.get("containerStatuses") if isinstance(status, Mapping) else None
        if not isinstance(statuses, list):
            raise KubernetesPreflightPolicyError(
                "Source Pod has no container status evidence"
            )
        matches = [
            item
            for item in statuses
            if isinstance(item, Mapping) and item.get("name") == container_name
        ]
        if len(matches) != 1:
            raise KubernetesPreflightPolicyError(
                "Source Pod container status is ambiguous"
            )

        container_status = matches[0]
        state = container_status.get("state")
        last_state = container_status.get("lastState")
        current_reason = cls._termination_reason(state)
        last_reason = cls._termination_reason(last_state)
        if "OOMKilled" not in {current_reason, last_reason}:
            raise KubernetesPreflightPolicyError(
                "Source Pod does not prove OOMKilled for the selected container"
            )

        restart_count = container_status.get("restartCount", 0)
        if (
            isinstance(restart_count, bool)
            or not isinstance(restart_count, int)
            or restart_count < 0
        ):
            raise KubernetesPreflightResponseError(
                "Source Pod restart count is invalid"
            )
        return restart_count

    @staticmethod
    def _termination_reason(value: Any) -> str | None:
        if not isinstance(value, Mapping):
            return None
        terminated = value.get("terminated")
        if not isinstance(terminated, Mapping):
            return None
        reason = terminated.get("reason")
        return reason if isinstance(reason, str) else None

    @staticmethod
    def _mapping_path(
        value: Mapping[str, Any],
        *keys: str,
    ) -> Mapping[str, Any]:
        current: Any = value
        for key in keys:
            if not isinstance(current, Mapping):
                raise KubernetesPreflightResponseError(
                    "Kubernetes workload structure is invalid"
                )
            current = current.get(key)
        if not isinstance(current, Mapping):
            raise KubernetesPreflightResponseError(
                "Kubernetes workload structure is invalid"
            )
        return current

    @staticmethod
    def _canonical_json(value: Mapping[str, Any]) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    @staticmethod
    def _pod_path(namespace: str, name: str) -> str:
        return (
            f"/api/v1/namespaces/{quote(namespace, safe='')}/pods/"
            f"{quote(name, safe='')}"
        )

    @staticmethod
    def _replica_set_path(namespace: str, name: str) -> str:
        return (
            f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/"
            f"replicasets/{quote(name, safe='')}"
        )

    @staticmethod
    def _deployment_path(namespace: str, name: str) -> str:
        return (
            f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/"
            f"deployments/{quote(name, safe='')}"
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise KubernetesPreflightConfigurationError(
                "Kubernetes preflight clock must be timezone-aware"
            )
        return value.astimezone(UTC)


__all__ = [
    "KubernetesPreflightArtifact",
    "KubernetesPreflightAuthorizationError",
    "KubernetesPreflightConfigurationError",
    "KubernetesPreflightConflictError",
    "KubernetesPreflightError",
    "KubernetesPreflightPolicy",
    "KubernetesPreflightPolicyError",
    "KubernetesPreflightRequest",
    "KubernetesPreflightResolver",
    "KubernetesPreflightResourceNotFoundError",
    "KubernetesPreflightResponseError",
]
