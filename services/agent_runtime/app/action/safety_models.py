from datetime import UTC, datetime, timedelta
from enum import Enum
from re import fullmatch
from typing import Any, Literal
from uuid import UUID, uuid4

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


class ActionSafetyContractError(ValueError):
    """The approved Action does not match its production safety contract."""


class KubernetesWorkloadKind(str, Enum):
    """Workload kinds enabled by the first production remediation pilot."""

    DEPLOYMENT = "Deployment"


_DNS_LABEL_PATTERN = r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?"
_CLUSTER_PATTERN = r"[A-Za-z0-9](?:[A-Za-z0-9_.:-]{0,126}[A-Za-z0-9])?"
_MEMORY_QUANTITY_PATTERN = r"([1-9][0-9]*)(Mi|Gi)"
_SHA256_PATTERN = r"[0-9a-f]{64}"
_MAX_CONTRACT_TTL = timedelta(minutes=15)
_MAX_DRY_RUN_AGE = timedelta(minutes=5)
_MAX_FUTURE_SKEW = timedelta(seconds=30)


def _normalize_required_text(
    value: Any,
    *,
    label: str,
    max_length: int,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")

    normalized = value.strip()

    if not normalized:
        raise ValueError(f"{label} cannot be empty")

    if normalized != value:
        raise ValueError(f"{label} cannot contain surrounding whitespace")

    if len(normalized) > max_length:
        raise ValueError(f"{label} is too long")

    return normalized


def _normalize_utc_datetime(value: Any, *, label: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                f"{label} must be an ISO-8601 datetime"
            ) from exc

    if not isinstance(value, datetime):
        raise ValueError(f"{label} must be a datetime")

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")

    return value.astimezone(UTC)


def _validate_dns_label(value: Any, *, label: str) -> str:
    normalized = _normalize_required_text(
        value,
        label=label,
        max_length=63,
    )

    if fullmatch(_DNS_LABEL_PATTERN, normalized) is None:
        raise ValueError(f"{label} must be a Kubernetes DNS label")

    return normalized


def _validate_dns_subdomain(value: Any, *, label: str) -> str:
    normalized = _normalize_required_text(
        value,
        label=label,
        max_length=253,
    )
    labels = normalized.split(".")

    if any(fullmatch(_DNS_LABEL_PATTERN, item) is None for item in labels):
        raise ValueError(f"{label} must be a Kubernetes DNS subdomain")

    return normalized


def memory_quantity_bytes(value: str) -> int:
    """Parse the deliberately small Mi/Gi quantity subset used by Pilot v1."""

    match = fullmatch(_MEMORY_QUANTITY_PATTERN, value)

    if match is None:
        raise ValueError("Pilot memory quantity must use an integer Mi or Gi")

    amount = int(match.group(1))
    multiplier = 2**20 if match.group(2) == "Mi" else 2**30

    return amount * multiplier


class KubernetesWorkloadScope(BaseModel):
    """Exact trusted mutation target; no field may be supplied by the LLM."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    cluster: str
    namespace: str
    kind: KubernetesWorkloadKind = KubernetesWorkloadKind.DEPLOYMENT
    name: str
    container: str

    @field_validator("cluster", mode="before")
    @classmethod
    def validate_cluster(cls, value: Any) -> str:
        normalized = _normalize_required_text(
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
        return _validate_dns_label(
            value,
            label="Kubernetes namespace",
        )

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: Any) -> str:
        return _validate_dns_subdomain(
            value,
            label="Kubernetes workload name",
        )

    @field_validator("container", mode="before")
    @classmethod
    def validate_container(cls, value: Any) -> str:
        return _validate_dns_label(
            value,
            label="Kubernetes container name",
        )


class KubernetesMutationPrecondition(BaseModel):
    """Object identity and optimistic-concurrency state read before approval."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    workload_uid: UUID
    resource_version: str
    generation: int = Field(ge=1)

    @field_validator("resource_version", mode="before")
    @classmethod
    def validate_resource_version(cls, value: Any) -> str:
        return _normalize_required_text(
            value,
            label="Kubernetes resourceVersion",
            max_length=128,
        )


class MemoryLimitChange(BaseModel):
    """Exact bounded memory mutation and its deterministic rollback value."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    current_limit: str
    desired_limit: str
    max_increase_percent: int = Field(
        default=25,
        ge=1,
        le=25,
    )

    @field_validator("current_limit", "desired_limit", mode="before")
    @classmethod
    def validate_quantity_text(cls, value: Any) -> str:
        normalized = _normalize_required_text(
            value,
            label="Memory limit",
            max_length=32,
        )
        memory_quantity_bytes(normalized)
        return normalized

    @model_validator(mode="after")
    def validate_bounded_increase(self) -> "MemoryLimitChange":
        current = self.current_limit_bytes
        desired = self.desired_limit_bytes

        if desired <= current:
            raise ValueError("Desired memory limit must exceed current limit")

        if (desired - current) * 100 > current * self.max_increase_percent:
            raise ValueError("Desired memory limit exceeds the approved increase")

        return self

    @property
    def current_limit_bytes(self) -> int:
        return memory_quantity_bytes(self.current_limit)

    @property
    def desired_limit_bytes(self) -> int:
        return memory_quantity_bytes(self.desired_limit)

    @property
    def rollback_limit(self) -> str:
        return self.current_limit

    @property
    def increase_percent(self) -> float:
        return (
            (self.desired_limit_bytes - self.current_limit_bytes)
            / self.current_limit_bytes
            * 100.0
        )


class KubernetesServerDryRunProof(BaseModel):
    """Bounded proof returned by Kubernetes dryRun=All for the exact patch."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    server_dry_run: Literal[True] = True
    validated_at: datetime
    workload_uid: UUID
    resource_version: str
    generation: int = Field(ge=1)
    patch_sha256: str = Field(pattern=_SHA256_PATTERN)
    field_manager: str = "ai-reliability-platform"
    warnings: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("validated_at", mode="before")
    @classmethod
    def validate_time(cls, value: Any) -> datetime:
        return _normalize_utc_datetime(
            value,
            label="Kubernetes dry-run timestamp",
        )

    @field_validator("resource_version", mode="before")
    @classmethod
    def validate_resource_version(cls, value: Any) -> str:
        return _normalize_required_text(
            value,
            label="Kubernetes dry-run resourceVersion",
            max_length=128,
        )

    @field_validator("patch_sha256", mode="before")
    @classmethod
    def normalize_patch_digest(cls, value: Any) -> str:
        normalized = _normalize_required_text(
            value,
            label="Kubernetes patch digest",
            max_length=64,
        ).lower()

        if fullmatch(_SHA256_PATTERN, normalized) is None:
            raise ValueError("Kubernetes patch digest must be SHA-256")

        return normalized

    @field_validator("field_manager", mode="before")
    @classmethod
    def validate_field_manager(cls, value: Any) -> str:
        return _normalize_required_text(
            value,
            label="Kubernetes field manager",
            max_length=128,
        )

    @field_validator("warnings", mode="before")
    @classmethod
    def validate_warnings(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return tuple()

        if isinstance(value, (str, bytes)):
            raise ValueError("Kubernetes dry-run warnings must be a collection")

        try:
            warnings = tuple(value)
        except TypeError:
            raise ValueError(
                "Kubernetes dry-run warnings must be a collection"
            ) from None

        if len(warnings) > 20:
            raise ValueError("Kubernetes dry-run returned too many warnings")

        return tuple(
            _normalize_required_text(
                item,
                label="Kubernetes dry-run warning",
                max_length=1000,
            )
            for item in warnings
        )


class ProductionActionSafetyContract(BaseModel):
    """
    Immutable approval and execution boundary for OOMKilled Pilot v1.

    The contract is created only from trusted Kubernetes preflight evidence.
    It binds one Incident, one Deployment container, one exact memory patch,
    one resourceVersion and one successful server-side dry-run proof.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    contract_id: UUID = Field(default_factory=uuid4)
    incident_id: UUID
    action_type: ActionType = ActionType.INCREASE_MEMORY_LIMIT
    scope: KubernetesWorkloadScope
    precondition: KubernetesMutationPrecondition
    memory: MemoryLimitChange
    dry_run: KubernetesServerDryRunProof
    required_approval: Literal[True] = True
    policy_version: str = "oom-memory-increase-v1"
    prepared_at: datetime
    expires_at: datetime

    @field_validator("policy_version", mode="before")
    @classmethod
    def validate_policy_version(cls, value: Any) -> str:
        return _normalize_required_text(
            value,
            label="Action safety policy version",
            max_length=64,
        )

    @field_validator("prepared_at", "expires_at", mode="before")
    @classmethod
    def validate_contract_time(cls, value: Any, info) -> datetime:
        return _normalize_utc_datetime(
            value,
            label=info.field_name,
        )

    @model_validator(mode="after")
    def validate_contract_integrity(self) -> "ProductionActionSafetyContract":
        if self.action_type != ActionType.INCREASE_MEMORY_LIMIT:
            raise ValueError(
                "OOMKilled Pilot only allows increase_memory_limit"
            )

        if self.expires_at <= self.prepared_at:
            raise ValueError("Action safety contract must expire after preparation")

        if self.expires_at - self.prepared_at > _MAX_CONTRACT_TTL:
            raise ValueError("Action safety contract lifetime exceeds 15 minutes")

        if self.dry_run.validated_at > self.prepared_at + _MAX_FUTURE_SKEW:
            raise ValueError("Kubernetes dry-run proof is from the future")

        if self.prepared_at - self.dry_run.validated_at > _MAX_DRY_RUN_AGE:
            raise ValueError("Kubernetes dry-run proof is stale")

        if self.dry_run.workload_uid != self.precondition.workload_uid:
            raise ValueError("Dry-run workload UID does not match precondition")

        if self.dry_run.resource_version != self.precondition.resource_version:
            raise ValueError(
                "Dry-run resourceVersion does not match precondition"
            )

        if self.dry_run.generation != self.precondition.generation:
            raise ValueError("Dry-run generation does not match precondition")

        return self

    def is_expired(self, at: datetime | None = None) -> bool:
        checked_at = _normalize_utc_datetime(
            at if at is not None else datetime.now(UTC),
            label="Action safety contract check time",
        )
        return checked_at >= self.expires_at

    def bind_plan(self, plan: ActionPlan) -> ActionPlan:
        """Return an unapproved plan bound to this exact trusted contract."""

        self._require_matching_scope(plan)

        if plan.approved:
            raise ActionSafetyContractError(
                "A safety contract must be bound before approval"
            )

        metadata = dict(plan.metadata)
        metadata.update(
            {
                "safety_contract_id": str(self.contract_id),
                "safety_policy_version": self.policy_version,
                "safety_patch_sha256": self.dry_run.patch_sha256,
                "container": self.scope.container,
                "current_memory_limit": self.memory.current_limit,
                "desired_memory_limit": self.memory.desired_limit,
                "rollback_memory_limit": self.memory.rollback_limit,
                "approval_required": True,
            }
        )

        bound = plan.model_copy(deep=True)
        bound.metadata = metadata
        return bound

    def require_executable_plan(
        self,
        plan: ActionPlan,
        *,
        at: datetime | None = None,
    ) -> None:
        """Fail closed unless an approved plan matches every contract field."""

        if self.is_expired(at):
            raise ActionSafetyContractError("Action safety contract has expired")

        if not plan.approved:
            raise ActionSafetyContractError("Action plan is not approved")

        self._require_matching_scope(plan)

        expected_metadata = {
            "safety_contract_id": str(self.contract_id),
            "safety_policy_version": self.policy_version,
            "safety_patch_sha256": self.dry_run.patch_sha256,
            "container": self.scope.container,
            "current_memory_limit": self.memory.current_limit,
            "desired_memory_limit": self.memory.desired_limit,
            "rollback_memory_limit": self.memory.rollback_limit,
            "approval_required": True,
        }

        for key, expected in expected_metadata.items():
            if plan.metadata.get(key) != expected:
                raise ActionSafetyContractError(
                    f"Action plan does not match safety contract field: {key}"
                )

    def _require_matching_scope(self, plan: ActionPlan) -> None:
        if not isinstance(plan, ActionPlan):
            raise ActionSafetyContractError("Safety contract requires ActionPlan")

        mismatches = []

        if plan.type != self.action_type:
            mismatches.append("action_type")

        if plan.risk != ActionRisk.MEDIUM:
            mismatches.append("risk")

        if plan.target != self.scope.name:
            mismatches.append("workload_name")

        if plan.namespace != self.scope.namespace:
            mismatches.append("namespace")

        if plan.cluster != self.scope.cluster:
            mismatches.append("cluster")

        if mismatches:
            raise ActionSafetyContractError(
                "Action plan does not match trusted safety scope: "
                + ", ".join(mismatches)
            )


__all__ = [
    "ActionSafetyContractError",
    "KubernetesMutationPrecondition",
    "KubernetesServerDryRunProof",
    "KubernetesWorkloadKind",
    "KubernetesWorkloadScope",
    "MemoryLimitChange",
    "ProductionActionSafetyContract",
    "memory_quantity_bytes",
]
