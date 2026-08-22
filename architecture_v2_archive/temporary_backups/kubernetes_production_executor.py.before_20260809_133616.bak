import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from re import fullmatch
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from services.agent_runtime.app.action.execution_models import (
    ActionExecutionRecord,
    ActionExecutionStatus,
)
from services.agent_runtime.app.action.preflight_artifact_models import (
    PreflightArtifactRecord,
    PreflightArtifactStatus,
)
from services.agent_runtime.app.action.production_pilot import (
    KubernetesProductionPilotBlockedError,
    KubernetesProductionPilotControl,
)
from services.agent_runtime.app.action.production_pilot_budget_service import (
    ProductionPilotBudgetService,
)
from services.agent_runtime.app.action.production_pilot_budget_store import (
    ProductionPilotBudgetConflictError,
)
from services.agent_runtime.app.action.safety_models import (
    ActionSafetyContractError,
    KubernetesWorkloadScope,
)


class KubernetesProductionExecutorError(RuntimeError):
    """Base fail-closed production execution error."""


class KubernetesProductionExecutorConfigurationError(
    KubernetesProductionExecutorError
):
    """The write executor is disabled or configured unsafely."""


class KubernetesProductionExecutorPolicyError(
    KubernetesProductionExecutorError
):
    """The durable action is outside the exact production allowlist."""


class KubernetesProductionExecutorBindingError(
    KubernetesProductionExecutorError
):
    """Execution, Artifact, Action, Incident, or digest binding is invalid."""


class KubernetesProductionExecutorConflictError(
    KubernetesProductionExecutorError
):
    """The Deployment changed after trusted preflight."""


class KubernetesProductionExecutorAuthorizationError(
    KubernetesProductionExecutorError
):
    """Kubernetes rejected the dedicated production execution identity."""


class KubernetesProductionExecutorResponseError(
    KubernetesProductionExecutorError
):
    """Kubernetes returned an invalid response before a real write."""


class KubernetesProductionOutcomeIndeterminateError(
    KubernetesProductionExecutorError
):
    """
    A real PATCH may have reached Kubernetes but its outcome is unknown.

    Callers must persist INDETERMINATE and require reconciliation. They must
    never automatically replay the write.
    """


_CLUSTER_PATTERN = (
    r"[A-Za-z0-9](?:[A-Za-z0-9_.:-]{0,126}[A-Za-z0-9])?"
)
_FIELD_MANAGER_PATTERN = (
    r"[A-Za-z0-9](?:[A-Za-z0-9_.:/-]{0,126}[A-Za-z0-9])?"
)
_CONTRACT_ANNOTATION = (
    "ai-reliability-platform/safety-contract-id"
)
_POLICY_ANNOTATION = (
    "ai-reliability-platform/safety-policy-version"
)
_CONTENT_TYPE = "application/strategic-merge-patch+json"


class _BearerCredential:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def authorization_header(self) -> str:
        return f"Bearer {self._value}"

    def __repr__(self) -> str:
        return "<KubernetesProductionCredential redacted>"


class KubernetesProductionExecutorPolicy(BaseModel):
    """Frozen gate and exact targets for the first real-write pilot."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    enabled: bool = False
    allowed_targets: tuple[KubernetesWorkloadScope, ...] = Field(
        default_factory=tuple,
    )
    request_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=30,
    )
    minimum_remaining_seconds: int = Field(
        default=5,
        ge=1,
        le=60,
    )
    field_manager: str = "ai-reliability-platform"
    policy_version: str = "oom-memory-increase-v1"

    @field_validator("field_manager", mode="before")
    @classmethod
    def validate_field_manager(cls, value: Any) -> str:
        if (
            not isinstance(value, str)
            or value != value.strip()
            or fullmatch(_FIELD_MANAGER_PATTERN, value) is None
        ):
            raise ValueError(
                "Kubernetes production field manager is invalid"
            )
        return value

    @field_validator("policy_version", mode="before")
    @classmethod
    def validate_policy_version(cls, value: Any) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 64
        ):
            raise ValueError(
                "Kubernetes production policy version is invalid"
            )
        return value

    @model_validator(mode="after")
    def validate_allowlist(
        self,
    ) -> "KubernetesProductionExecutorPolicy":
        if self.enabled and not self.allowed_targets:
            raise ValueError(
                "Enabled Kubernetes production execution requires an allowlist"
            )

        target_keys = [
            (
                item.cluster,
                item.namespace,
                item.kind.value,
                item.name,
                item.container,
            )
            for item in self.allowed_targets
        ]
        if len(target_keys) != len(set(target_keys)):
            raise ValueError(
                "Kubernetes production execution targets must be unique"
            )

        return self

    def require_scope_allowed(
        self,
        scope: KubernetesWorkloadScope,
    ) -> None:
        if not self.enabled:
            raise KubernetesProductionExecutorConfigurationError(
                "Kubernetes production execution is disabled"
            )
        if scope not in self.allowed_targets:
            raise KubernetesProductionExecutorPolicyError(
                "Kubernetes production target is outside the exact allowlist"
            )


class KubernetesProductionExecutor:
    """
    Digest-bound Kubernetes Deployment writer for OOMKilled Pilot v1.

    The executor accepts only a durable RUNNING execution claim and the exact
    Approval-bound immutable preflight record. It performs one current-state
    GET and one server dry-run before one real PATCH. The real PATCH is never
    retried. Ambiguous post-write transport or response failures raise
    KubernetesProductionOutcomeIndeterminateError.

    This class is intentionally not a BaseExecutor implementation yet. Merely
    constructing it cannot alter ActionRuntime's existing MockExecutor path.
    """

    def __init__(
        self,
        *,
        api_url: str,
        cluster_name: str,
        policy: KubernetesProductionExecutorPolicy,
        bearer_token: str | None = None,
        verify_tls: bool | str = True,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
        pilot_control: (
            KubernetesProductionPilotControl | None
        ) = None,
        pilot_budget_service: (
            ProductionPilotBudgetService | None
        ) = None,
    ) -> None:
        if not isinstance(api_url, str):
            raise KubernetesProductionExecutorConfigurationError(
                "Kubernetes production API URL is invalid"
            )
        normalized_url = api_url.rstrip("/")
        parsed = urlparse(normalized_url)
        if (
            not normalized_url
            or api_url != api_url.strip()
            or parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise KubernetesProductionExecutorConfigurationError(
                "Kubernetes production execution requires a clean HTTPS API URL"
            )

        if (
            not isinstance(cluster_name, str)
            or cluster_name != cluster_name.strip()
            or fullmatch(_CLUSTER_PATTERN, cluster_name) is None
        ):
            raise KubernetesProductionExecutorConfigurationError(
                "Kubernetes production cluster identifier is invalid"
            )
        if not isinstance(policy, KubernetesProductionExecutorPolicy):
            raise KubernetesProductionExecutorConfigurationError(
                "Kubernetes production execution policy is invalid"
            )
        if verify_tls is not True and not isinstance(verify_tls, str):
            raise KubernetesProductionExecutorConfigurationError(
                "Kubernetes production execution requires TLS verification"
            )
        if isinstance(verify_tls, str) and not verify_tls.strip():
            raise KubernetesProductionExecutorConfigurationError(
                "Kubernetes production CA path cannot be empty"
            )
        if bearer_token is not None and (
            not isinstance(bearer_token, str)
            or not bearer_token
            or bearer_token != bearer_token.strip()
        ):
            raise KubernetesProductionExecutorConfigurationError(
                "Kubernetes production bearer token is invalid"
            )
        if client is None and bearer_token is None:
            raise KubernetesProductionExecutorConfigurationError(
                "Kubernetes production bearer token is required"
            )
        if (
            pilot_control is not None
            and not isinstance(
                pilot_control,
                KubernetesProductionPilotControl,
            )
        ):
            raise KubernetesProductionExecutorConfigurationError(
                "Kubernetes production pilot control is invalid"
            )
        if (
            pilot_budget_service is not None
            and not isinstance(
                pilot_budget_service,
                ProductionPilotBudgetService,
            )
        ):
            raise KubernetesProductionExecutorConfigurationError(
                "Kubernetes production pilot budget service is invalid"
            )

        self.api_url = normalized_url
        self.cluster_name = cluster_name
        self.policy = policy
        self.verify_tls = verify_tls
        self.client = client
        self._clock = clock or (lambda: datetime.now(UTC))
        self.pilot_control = pilot_control
        self.pilot_budget_service = (
            pilot_budget_service
        )
        self._bearer_credential = (
            _BearerCredential(bearer_token)
            if bearer_token is not None
            else None
        )

    async def execute_claimed(
        self,
        execution: ActionExecutionRecord,
        record: PreflightArtifactRecord,
    ) -> dict[str, Any]:
        """Perform at most one real PATCH for one durable execution claim."""

        self._require_pilot(
            execution
        )
        self._require_binding(
            execution=execution,
            record=record,
        )
        artifact = record.artifact
        contract = artifact.contract
        scope = contract.scope

        if scope.cluster != self.cluster_name:
            raise KubernetesProductionExecutorPolicyError(
                "Kubernetes production target cluster does not match connection"
            )
        self.policy.require_scope_allowed(scope)
        if contract.policy_version != self.policy.policy_version:
            raise KubernetesProductionExecutorPolicyError(
                "Kubernetes production policy version changed after preflight"
            )
        if contract.dry_run.field_manager != self.policy.field_manager:
            raise KubernetesProductionExecutorPolicyError(
                "Kubernetes production field manager changed after preflight"
            )

        self._require_contract_time(execution, record)
        current = await self._request_json(
            "GET",
            self._deployment_path(scope),
            write_committed=False,
        )
        self._validate_current_deployment(
            current,
            record=record,
        )

        dry_run = await self._request_json(
            "PATCH",
            self._deployment_path(scope),
            params={
                "dryRun": "All",
                "fieldManager": self.policy.field_manager,
            },
            content=artifact.patch_json.encode("utf-8"),
            content_type=_CONTENT_TYPE,
            write_committed=False,
        )
        self._validate_dry_run(
            dry_run,
            record=record,
        )

        # The final clock check immediately precedes the sole real PATCH.
        self._require_contract_time(execution, record)
        self._require_pilot(
            execution
        )
        await self._consume_pilot_budget(
            execution=execution,
            record=record,
        )
        applied = await self._request_json(
            "PATCH",
            self._deployment_path(scope),
            params={
                "fieldManager": self.policy.field_manager,
            },
            content=artifact.patch_json.encode("utf-8"),
            content_type=_CONTENT_TYPE,
            write_committed=True,
        )

        try:
            applied_metadata = self._validate_applied(
                applied,
                record=record,
            )
        except KubernetesProductionExecutorError as exc:
            raise KubernetesProductionOutcomeIndeterminateError(
                "Kubernetes production write returned an unsafe response"
            ) from exc

        return {
            "success": True,
            "mode": "kubernetes_production",
            "action": execution.action.type.value,
            "resource": "deployment",
            "cluster": scope.cluster,
            "namespace": scope.namespace,
            "target": scope.name,
            "container": scope.container,
            "contract_id": str(contract.contract_id),
            "resource_version": applied_metadata["resourceVersion"],
            "generation": applied_metadata["generation"],
            "message": "Bounded Kubernetes memory update was acknowledged",
        }

    def _require_pilot(
        self,
        execution: ActionExecutionRecord,
    ) -> None:
        if self.pilot_control is None:
            raise KubernetesProductionExecutorConfigurationError(
                "Kubernetes production pilot control is unavailable"
            )
        try:
            self.pilot_control.require_execution(
                operator_id=execution.operator_id,
                production_executor_configured=True,
            )
        except KubernetesProductionPilotBlockedError as exc:
            raise KubernetesProductionExecutorConfigurationError(
                "Kubernetes production pilot blocked execution"
            ) from exc

    async def _consume_pilot_budget(
        self,
        *,
        execution: ActionExecutionRecord,
        record: PreflightArtifactRecord,
    ) -> None:
        if (
            self.pilot_control is None
            or self.pilot_control.config.pilot_id is None
            or self.pilot_budget_service is None
        ):
            raise KubernetesProductionExecutorConfigurationError(
                "Kubernetes production pilot budget is unavailable"
            )
        try:
            await self.pilot_budget_service.consume(
                pilot_id=(
                    self.pilot_control.config.pilot_id
                ),
                execution=execution,
                preflight_record=record,
            )
        except (
            ProductionPilotBudgetConflictError,
            TypeError,
            ValueError,
        ) as exc:
            raise KubernetesProductionExecutorConfigurationError(
                "Kubernetes production pilot budget could not be consumed"
            ) from exc

    def _require_binding(
        self,
        *,
        execution: ActionExecutionRecord,
        record: PreflightArtifactRecord,
    ) -> None:
        if not isinstance(execution, ActionExecutionRecord):
            raise KubernetesProductionExecutorBindingError(
                "Kubernetes production execution record is invalid"
            )
        if not isinstance(record, PreflightArtifactRecord):
            raise KubernetesProductionExecutorBindingError(
                "Kubernetes production preflight record is invalid"
            )
        if execution.status != ActionExecutionStatus.RUNNING:
            raise KubernetesProductionExecutorBindingError(
                "Kubernetes production execution claim is not running"
            )
        if execution.metadata.get("source") != "action_runtime.resume":
            raise KubernetesProductionExecutorBindingError(
                "Kubernetes production execution source is invalid"
            )
        if (
            record.status != PreflightArtifactStatus.APPROVAL_BOUND
            or record.approval_id != execution.approval_id
            or execution.incident_id != record.incident_id
        ):
            raise KubernetesProductionExecutorBindingError(
                "Kubernetes production durable binding is inconsistent"
            )

        patch_digest = sha256(
            record.artifact.patch_json.encode("utf-8")
        ).hexdigest()
        if patch_digest != record.artifact.contract.dry_run.patch_sha256:
            raise KubernetesProductionExecutorBindingError(
                "Kubernetes production patch digest is invalid"
            )
        if (
            record.artifact.patch_json
            != self._expected_patch_json(record)
        ):
            raise KubernetesProductionExecutorBindingError(
                "Kubernetes production patch is not the exact bounded mutation"
            )
        if record.artifact.dry_run_generation not in {
            record.artifact.contract.precondition.generation,
            record.artifact.contract.precondition.generation + 1,
        }:
            raise KubernetesProductionExecutorBindingError(
                "Kubernetes production preflight generation is invalid"
            )

        prepared = record.artifact.plan.model_dump(mode="json")
        claimed = execution.action.model_dump(mode="json")
        prepared.pop("approved", None)
        claimed.pop("approved", None)
        if prepared != claimed:
            raise KubernetesProductionExecutorBindingError(
                "Kubernetes production action does not match preflight"
            )

    def _require_contract_time(
        self,
        execution: ActionExecutionRecord,
        record: PreflightArtifactRecord,
    ) -> datetime:
        now = self._now()
        contract = record.artifact.contract
        if now < contract.prepared_at:
            raise KubernetesProductionExecutorBindingError(
                "Kubernetes production execution clock is invalid"
            )
        try:
            contract.require_executable_plan(
                execution.action,
                at=now,
            )
        except ActionSafetyContractError as exc:
            raise KubernetesProductionExecutorBindingError(
                "Kubernetes production safety contract is not executable"
            ) from exc

        remaining = (contract.expires_at - now).total_seconds()
        if remaining < self.policy.minimum_remaining_seconds:
            raise KubernetesProductionExecutorBindingError(
                "Kubernetes production safety contract has insufficient lifetime"
            )
        return now

    def _validate_current_deployment(
        self,
        payload: Mapping[str, Any],
        *,
        record: PreflightArtifactRecord,
    ) -> None:
        contract = record.artifact.contract
        metadata = self._deployment_metadata(
            payload,
            scope=contract.scope,
        )
        if metadata.get("deletionTimestamp") is not None:
            raise KubernetesProductionExecutorConflictError(
                "Kubernetes Deployment is being deleted"
            )
        if self._required_text(metadata, "uid") != str(
            contract.precondition.workload_uid
        ):
            raise KubernetesProductionExecutorConflictError(
                "Kubernetes Deployment UID changed after preflight"
            )
        if self._required_text(metadata, "resourceVersion") != (
            contract.precondition.resource_version
        ):
            raise KubernetesProductionExecutorConflictError(
                "Kubernetes Deployment resourceVersion changed after preflight"
            )
        if self._required_generation(metadata) != (
            contract.precondition.generation
        ):
            raise KubernetesProductionExecutorConflictError(
                "Kubernetes Deployment generation changed after preflight"
            )
        if self._memory_limit(payload, contract.scope.container) != (
            contract.memory.current_limit
        ):
            raise KubernetesProductionExecutorConflictError(
                "Kubernetes Deployment memory limit changed after preflight"
            )

    def _validate_dry_run(
        self,
        payload: Mapping[str, Any],
        *,
        record: PreflightArtifactRecord,
    ) -> None:
        contract = record.artifact.contract
        metadata = self._deployment_metadata(
            payload,
            scope=contract.scope,
        )
        if self._required_text(metadata, "uid") != str(
            contract.precondition.workload_uid
        ):
            raise KubernetesProductionExecutorConflictError(
                "Kubernetes dry-run Deployment UID changed"
            )
        if self._required_text(metadata, "resourceVersion") != (
            contract.precondition.resource_version
        ):
            raise KubernetesProductionExecutorConflictError(
                "Kubernetes dry-run resourceVersion changed"
            )
        if self._required_generation(metadata) not in {
            contract.precondition.generation,
            contract.precondition.generation + 1,
        }:
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes dry-run generation is unsafe"
            )
        self._require_desired_state(
            payload,
            record=record,
        )

    def _validate_applied(
        self,
        payload: Mapping[str, Any],
        *,
        record: PreflightArtifactRecord,
    ) -> dict[str, Any]:
        contract = record.artifact.contract
        metadata = self._deployment_metadata(
            payload,
            scope=contract.scope,
        )
        if self._required_text(metadata, "uid") != str(
            contract.precondition.workload_uid
        ):
            raise KubernetesProductionExecutorResponseError(
                "Applied Kubernetes Deployment UID is invalid"
            )
        resource_version = self._required_text(
            metadata,
            "resourceVersion",
        )
        if resource_version == contract.precondition.resource_version:
            raise KubernetesProductionExecutorResponseError(
                "Applied Kubernetes resourceVersion did not advance"
            )
        generation = self._required_generation(metadata)
        if generation != contract.precondition.generation + 1:
            raise KubernetesProductionExecutorResponseError(
                "Applied Kubernetes generation is unsafe"
            )
        self._require_desired_state(
            payload,
            record=record,
        )
        return {
            "resourceVersion": resource_version,
            "generation": generation,
        }

    def _require_desired_state(
        self,
        payload: Mapping[str, Any],
        *,
        record: PreflightArtifactRecord,
    ) -> None:
        contract = record.artifact.contract
        if self._memory_limit(payload, contract.scope.container) != (
            contract.memory.desired_limit
        ):
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes response omitted the approved memory limit"
            )
        template_metadata = self._mapping_path(
            payload,
            "spec",
            "template",
            "metadata",
        )
        annotations = template_metadata.get("annotations")
        if not isinstance(annotations, Mapping):
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes response omitted safety annotations"
            )
        if annotations.get(_CONTRACT_ANNOTATION) != str(
            contract.contract_id
        ):
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes response changed the safety contract annotation"
            )
        if annotations.get(_POLICY_ANNOTATION) != contract.policy_version:
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes response changed the safety policy annotation"
            )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
        write_committed: bool,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self._bearer_credential is not None:
            headers["Authorization"] = (
                self._bearer_credential.authorization_header()
            )
        if content_type is not None:
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
                    timeout=self.policy.request_timeout_seconds,
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
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            if write_committed:
                raise KubernetesProductionOutcomeIndeterminateError(
                    "Kubernetes production write outcome is indeterminate"
                ) from exc
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes production safety request failed"
            ) from exc

        status = response.status_code
        if status >= 500 and write_committed:
            raise KubernetesProductionOutcomeIndeterminateError(
                "Kubernetes production write outcome is indeterminate"
            )
        if status in {401, 403}:
            raise KubernetesProductionExecutorAuthorizationError(
                "Kubernetes production authorization failed"
            )
        if status in {404, 409}:
            raise KubernetesProductionExecutorConflictError(
                "Kubernetes production target changed or is unavailable"
            )
        if status < 200 or status >= 300:
            raise KubernetesProductionExecutorResponseError(
                f"Kubernetes production request returned HTTP {status}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            if write_committed:
                raise KubernetesProductionOutcomeIndeterminateError(
                    "Kubernetes production write outcome is indeterminate"
                ) from exc
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes production safety request returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            if write_committed:
                raise KubernetesProductionOutcomeIndeterminateError(
                    "Kubernetes production write outcome is indeterminate"
                )
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes production safety response is not an object"
            )
        if payload.get("kind") == "Status" and payload.get("status") == "Failure":
            if write_committed:
                raise KubernetesProductionOutcomeIndeterminateError(
                    "Kubernetes production write outcome is indeterminate"
                )
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes production safety request returned failure"
            )
        return payload

    def _now(self) -> datetime:
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise KubernetesProductionExecutorBindingError(
                "Kubernetes production clock must be timezone-aware"
            )
        return value.astimezone(UTC)

    @staticmethod
    def _expected_patch_json(
        record: PreflightArtifactRecord,
    ) -> str:
        contract = record.artifact.contract
        scope = contract.scope
        patch = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": scope.name,
                "namespace": scope.namespace,
                "resourceVersion": (
                    contract.precondition.resource_version
                ),
            },
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            _CONTRACT_ANNOTATION: str(
                                contract.contract_id
                            ),
                            _POLICY_ANNOTATION: (
                                contract.policy_version
                            ),
                        }
                    },
                    "spec": {
                        "containers": [
                            {
                                "name": scope.container,
                                "resources": {
                                    "limits": {
                                        "memory": (
                                            contract.memory.desired_limit
                                        )
                                    }
                                },
                            }
                        ]
                    },
                }
            },
        }
        return json.dumps(
            patch,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    @staticmethod
    def _deployment_path(scope: KubernetesWorkloadScope) -> str:
        return (
            "/apis/apps/v1/namespaces/"
            f"{quote(scope.namespace, safe='')}/deployments/"
            f"{quote(scope.name, safe='')}"
        )

    @classmethod
    def _deployment_metadata(
        cls,
        payload: Mapping[str, Any],
        *,
        scope: KubernetesWorkloadScope,
    ) -> Mapping[str, Any]:
        if (
            not isinstance(payload, Mapping)
            or payload.get("apiVersion") != "apps/v1"
            or payload.get("kind") != "Deployment"
        ):
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes response is not an apps/v1 Deployment"
            )
        metadata = payload.get("metadata")
        if not isinstance(metadata, Mapping):
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes Deployment metadata is invalid"
            )
        if (
            metadata.get("name") != scope.name
            or metadata.get("namespace") != scope.namespace
        ):
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes Deployment scope changed"
            )
        return metadata

    @classmethod
    def _memory_limit(
        cls,
        payload: Mapping[str, Any],
        container_name: str,
    ) -> str:
        pod_spec = cls._mapping_path(
            payload,
            "spec",
            "template",
            "spec",
        )
        containers = pod_spec.get("containers")
        if not isinstance(containers, list):
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes Deployment containers are invalid"
            )
        matches = [
            item
            for item in containers
            if isinstance(item, Mapping)
            and item.get("name") == container_name
        ]
        if len(matches) != 1:
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes target container is missing or ambiguous"
            )
        resources = matches[0].get("resources")
        limits = resources.get("limits") if isinstance(resources, Mapping) else None
        memory = limits.get("memory") if isinstance(limits, Mapping) else None
        if not isinstance(memory, str) or not memory:
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes target memory limit is invalid"
            )
        return memory

    @staticmethod
    def _mapping_path(
        value: Mapping[str, Any],
        *path: str,
    ) -> Mapping[str, Any]:
        current: Any = value
        for segment in path:
            if not isinstance(current, Mapping):
                raise KubernetesProductionExecutorResponseError(
                    "Kubernetes response structure is invalid"
                )
            current = current.get(segment)
        if not isinstance(current, Mapping):
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes response structure is invalid"
            )
        return current

    @staticmethod
    def _required_text(
        metadata: Mapping[str, Any],
        key: str,
    ) -> str:
        value = metadata.get(key)
        if not isinstance(value, str) or not value:
            raise KubernetesProductionExecutorResponseError(
                f"Kubernetes Deployment {key} is invalid"
            )
        return value

    @staticmethod
    def _required_generation(
        metadata: Mapping[str, Any],
    ) -> int:
        value = metadata.get("generation")
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise KubernetesProductionExecutorResponseError(
                "Kubernetes Deployment generation is invalid"
            )
        return value


__all__ = [
    "KubernetesProductionExecutor",
    "KubernetesProductionExecutorAuthorizationError",
    "KubernetesProductionExecutorBindingError",
    "KubernetesProductionExecutorConfigurationError",
    "KubernetesProductionExecutorConflictError",
    "KubernetesProductionExecutorError",
    "KubernetesProductionExecutorPolicy",
    "KubernetesProductionExecutorPolicyError",
    "KubernetesProductionExecutorResponseError",
    "KubernetesProductionOutcomeIndeterminateError",
]
