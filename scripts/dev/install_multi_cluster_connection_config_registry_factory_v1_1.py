from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "multi-cluster-connection-config-registry-factory-v1.1"

AFTER_NAME = (
    "multi_cluster_connection_config_registry_factory_v1_1_after.txt"
)

ERROR_NAME = (
    "multi_cluster_connection_config_registry_factory_v1_1_error.txt"
)

EXPECTED_HASHES = {'packages/common/src/common/config/settings.py': '012e7ca965b4fc3787310f598dad8140b5521180330742846adc020ba0344979', 'services/agent_runtime/app/runtime/runtime.py': '3d6ddeed9a0acd876b267dc3f9fae2938fe950a4b4fba5650e4fbfeb083e7008', 'services/agent_runtime/app/tools/kubernetes/router.py': '8a17dd6a6aede00a86ac7075320fdfea5b922215338f36100c1c9be65e611539', 'services/agent_runtime/app/tools/kubernetes/tool.py': '3dd38be804104039cf4becc544f9d38ae70fb9207214bddfe82ccb59b0733095'}

SETTINGS_SOURCE = 'from collections.abc import Mapping\nfrom datetime import UTC, datetime\nfrom functools import lru_cache\nfrom pathlib import Path\nfrom re import fullmatch\nfrom typing import Any\nfrom urllib.parse import urlparse\n\nimport yaml\n\nfrom pydantic import (\n    BaseModel,\n    ConfigDict,\n    Field,\n    field_validator,\n    model_validator,\n)\n\n\n_PROVIDER_NAME_PATTERN = (\n    r"[a-z][a-z0-9_.-]{0,127}"\n)\n\n\n_ENVIRONMENT_NAME_PATTERN = (\n    r"[A-Z][A-Z0-9_]{2,127}"\n)\n\n\n_ALLOWED_OPERATOR_ROLES = frozenset(\n    {\n        "viewer",\n        "analyst",\n        "approver",\n        "executor",\n        "reconciler",\n        "admin",\n        "service",\n    }\n)\n\n\n_SENSITIVE_ATTRIBUTE_FRAGMENTS = (\n    "authorization",\n    "credential",\n    "password",\n    "secret",\n    "token",\n    "api_key",\n    "apikey",\n    "private_key",\n)\n\n\nclass AppConfig(BaseModel):\n    """\n    Application configuration.\n    """\n\n    name: str\n\n    version: str\n\n\nclass RateLimitConfig(BaseModel):\n    """\n    LLM gateway rate limit configuration.\n    """\n\n    enabled: bool = True\n\n    requests_per_minute: int = 60\n\n\nclass LLMGatewayConfig(BaseModel):\n    """\n    LLM Gateway reliability configuration.\n\n    Controls:\n\n    - fallback\n    - retry\n    - timeout\n    - rate limit\n    """\n\n    fallback_enabled: bool = True\n\n    retry_attempts: int = 3\n\n    request_timeout: int = 30\n\n    rate_limit: RateLimitConfig = Field(\n        default_factory=RateLimitConfig\n    )\n\n\nclass LLMConfig(BaseModel):\n    """\n    LLM configuration.\n    """\n\n    provider: str\n\n    temperature: float\n\n    timeout: int\n\n    gateway: LLMGatewayConfig = Field(\n        default_factory=LLMGatewayConfig\n    )\n\n\nclass RuntimeConfig(BaseModel):\n    """\n    Agent runtime configuration.\n    """\n\n    pipeline: str\n\n    max_workers: int\n\n\nclass KubernetesPreflightTargetConfig(BaseModel):\n    """One exact Deployment container allowed by OOMKilled Pilot v1."""\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    cluster: str = Field(min_length=1, max_length=128)\n    namespace: str = Field(min_length=1, max_length=63)\n    deployment: str = Field(min_length=1, max_length=253)\n    container: str = Field(min_length=1, max_length=63)\n\n    @field_validator(\n        "cluster",\n        "namespace",\n        "deployment",\n        "container",\n        mode="before",\n    )\n    @classmethod\n    def validate_exact_target_text(\n        cls,\n        value: Any,\n        info,\n    ) -> str:\n        if (\n            not isinstance(value, str)\n            or not value\n            or value != value.strip()\n        ):\n            raise ValueError(\n                "Kubernetes preflight target fields must be exact text"\n            )\n\n        if info.field_name == "cluster":\n            pattern = r"[A-Za-z0-9](?:[A-Za-z0-9_.:-]{0,126}[A-Za-z0-9])?"\n        elif info.field_name == "deployment":\n            label = r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?"\n            pattern = rf"{label}(?:\\.{label})*"\n        else:\n            pattern = r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?"\n\n        if fullmatch(pattern, value) is None:\n            raise ValueError(\n                "Kubernetes preflight target identifier is invalid"\n            )\n\n        return value\n\n\nclass KubernetesPreflightConfig(BaseModel):\n    """\n    Fail-closed production configuration for trusted Kubernetes preflight.\n\n    The configuration stores only a credential environment-variable name or\n    token-file path. The bearer token itself must never be serialized here.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    enabled: bool = False\n    api_url: str | None = None\n    cluster_name: str | None = None\n    bearer_token_env: str | None = None\n    bearer_token_file: str | None = None\n    ca_file: str | None = None\n    allowed_targets: tuple[\n        KubernetesPreflightTargetConfig,\n        ...,\n    ] = Field(default_factory=tuple)\n    increase_percent: int = Field(default=25, ge=1, le=25)\n    contract_ttl_seconds: int = Field(default=600, ge=60, le=900)\n    request_timeout_seconds: float = Field(default=5.0, gt=0, le=30)\n    field_manager: str = "ai-reliability-platform"\n    policy_version: str = "oom-memory-increase-v1"\n\n    @field_validator("api_url", mode="before")\n    @classmethod\n    def validate_api_url(cls, value: Any) -> str | None:\n        if value is None:\n            return None\n        if (\n            not isinstance(value, str)\n            or not value\n            or value != value.strip()\n        ):\n            raise ValueError("Kubernetes API URL is invalid")\n\n        normalized = value.rstrip("/")\n        parsed = urlparse(normalized)\n        if (\n            parsed.scheme != "https"\n            or not parsed.netloc\n            or parsed.username is not None\n            or parsed.password is not None\n            or parsed.query\n            or parsed.fragment\n            or parsed.path not in {"", "/"}\n        ):\n            raise ValueError("Kubernetes API URL must be a clean HTTPS origin")\n\n        return normalized\n\n    @field_validator("cluster_name", mode="before")\n    @classmethod\n    def validate_cluster_name(cls, value: Any) -> str | None:\n        if value is None:\n            return None\n        if (\n            not isinstance(value, str)\n            or value != value.strip()\n            or fullmatch(\n                r"[A-Za-z0-9](?:[A-Za-z0-9_.:-]{0,126}[A-Za-z0-9])?",\n                value,\n            )\n            is None\n        ):\n            raise ValueError("Kubernetes cluster name is invalid")\n        return value\n\n    @field_validator("bearer_token_env", mode="before")\n    @classmethod\n    def validate_bearer_token_env(cls, value: Any) -> str | None:\n        if value is None:\n            return None\n        if (\n            not isinstance(value, str)\n            or value != value.strip()\n            or fullmatch(_ENVIRONMENT_NAME_PATTERN, value) is None\n        ):\n            raise ValueError(\n                "Kubernetes bearer-token environment name is invalid"\n            )\n        return value\n\n    @field_validator("bearer_token_file", "ca_file", mode="before")\n    @classmethod\n    def validate_file_reference(\n        cls,\n        value: Any,\n        info,\n    ) -> str | None:\n        if value is None:\n            return None\n        if (\n            not isinstance(value, str)\n            or not value\n            or value != value.strip()\n            or "\\x00" in value\n            or len(value) > 4096\n        ):\n            raise ValueError(\n                f"Kubernetes {info.field_name} reference is invalid"\n            )\n        return value\n\n    @field_validator("field_manager", mode="before")\n    @classmethod\n    def validate_field_manager(cls, value: Any) -> str:\n        if (\n            not isinstance(value, str)\n            or value != value.strip()\n            or fullmatch(\n                r"[A-Za-z0-9](?:[A-Za-z0-9_.:/-]{0,126}[A-Za-z0-9])?",\n                value,\n            )\n            is None\n        ):\n            raise ValueError("Kubernetes field manager is invalid")\n        return value\n\n    @field_validator("policy_version", mode="before")\n    @classmethod\n    def validate_policy_version(cls, value: Any) -> str:\n        if (\n            not isinstance(value, str)\n            or not value\n            or value != value.strip()\n            or len(value) > 64\n        ):\n            raise ValueError("Kubernetes preflight policy version is invalid")\n        return value\n\n    @model_validator(mode="after")\n    def validate_enabled_boundary(self) -> "KubernetesPreflightConfig":\n        credential_references = sum(\n            item is not None\n            for item in (\n                self.bearer_token_env,\n                self.bearer_token_file,\n            )\n        )\n\n        if self.enabled:\n            if self.api_url is None or self.cluster_name is None:\n                raise ValueError(\n                    "Enabled Kubernetes preflight requires API URL and cluster"\n                )\n            if credential_references != 1:\n                raise ValueError(\n                    "Enabled Kubernetes preflight requires exactly one token source"\n                )\n            if not self.allowed_targets:\n                raise ValueError(\n                    "Enabled Kubernetes preflight requires an exact allowlist"\n                )\n\n        target_keys = [\n            (\n                item.cluster,\n                item.namespace,\n                item.deployment,\n                item.container,\n            )\n            for item in self.allowed_targets\n        ]\n        if len(target_keys) != len(set(target_keys)):\n            raise ValueError(\n                "Kubernetes preflight allowlist targets must be unique"\n            )\n\n        if self.cluster_name is not None and any(\n            item.cluster != self.cluster_name\n            for item in self.allowed_targets\n        ):\n            raise ValueError(\n                "Kubernetes preflight target cluster does not match connection"\n            )\n\n        return self\n\n\nKUBERNETES_PRODUCTION_WRITE_ACKNOWLEDGEMENT = (\n    "I_UNDERSTAND_THIS_ENABLES_REAL_KUBERNETES_WRITES"\n)\n\n\nclass KubernetesProductionExecutionConfig(BaseModel):\n    """\n    Fail-closed feature gate for the OOMKilled Pilot production write path.\n\n    The execution identity is deliberately separate from the read/dry-run\n    preflight identity. Configuration contains only an environment-variable\n    name or token-file path and never serializes the credential itself.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    enabled: bool = False\n    write_acknowledgement: str | None = None\n    bearer_token_env: str | None = None\n    bearer_token_file: str | None = None\n    request_timeout_seconds: float = Field(\n        default=5.0,\n        gt=0,\n        le=30,\n    )\n    minimum_remaining_seconds: int = Field(\n        default=5,\n        ge=1,\n        le=60,\n    )\n\n    @field_validator("write_acknowledgement", mode="before")\n    @classmethod\n    def validate_write_acknowledgement(\n        cls,\n        value: Any,\n    ) -> str | None:\n        if value is None:\n            return None\n        if (\n            not isinstance(value, str)\n            or not value\n            or value != value.strip()\n            or len(value) > 128\n        ):\n            raise ValueError(\n                "Kubernetes production write acknowledgement is invalid"\n            )\n        return value\n\n    @field_validator("bearer_token_env", mode="before")\n    @classmethod\n    def validate_bearer_token_env(\n        cls,\n        value: Any,\n    ) -> str | None:\n        if value is None:\n            return None\n        if (\n            not isinstance(value, str)\n            or value != value.strip()\n            or fullmatch(_ENVIRONMENT_NAME_PATTERN, value) is None\n        ):\n            raise ValueError(\n                "Kubernetes production bearer-token environment name "\n                "is invalid"\n            )\n        return value\n\n    @field_validator("bearer_token_file", mode="before")\n    @classmethod\n    def validate_bearer_token_file(\n        cls,\n        value: Any,\n    ) -> str | None:\n        if value is None:\n            return None\n        if (\n            not isinstance(value, str)\n            or not value\n            or value != value.strip()\n            or "\\x00" in value\n            or len(value) > 4096\n        ):\n            raise ValueError(\n                "Kubernetes production bearer-token file reference "\n                "is invalid"\n            )\n        return value\n\n    @model_validator(mode="after")\n    def validate_enabled_boundary(\n        self,\n    ) -> "KubernetesProductionExecutionConfig":\n        credential_references = sum(\n            item is not None\n            for item in (\n                self.bearer_token_env,\n                self.bearer_token_file,\n            )\n        )\n\n        if self.enabled:\n            if (\n                self.write_acknowledgement\n                != KUBERNETES_PRODUCTION_WRITE_ACKNOWLEDGEMENT\n            ):\n                raise ValueError(\n                    "Enabled Kubernetes production execution requires the "\n                    "exact write acknowledgement"\n                )\n            if credential_references != 1:\n                raise ValueError(\n                    "Enabled Kubernetes production execution requires "\n                    "exactly one token source"\n                )\n\n        return self\n\n\nclass RemediationConfig(BaseModel):\n    """Production remediation configuration root."""\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    kubernetes_preflight: KubernetesPreflightConfig = Field(\n        default_factory=KubernetesPreflightConfig\n    )\n\n    kubernetes_production_execution: (\n        KubernetesProductionExecutionConfig\n    ) = Field(\n        default_factory=(\n            KubernetesProductionExecutionConfig\n        )\n    )\n\n    @model_validator(mode="after")\n    def validate_production_execution_boundary(\n        self,\n    ) -> "RemediationConfig":\n        execution = self.kubernetes_production_execution\n        preflight = self.kubernetes_preflight\n\n        if not execution.enabled:\n            return self\n\n        if not preflight.enabled:\n            raise ValueError(\n                "Kubernetes production execution requires enabled preflight"\n            )\n\n        execution_reference = (\n            ("env", execution.bearer_token_env)\n            if execution.bearer_token_env is not None\n            else ("file", execution.bearer_token_file)\n        )\n        preflight_reference = (\n            ("env", preflight.bearer_token_env)\n            if preflight.bearer_token_env is not None\n            else ("file", preflight.bearer_token_file)\n        )\n\n        if execution_reference == preflight_reference:\n            raise ValueError(\n                "Kubernetes production execution must use a credential "\n                "reference separate from preflight"\n            )\n\n        return self\n\n\nclass KubernetesReadClusterConfig(BaseModel):\n    """\n    One exact read-only Kubernetes connection descriptor.\n\n    This model stores credential references only. The bearer token value is\n    resolved by the Agent Runtime factory and must never be serialized into\n    Settings or app.yaml.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    cluster_name: str\n    api_url: str\n    bearer_token_env: str | None = None\n    bearer_token_file: str | None = None\n    ca_file: str | None = None\n    request_timeout_seconds: float = Field(\n        default=5.0,\n        gt=0,\n        le=30,\n    )\n\n    @field_validator(\n        "cluster_name",\n        mode="before",\n    )\n    @classmethod\n    def validate_cluster_name(\n        cls,\n        value: Any,\n    ) -> str:\n        if (\n            not isinstance(\n                value,\n                str,\n            )\n            or value != value.strip()\n            or fullmatch(\n                r"[A-Za-z0-9](?:[A-Za-z0-9_.:-]{0,126}[A-Za-z0-9])?",\n                value,\n            )\n            is None\n        ):\n            raise ValueError(\n                "Kubernetes read cluster name is invalid"\n            )\n\n        return value\n\n    @field_validator(\n        "api_url",\n        mode="before",\n    )\n    @classmethod\n    def validate_api_url(\n        cls,\n        value: Any,\n    ) -> str:\n        if (\n            not isinstance(\n                value,\n                str,\n            )\n            or not value\n            or value != value.strip()\n        ):\n            raise ValueError(\n                "Kubernetes read API URL is invalid"\n            )\n\n        normalized = value.rstrip(\n            "/"\n        )\n\n        parsed = urlparse(\n            normalized\n        )\n\n        if (\n            parsed.scheme != "https"\n            or not parsed.netloc\n            or parsed.username is not None\n            or parsed.password is not None\n            or parsed.query\n            or parsed.fragment\n            or parsed.path not in {\n                "",\n                "/",\n            }\n        ):\n            raise ValueError(\n                "Kubernetes read API URL must be a clean HTTPS origin"\n            )\n\n        return normalized\n\n    @field_validator(\n        "bearer_token_env",\n        mode="before",\n    )\n    @classmethod\n    def validate_bearer_token_env(\n        cls,\n        value: Any,\n    ) -> str | None:\n        if value is None:\n            return None\n\n        if (\n            not isinstance(\n                value,\n                str,\n            )\n            or value != value.strip()\n            or fullmatch(\n                _ENVIRONMENT_NAME_PATTERN,\n                value,\n            )\n            is None\n        ):\n            raise ValueError(\n                "Kubernetes read bearer-token environment name is invalid"\n            )\n\n        return value\n\n    @field_validator(\n        "bearer_token_file",\n        "ca_file",\n        mode="before",\n    )\n    @classmethod\n    def validate_file_reference(\n        cls,\n        value: Any,\n        info,\n    ) -> str | None:\n        if value is None:\n            return None\n\n        if (\n            not isinstance(\n                value,\n                str,\n            )\n            or not value\n            or value != value.strip()\n            or "\\x00" in value\n            or len(\n                value\n            )\n            > 4096\n        ):\n            raise ValueError(\n                f"Kubernetes read {info.field_name} reference is invalid"\n            )\n\n        return value\n\n    @model_validator(\n        mode="after"\n    )\n    def validate_credential_reference(\n        self,\n    ) -> "KubernetesReadClusterConfig":\n        references = sum(\n            item is not None\n            for item in (\n                self.bearer_token_env,\n                self.bearer_token_file,\n            )\n        )\n\n        if references != 1:\n            raise ValueError(\n                "Kubernetes read cluster requires exactly one token source"\n            )\n\n        return self\n\n\nclass KubernetesReadMultiClusterConfig(BaseModel):\n    """\n    Disabled-by-default read-only multi-cluster connection configuration.\n\n    The configuration is immutable and bounded. Enabling it does not itself\n    contact Kubernetes; the connection factory only resolves local credential\n    references and constructs cluster-bound read-only Tool objects.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    enabled: bool = False\n\n    clusters: tuple[\n        KubernetesReadClusterConfig,\n        ...,\n    ] = Field(\n        default_factory=tuple,\n        max_length=64,\n    )\n\n    @model_validator(\n        mode="after"\n    )\n    def validate_cluster_set(\n        self,\n    ) -> "KubernetesReadMultiClusterConfig":\n        if (\n            self.enabled\n            and not self.clusters\n        ):\n            raise ValueError(\n                "Enabled Kubernetes read multi-cluster configuration requires at least one cluster"\n            )\n\n        cluster_names = [\n            item.cluster_name\n            for item in self.clusters\n        ]\n\n        api_urls = [\n            item.api_url\n            for item in self.clusters\n        ]\n\n        credential_references = [\n            (\n                (\n                    "env",\n                    item.bearer_token_env,\n                )\n                if item.bearer_token_env\n                is not None\n                else (\n                    "file",\n                    item.bearer_token_file,\n                )\n            )\n            for item in self.clusters\n        ]\n\n        if len(\n            cluster_names\n        ) != len(\n            set(\n                cluster_names\n            )\n        ):\n            raise ValueError(\n                "Kubernetes read cluster names must be unique"\n            )\n\n        if len(\n            api_urls\n        ) != len(\n            set(\n                api_urls\n            )\n        ):\n            raise ValueError(\n                "Kubernetes read API URLs must be unique"\n            )\n\n        if len(\n            credential_references\n        ) != len(\n            set(\n                credential_references\n            )\n        ):\n            raise ValueError(\n                "Kubernetes read clusters must use distinct credential references"\n            )\n\n        return self\n\n\nclass ConnectionsConfig(BaseModel):\n    """\n    Non-remediation infrastructure connection configuration.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    kubernetes_read: (\n        KubernetesReadMultiClusterConfig\n    ) = Field(\n        default_factory=(\n            KubernetesReadMultiClusterConfig\n        )\n    )\n\n\nclass AuthenticationApiKeyConfig(BaseModel):\n    """\n    Non-secret API key identity configuration.\n\n    secret_env stores only the name of an environment variable. The API key\n    value must never be written to app.yaml or serialized as Settings data.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    key_id: str = Field(\n        min_length=1,\n        max_length=128,\n    )\n\n    secret_env: str = Field(\n        min_length=3,\n        max_length=128,\n    )\n\n    principal_id: str = Field(\n        min_length=1,\n        max_length=128,\n    )\n\n    roles: frozenset[str] = Field(\n        min_length=1,\n    )\n\n    display_name: str | None = Field(\n        default=None,\n        max_length=256,\n    )\n\n    active: bool = True\n\n    expires_at: datetime | None = None\n\n    attributes: dict[str, Any] = Field(\n        default_factory=dict,\n    )\n\n    @field_validator(\n        "key_id",\n        "principal_id",\n        mode="before",\n    )\n    @classmethod\n    def normalize_required_text(\n        cls,\n        value: Any,\n    ) -> str:\n        if not isinstance(\n            value,\n            str,\n        ):\n            raise ValueError(\n                "Authentication identity fields must be text"\n            )\n\n        normalized = value.strip()\n\n        if not normalized:\n            raise ValueError(\n                "Authentication identity fields cannot be empty"\n            )\n\n        return normalized\n\n    @field_validator(\n        "secret_env",\n        mode="before",\n    )\n    @classmethod\n    def validate_secret_environment_name(\n        cls,\n        value: Any,\n    ) -> str:\n        if (\n            not isinstance(\n                value,\n                str,\n            )\n            or value != value.strip()\n            or fullmatch(\n                _ENVIRONMENT_NAME_PATTERN,\n                value,\n            )\n            is None\n        ):\n            raise ValueError(\n                "API key secret environment name is invalid"\n            )\n\n        return value\n\n    @field_validator(\n        "roles",\n        mode="before",\n    )\n    @classmethod\n    def normalize_roles(\n        cls,\n        value: Any,\n    ) -> frozenset[str]:\n        if (\n            isinstance(\n                value,\n                str,\n            )\n            or value is None\n        ):\n            raise ValueError(\n                "Authentication roles must be a collection"\n            )\n\n        try:\n            normalized_roles = frozenset(\n                str(\n                    getattr(\n                        role,\n                        "value",\n                        role,\n                    )\n                )\n                .strip()\n                .lower()\n                for role in value\n            )\n        except TypeError:\n            raise ValueError(\n                "Authentication roles must be a collection"\n            ) from None\n\n        if not normalized_roles:\n            raise ValueError(\n                "At least one authentication role is required"\n            )\n\n        unknown_roles = (\n            normalized_roles\n            - _ALLOWED_OPERATOR_ROLES\n        )\n\n        if unknown_roles:\n            raise ValueError(\n                "Authentication configuration contains an unknown role"\n            )\n\n        return normalized_roles\n\n    @field_validator(\n        "display_name",\n        mode="before",\n    )\n    @classmethod\n    def normalize_optional_text(\n        cls,\n        value: Any,\n    ) -> str | None:\n        if value is None:\n            return None\n\n        if not isinstance(\n            value,\n            str,\n        ):\n            raise ValueError(\n                "Authentication display name must be text"\n            )\n\n        normalized = value.strip()\n\n        return normalized or None\n\n    @field_validator(\n        "expires_at",\n        mode="after",\n    )\n    @classmethod\n    def require_timezone_aware_expiry(\n        cls,\n        value: datetime | None,\n    ) -> datetime | None:\n        if value is None:\n            return None\n\n        if (\n            value.tzinfo is None\n            or value.utcoffset() is None\n        ):\n            raise ValueError(\n                "API key expiry must be timezone-aware"\n            )\n\n        return value.astimezone(\n            UTC\n        )\n\n    @field_validator(\n        "attributes",\n        mode="before",\n    )\n    @classmethod\n    def reject_secret_attributes(\n        cls,\n        value: Any,\n    ) -> dict[str, Any]:\n        if value is None:\n            return {}\n\n        if not isinstance(\n            value,\n            Mapping,\n        ):\n            raise ValueError(\n                "Authentication attributes must be a mapping"\n            )\n\n        attributes = dict(\n            value\n        )\n\n        for key in attributes:\n            normalized_key = str(\n                key\n            ).strip().lower().replace(\n                "-",\n                "_",\n            )\n\n            if any(\n                fragment in normalized_key\n                for fragment in (\n                    _SENSITIVE_ATTRIBUTE_FRAGMENTS\n                )\n            ):\n                raise ValueError(\n                    "Authentication attributes must not contain "\n                    "credentials or secrets"\n                )\n\n        return attributes\n\n\nclass AuthenticationConfig(BaseModel):\n    """\n    Authentication provider startup configuration.\n\n    Disabled authentication does not mean anonymous access. The factory will\n    create a reject-all provider until authentication is explicitly enabled.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    enabled: bool = False\n\n    default_provider: str = "api_key"\n\n    api_keys: tuple[\n        AuthenticationApiKeyConfig,\n        ...,\n    ] = Field(\n        default_factory=tuple,\n    )\n\n    @field_validator(\n        "default_provider",\n        mode="before",\n    )\n    @classmethod\n    def validate_default_provider(\n        cls,\n        value: Any,\n    ) -> str:\n        if (\n            not isinstance(\n                value,\n                str,\n            )\n            or value != value.strip()\n            or fullmatch(\n                _PROVIDER_NAME_PATTERN,\n                value,\n            )\n            is None\n        ):\n            raise ValueError(\n                "Default authentication provider name is invalid"\n            )\n\n        return value\n\n    @model_validator(\n        mode="after"\n    )\n    def validate_provider_configuration(\n        self,\n    ) -> "AuthenticationConfig":\n        if self.default_provider != "api_key":\n            raise ValueError(\n                "Default authentication provider is not configured"\n            )\n\n        key_ids = [\n            item.key_id\n            for item in self.api_keys\n        ]\n        secret_environment_names = [\n            item.secret_env\n            for item in self.api_keys\n        ]\n\n        if len(\n            key_ids\n        ) != len(\n            set(\n                key_ids\n            )\n        ):\n            raise ValueError(\n                "Authentication API key IDs must be unique"\n            )\n\n        if len(\n            secret_environment_names\n        ) != len(\n            set(\n                secret_environment_names\n            )\n        ):\n            raise ValueError(\n                "Authentication secret environment names must be unique"\n            )\n\n        if self.enabled and not self.api_keys:\n            raise ValueError(\n                "Enabled API key authentication requires at least one key"\n            )\n\n        return self\n\n\nclass SecurityConfig(BaseModel):\n    """Security configuration root."""\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    authentication: AuthenticationConfig = Field(\n        default_factory=AuthenticationConfig\n    )\n\n\nclass Settings(BaseModel):\n    """\n    Root application settings.\n    """\n\n    app: AppConfig\n\n    llm: LLMConfig\n\n    runtime: RuntimeConfig\n\n    security: SecurityConfig = Field(\n        default_factory=SecurityConfig\n    )\n\n    connections: ConnectionsConfig = Field(\n        default_factory=ConnectionsConfig\n    )\n\n    remediation: RemediationConfig = Field(\n        default_factory=RemediationConfig\n    )\n\n\n@lru_cache\ndef get_settings() -> Settings:\n    """\n    Load settings from configs/app.yaml.\n    """\n\n    root = Path(__file__).resolve().parents[5]\n\n    config_path = (\n        root\n        / "configs"\n        / "app.yaml"\n    )\n\n    data = yaml.safe_load(\n        config_path.read_text(\n            encoding="utf-8",\n        )\n    )\n\n    return Settings.model_validate(data)\n'
CONNECTION_FACTORY_SOURCE = 'from __future__ import annotations\n\nfrom collections.abc import (\n    Callable,\n    Mapping,\n)\nfrom os import environ\nfrom pathlib import Path\nfrom typing import Any\n\nfrom common.config import (\n    get_settings,\n)\nfrom common.config.settings import (\n    KubernetesReadClusterConfig,\n    KubernetesReadMultiClusterConfig,\n)\n\nfrom services.agent_runtime.app.tools.kubernetes.router import (\n    KubernetesClusterRegistry,\n    KubernetesClusterRoutingError,\n)\nfrom services.agent_runtime.app.tools.kubernetes.tool import (\n    KubernetesConfigurationError,\n    KubernetesTool,\n)\n\n\nclass KubernetesReadConnectionFactoryConfigurationError(\n    RuntimeError\n):\n    """\n    Read-only multi-cluster Kubernetes connections cannot be assembled safely.\n    """\n\n\n_MAX_TOKEN_FILE_BYTES = (\n    16\n    * 1024\n)\n\n\ndef _resolve_config(\n    config: (\n        KubernetesReadMultiClusterConfig\n        | None\n    ),\n) -> KubernetesReadMultiClusterConfig:\n    resolved = (\n        get_settings()\n        .connections\n        .kubernetes_read\n        if config is None\n        else config\n    )\n\n    if not isinstance(\n        resolved,\n        KubernetesReadMultiClusterConfig,\n    ):\n        raise KubernetesReadConnectionFactoryConfigurationError(\n            "Kubernetes read connection factory requires validated configuration"\n        )\n\n    return resolved\n\n\ndef _resolve_environment(\n    environment: (\n        Mapping[\n            str,\n            str,\n        ]\n        | None\n    ),\n) -> Mapping[\n    str,\n    str,\n]:\n    resolved = (\n        environ\n        if environment is None\n        else environment\n    )\n\n    if not isinstance(\n        resolved,\n        Mapping,\n    ):\n        raise KubernetesReadConnectionFactoryConfigurationError(\n            "Kubernetes read credential source must be a mapping"\n        )\n\n    return resolved\n\n\ndef _default_token_file_reader(\n    path: str,\n) -> str:\n    token_path = Path(\n        path\n    )\n\n    try:\n        if not token_path.is_file():\n            raise KubernetesReadConnectionFactoryConfigurationError(\n                "Kubernetes read token file is unavailable"\n            )\n\n        if (\n            token_path.stat().st_size\n            > _MAX_TOKEN_FILE_BYTES\n        ):\n            raise KubernetesReadConnectionFactoryConfigurationError(\n                "Kubernetes read token file is too large"\n            )\n\n        return token_path.read_text(\n            encoding="utf-8"\n        )\n\n    except KubernetesReadConnectionFactoryConfigurationError:\n        raise\n\n    except OSError:\n        raise KubernetesReadConnectionFactoryConfigurationError(\n            "Kubernetes read token file is unavailable"\n        ) from None\n\n\ndef _validate_token(\n    value: Any,\n) -> str:\n    if not isinstance(\n        value,\n        str,\n    ):\n        raise KubernetesReadConnectionFactoryConfigurationError(\n            "Kubernetes read credential is invalid"\n        )\n\n    normalized = value.rstrip(\n        "\\r\\n"\n    )\n\n    if (\n        not normalized\n        or normalized\n        != normalized.strip()\n        or len(\n            normalized\n        )\n        < 16\n        or len(\n            normalized.encode(\n                "utf-8"\n            )\n        )\n        > _MAX_TOKEN_FILE_BYTES\n        or "\\x00"\n        in normalized\n    ):\n        raise KubernetesReadConnectionFactoryConfigurationError(\n            "Kubernetes read credential is invalid"\n        )\n\n    return normalized\n\n\ndef _load_bearer_token(\n    config: KubernetesReadClusterConfig,\n    *,\n    environment: (\n        Mapping[\n            str,\n            str,\n        ]\n        | None\n    ),\n    token_file_reader: (\n        Callable[\n            [\n                str,\n            ],\n            str,\n        ]\n        | None\n    ),\n) -> str:\n    if (\n        config.bearer_token_env\n        is not None\n    ):\n        source = _resolve_environment(\n            environment\n        )\n\n        value = source.get(\n            config.bearer_token_env\n        )\n\n        if value is None:\n            raise KubernetesReadConnectionFactoryConfigurationError(\n                "Kubernetes read credential environment variable is missing: "\n                + config.bearer_token_env\n            )\n\n        return _validate_token(\n            value\n        )\n\n    if (\n        config.bearer_token_file\n        is not None\n    ):\n        reader = (\n            token_file_reader\n            or _default_token_file_reader\n        )\n\n        try:\n            value = reader(\n                config.bearer_token_file\n            )\n\n        except KubernetesReadConnectionFactoryConfigurationError:\n            raise\n\n        except Exception:\n            raise KubernetesReadConnectionFactoryConfigurationError(\n                "Kubernetes read token file is unavailable"\n            ) from None\n\n        return _validate_token(\n            value\n        )\n\n    raise KubernetesReadConnectionFactoryConfigurationError(\n        "Kubernetes read cluster has no configured credential source"\n    )\n\n\ndef _validate_ca_file(\n    path: str | None,\n) -> str | None:\n    if path is None:\n        return None\n\n    try:\n        if not Path(\n            path\n        ).is_file():\n            raise KubernetesReadConnectionFactoryConfigurationError(\n                "Kubernetes read CA file is unavailable"\n            )\n\n    except OSError:\n        raise KubernetesReadConnectionFactoryConfigurationError(\n            "Kubernetes read CA file is unavailable"\n        ) from None\n\n    return path\n\n\ndef create_kubernetes_cluster_registry(\n    config: (\n        KubernetesReadMultiClusterConfig\n        | None\n    ) = None,\n    *,\n    environment: (\n        Mapping[\n            str,\n            str,\n        ]\n        | None\n    ) = None,\n    token_file_reader: (\n        Callable[\n            [\n                str,\n            ],\n            str,\n        ]\n        | None\n    ) = None,\n) -> KubernetesClusterRegistry | None:\n    """\n    Build read-only multi-cluster connections from validated non-secret config.\n\n    Disabled configuration returns None before reading environment variables,\n    token files, CA files, or constructing any KubernetesTool.\n\n    Enabled configuration resolves local credential references, constructs\n    fail-closed cluster-bound read-only KubernetesTool objects, and returns an\n    immutable KubernetesClusterRegistry. No HTTP request is made here.\n    """\n\n    resolved = _resolve_config(\n        config\n    )\n\n    if not resolved.enabled:\n        return None\n\n    tools = []\n\n    try:\n        for item in resolved.clusters:\n            token = _load_bearer_token(\n                item,\n                environment=environment,\n                token_file_reader=(\n                    token_file_reader\n                ),\n            )\n\n            ca_file = _validate_ca_file(\n                item.ca_file\n            )\n\n            tool = KubernetesTool(\n                api_url=item.api_url,\n                timeout_seconds=(\n                    item.request_timeout_seconds\n                ),\n                verify_tls=True,\n                bearer_token=token,\n                token_file=None,\n                ca_file=ca_file,\n                cluster_name=(\n                    item.cluster_name\n                ),\n                allow_dry_run_fallback=False,\n            )\n\n            if (\n                tool.api_url\n                != item.api_url\n                or tool.cluster_name\n                != item.cluster_name\n                or tool.verify_tls\n                is not True\n                or tool.allow_dry_run_fallback\n                is not False\n            ):\n                raise KubernetesReadConnectionFactoryConfigurationError(\n                    "Kubernetes read Tool did not retain the validated connection boundary"\n                )\n\n            tools.append(\n                tool\n            )\n\n        registry = (\n            KubernetesClusterRegistry(\n                tools\n            )\n        )\n\n    except KubernetesReadConnectionFactoryConfigurationError:\n        raise\n\n    except (\n        KubernetesClusterRoutingError,\n        KubernetesConfigurationError,\n        TypeError,\n        ValueError,\n    ):\n        raise KubernetesReadConnectionFactoryConfigurationError(\n            "Kubernetes read cluster registry configuration is invalid"\n        ) from None\n\n    if (\n        registry.count\n        != len(\n            resolved.clusters\n        )\n    ):\n        raise KubernetesReadConnectionFactoryConfigurationError(\n            "Kubernetes read cluster registry lost configured connections"\n        )\n\n    return registry\n\n\n__all__ = [\n    "KubernetesReadConnectionFactoryConfigurationError",\n    "create_kubernetes_cluster_registry",\n]\n'
RUNTIME_SOURCE = 'from copy import deepcopy\n\nfrom services.agent_runtime.app.registry.factory import (\n    create_agent_registry,\n)\nfrom services.agent_runtime.app.llm.gateway.factory import (\n    create_llm_gateway,\n)\nfrom services.agent_runtime.app.llm.gateway.gateway import (\n    LLMGateway,\n)\nfrom services.agent_runtime.app.planner.agent_planner import (\n    AgentPlanner,\n)\nfrom services.agent_runtime.app.pipeline.planner_pipeline import (\n    PlannerPipeline,\n)\nfrom services.agent_runtime.app.memory.store import (\n    MemoryStore,\n)\nfrom services.agent_runtime.app.tools.factory import (\n    create_tool_manager,\n)\nfrom services.agent_runtime.app.tools.kubernetes.router import (\n    KubernetesClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.kubernetes.connection_factory import (\n    create_kubernetes_cluster_registry,\n)\nfrom services.agent_runtime.app.skills.factory import (\n    create_skill_registry,\n)\nfrom services.agent_runtime.app.mcp.factory import (\n    create_mcp_registry,\n)\nfrom services.agent_runtime.app.observability.collector import (\n    TraceCollector,\n)\nfrom services.agent_runtime.app.evaluation.factory import (\n    create_evaluation_registry,\n)\nfrom services.agent_runtime.app.policy.factory import (\n    create_policy_engine,\n)\nfrom services.agent_runtime.app.approval.service import (\n    ApprovalService,\n)\nfrom services.agent_runtime.app.incident.store import (\n    IncidentStore,\n)\nfrom services.agent_runtime.app.incident.service import (\n    IncidentService,\n)\nfrom services.agent_runtime.app.investigation.comparison import (\n    build_rca_investigation_comparison,\n)\nfrom services.agent_runtime.app.investigation.factory import (\n    create_investigation_coordinator,\n)\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    InvestigationLLMGatewayAdapter,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n    LLMInvestigationReasoner,\n)\nfrom services.agent_runtime.app.investigation.settings import (\n    InvestigationSettings,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationState,\n)\nfrom services.agent_runtime.app.model.context import (\n    AgentContext,\n)\nfrom services.agent_runtime.app.workflow.service import (\n    WorkflowService,\n)\nfrom services.agent_runtime.app.action.execution_service import (\n    ActionExecutionService,\n)\nfrom services.agent_runtime.app.action.execution_store import (\n    ActionExecutionStore,\n)\nfrom services.agent_runtime.app.action.kubernetes_preflight import (\n    KubernetesPreflightResolver,\n)\nfrom services.agent_runtime.app.action.kubernetes_preflight_factory import (\n    create_kubernetes_preflight_resolver,\n)\nfrom services.agent_runtime.app.action.kubernetes_production_executor import (\n    KubernetesProductionExecutor,\n)\nfrom services.agent_runtime.app.action.kubernetes_production_factory import (\n    create_kubernetes_production_executor,\n)\nfrom services.agent_runtime.app.action.preflight_artifact_service import (\n    PreflightArtifactService,\n)\nfrom services.agent_runtime.app.action.preflight_artifact_store import (\n    PreflightArtifactStore,\n)\nfrom services.agent_runtime.app.action.production_action_preparation import (\n    ProductionActionPreparationService,\n)\nfrom services.agent_runtime.app.action.production_action_query import (\n    ProductionActionQueryService,\n)\nfrom services.agent_runtime.app.action.production_action_guard import (\n    ProductionActionExpiryGuard,\n)\nfrom services.agent_runtime.app.action.production_pilot import (\n    KubernetesProductionPilotControl,\n    ProductionPilotReadinessService,\n)\nfrom services.agent_runtime.app.action.production_pilot_factory import (\n    create_kubernetes_production_pilot_control,\n)\nfrom services.agent_runtime.app.action.production_pilot_budget_service import (\n    ProductionPilotBudgetService,\n)\nfrom services.agent_runtime.app.action.production_pilot_budget_store import (\n    ProductionPilotBudgetStore,\n)\nfrom services.agent_runtime.app.action.production_pilot_rehearsal import (\n    ProductionPilotRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_crash_rehearsal import (\n    ProductionPilotCrashRecoveryRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_pre_enable_evidence import (\n    ProductionPilotPreEnableEvidenceService,\n)\nfrom services.agent_runtime.app.action.production_pilot_final_handoff import (\n    ProductionPilotFinalHandoffRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_live_probe import (\n    ProductionPilotLiveReadinessProbe,\n    create_production_pilot_live_readiness_probe,\n)\nfrom services.agent_runtime.app.action.production_pilot_go_no_go_service import (\n    ProductionPilotGoNoGoService,\n)\nfrom services.agent_runtime.app.action.production_pilot_go_no_go_store import (\n    ProductionPilotGoNoGoStore,\n)\nfrom services.agent_runtime.app.action.production_pilot_ceremony_service import (\n    ProductionPilotCeremonyService,\n)\nfrom services.agent_runtime.app.action.production_pilot_ceremony_store import (\n    ProductionPilotCeremonyStore,\n)\nfrom services.agent_runtime.app.verification.collector import (\n    VerificationEvidenceCollector,\n)\nfrom services.agent_runtime.app.verification.coordinator import (\n    VerificationCoordinator,\n)\nfrom services.agent_runtime.app.verification.profiles import (\n    VerificationProfileFactory,\n)\nfrom services.agent_runtime.app.verification.service import (\n    VerificationService,\n)\nfrom services.agent_runtime.app.verification.store import (\n    VerificationStore,\n)\nfrom services.agent_runtime.app.runtime.action_runtime import (\n    ActionRuntime,\n)\nfrom services.agent_runtime.app.runtime.verification_runtime import (\n    VerificationRuntime,\n)\nfrom services.agent_runtime.app.security.factory import (\n    create_authentication_service,\n)\nfrom services.agent_runtime.app.security.policy import (\n    SecurityPolicyEngine,\n)\nfrom services.agent_runtime.app.security.service import (\n    AuthenticationService,\n)\nfrom services.sandbox.executor.local import (\n    LocalSandboxExecutor,\n)\nfrom services.sandbox.policy.validator import (\n    SandboxPolicyValidator,\n)\n\n\nfrom services.agent_runtime.app.incident_evidence.recorder import (\n    ProductionIncidentEvidenceRecorder,\n)\nfrom services.agent_runtime.app.incident_evidence.settings import (\n    IncidentEvidenceRecorderSettings,\n)\n\nclass AgentRuntime:\n    """\n    Runtime container.\n\n    Owns and shares security and runtime infrastructure\n    across Pipeline, Action and Verification.\n\n    security_policy is the RBAC authorization policy. The existing policy\n    attribute remains the remediation business policy engine.\n    """\n\n    def __init__(\n        self,\n        authentication_service: (\n            AuthenticationService | None\n        ) = None,\n        security_policy: (\n            SecurityPolicyEngine | None\n        ) = None,\n        kubernetes_preflight: (\n            KubernetesPreflightResolver | None\n        ) = None,\n        kubernetes_production_executor: (\n            KubernetesProductionExecutor | None\n        ) = None,\n        production_pilot_control: (\n            KubernetesProductionPilotControl | None\n        ) = None,\n        production_pilot_budget_service: (\n            ProductionPilotBudgetService | None\n        ) = None,\n        production_pilot_live_probe: (\n            ProductionPilotLiveReadinessProbe | None\n        ) = None,\n        kubernetes_cluster_registry: (\n            KubernetesClusterRegistry | None\n        ) = None,\n        llm_gateway: (\n            LLMGateway | None\n        ) = None,\n        investigation_reasoner: (\n            BaseInvestigationReasoner | None\n        ) = None,\n        investigation_settings: (\n            InvestigationSettings | None\n        ) = None,\n    ) -> None:\n        # Validate every injected security component before factories, stores\n        # or other runtime components can produce side effects.\n        if (\n            authentication_service is not None\n            and not isinstance(\n                authentication_service,\n                AuthenticationService,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime authentication service is invalid"\n            )\n\n        if (\n            security_policy is not None\n            and not isinstance(\n                security_policy,\n                SecurityPolicyEngine,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime security policy is invalid"\n            )\n\n        if (\n            kubernetes_preflight is not None\n            and not isinstance(\n                kubernetes_preflight,\n                KubernetesPreflightResolver,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes preflight resolver is invalid"\n            )\n\n        if (\n            kubernetes_production_executor is not None\n            and not isinstance(\n                kubernetes_production_executor,\n                KubernetesProductionExecutor,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production executor is invalid"\n            )\n\n        if (\n            production_pilot_control is not None\n            and not isinstance(\n                production_pilot_control,\n                KubernetesProductionPilotControl,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production pilot control is invalid"\n            )\n\n        if (\n            production_pilot_budget_service is not None\n            and not isinstance(\n                production_pilot_budget_service,\n                ProductionPilotBudgetService,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production pilot budget service is invalid"\n            )\n\n        if (\n            production_pilot_live_probe is not None\n            and not isinstance(\n                production_pilot_live_probe,\n                ProductionPilotLiveReadinessProbe,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Production Pilot live probe is invalid"\n            )\n\n        if (\n            kubernetes_cluster_registry is not None\n            and not isinstance(\n                kubernetes_cluster_registry,\n                KubernetesClusterRegistry,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes cluster registry is invalid"\n            )\n\n        if (\n            llm_gateway is not None\n            and not isinstance(\n                llm_gateway,\n                LLMGateway,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime shared LLM gateway is invalid"\n            )\n\n        if (\n            investigation_reasoner is not None\n            and not isinstance(\n                investigation_reasoner,\n                BaseInvestigationReasoner,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation reasoner is invalid"\n            )\n\n        if (\n            investigation_settings is not None\n            and not isinstance(\n                investigation_settings,\n                InvestigationSettings,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation settings are invalid"\n            )\n\n        # Resolve disabled-default Investigation configuration before any\n        # Runtime store, tool, credential, network or LLM component is created.\n        self.investigation_settings = (\n            investigation_settings\n            if investigation_settings is not None\n            else InvestigationSettings.from_environment()\n        )\n\n        investigation_shared_gateway = None\n\n        # An enabled LLM-backed Investigation must use the exact shared\n        # LLMGateway instance that AgentRuntime will provide to its Agents.\n        #\n        # Disabled Investigation deliberately does not inspect or touch the\n        # supplied reasoner\'s LLM adapter.\n        if (\n            self.investigation_settings.enabled\n            and isinstance(\n                investigation_reasoner,\n                LLMInvestigationReasoner,\n            )\n        ):\n            investigation_llm = (\n                investigation_reasoner.investigation_llm\n            )\n\n            if not isinstance(\n                investigation_llm,\n                InvestigationLLMGatewayAdapter,\n            ):\n                raise TypeError(\n                    "AgentRuntime LLM Investigation requires "\n                    "InvestigationLLMGatewayAdapter"\n                )\n\n            investigation_shared_gateway = (\n                investigation_llm.llm_gateway\n            )\n\n            if not isinstance(\n                investigation_shared_gateway,\n                LLMGateway,\n            ):\n                raise TypeError(\n                    "AgentRuntime Investigation shared LLM gateway is invalid"\n                )\n\n            if (\n                llm_gateway is not None\n                and investigation_shared_gateway\n                is not llm_gateway\n            ):\n                raise TypeError(\n                    "AgentRuntime Investigation LLM gateway must be shared"\n                )\n\n        # Preserve the existing fail-closed Investigation assembly boundary.\n        # Enabled mode without an explicit reasoner still fails here before\n        # any Runtime or LLM infrastructure is constructed.\n        self.investigation_coordinator = (\n            create_investigation_coordinator(\n                reasoner=investigation_reasoner,\n                settings=self.investigation_settings,\n            )\n        )\n\n        # Do not construct a default Gateway yet. Keeping this unresolved\n        # preserves the previous initialization order. If Investigation\n        # already carries the approved Gateway Adapter, Runtime adopts that\n        # exact Gateway object as its shared instance.\n        self.llm_gateway = (\n            llm_gateway\n            if llm_gateway is not None\n            else investigation_shared_gateway\n        )\n\n        self.authentication = (\n            authentication_service\n            if authentication_service is not None\n            else create_authentication_service()\n        )\n\n        self.security_policy = (\n            security_policy\n            if security_policy is not None\n            else SecurityPolicyEngine()\n        )\n\n        self.kubernetes_preflight = (\n            kubernetes_preflight\n            if kubernetes_preflight is not None\n            else create_kubernetes_preflight_resolver()\n        )\n\n        self.production_pilot_control = (\n            production_pilot_control\n            if production_pilot_control is not None\n            else create_kubernetes_production_pilot_control()\n        )\n\n        # This independent gate may read both credential values at startup,\n        # but can construct only a two-GET probe. Disabled mode returns before\n        # any credential or CA access.\n        self.production_pilot_live_probe = (\n            production_pilot_live_probe\n            if production_pilot_live_probe is not None\n            else create_production_pilot_live_readiness_probe()\n        )\n\n        self.production_pilot_budget_store = None\n        self.production_pilot_budget_service = (\n            production_pilot_budget_service\n        )\n        if (\n            self.production_pilot_budget_service is None\n            and self.production_pilot_control.config.enabled\n        ):\n            self.production_pilot_budget_store = (\n                ProductionPilotBudgetStore()\n            )\n            self.production_pilot_budget_service = (\n                ProductionPilotBudgetService(\n                    store=(\n                        self.production_pilot_budget_store\n                    )\n                )\n            )\n\n        self.kubernetes_production_executor = (\n            kubernetes_production_executor\n            if kubernetes_production_executor is not None\n            else create_kubernetes_production_executor(\n                pilot_control=(\n                    self.production_pilot_control\n                ),\n                pilot_budget_service=(\n                    self.production_pilot_budget_service\n                ),\n            )\n        )\n\n        if self.kubernetes_production_executor is not None:\n            executor_control = getattr(\n                self.kubernetes_production_executor,\n                "pilot_control",\n                None,\n            )\n            if executor_control is None:\n                self.kubernetes_production_executor.pilot_control = (\n                    self.production_pilot_control\n                )\n            elif executor_control is not self.production_pilot_control:\n                raise TypeError(\n                    "AgentRuntime Kubernetes production pilot control must be shared"\n                )\n            executor_budget = getattr(\n                self.kubernetes_production_executor,\n                "pilot_budget_service",\n                None,\n            )\n            if executor_budget is None:\n                if self.production_pilot_budget_service is None:\n                    raise TypeError(\n                        "AgentRuntime Kubernetes production pilot budget is unavailable"\n                    )\n                self.kubernetes_production_executor.pilot_budget_service = (\n                    self.production_pilot_budget_service\n                )\n            elif executor_budget is not self.production_pilot_budget_service:\n                raise TypeError(\n                    "AgentRuntime Kubernetes production pilot budget must be shared"\n                )\n\n        if (\n            self.kubernetes_production_executor is not None\n            and self.kubernetes_preflight is None\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production executor requires "\n                "trusted preflight"\n            )\n\n        self.production_pilot_readiness = (\n            ProductionPilotReadinessService(\n                control=(\n                    self.production_pilot_control\n                ),\n                production_executor_configured=(\n                    self.kubernetes_production_executor\n                    is not None\n                ),\n            )\n        )\n        self.production_pilot_rehearsal = (\n            ProductionPilotRehearsalService(\n                control=(\n                    self.production_pilot_control\n                ),\n                budget_service=(\n                    self.production_pilot_budget_service\n                ),\n                production_executor_configured=(\n                    self.kubernetes_production_executor\n                    is not None\n                ),\n            )\n        )\n        # Pure recovery-policy proof. It owns no store, credential, network\n        # client or executor and is available while the production gate is\n        # disabled so operators can rehearse recovery before enablement.\n        self.production_pilot_crash_recovery_rehearsal = (\n            ProductionPilotCrashRecoveryRehearsalService()\n        )\n\n        self.memory = MemoryStore()\n\n        if (\n            kubernetes_cluster_registry\n            is None\n        ):\n            self.kubernetes_cluster_registry = (\n                create_kubernetes_cluster_registry()\n            )\n        else:\n            self.kubernetes_cluster_registry = (\n                kubernetes_cluster_registry\n            )\n\n        if (\n            self.kubernetes_cluster_registry\n            is None\n        ):\n            self.tools = create_tool_manager()\n        else:\n            self.tools = create_tool_manager(\n                kubernetes_cluster_registry=(\n                    self.kubernetes_cluster_registry\n                )\n            )\n\n        self.skills = create_skill_registry()\n        self.mcp = create_mcp_registry()\n        self.tracer = TraceCollector()\n        self.evaluators = create_evaluation_registry()\n\n        # Remediation business policy. This is intentionally separate from\n        # security_policy, which authorizes operator-facing operations.\n        self.policy = create_policy_engine()\n\n        self.preflight_artifact_store = None\n        self.preflight_artifact_service = None\n        self.production_action_guard = None\n        self.production_action_preparation = None\n        self.production_action_query = None\n\n        if self.kubernetes_preflight is not None:\n            self.preflight_artifact_store = PreflightArtifactStore()\n            self.preflight_artifact_service = PreflightArtifactService(\n                store=self.preflight_artifact_store\n            )\n            self.production_action_guard = (\n                ProductionActionExpiryGuard(\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    )\n                )\n            )\n\n        self.approval = ApprovalService()\n\n        if self.production_action_guard is not None:\n            self.approval.manager.set_transition_guard(\n                self.production_action_guard\n            )\n\n        if self.preflight_artifact_service is not None:\n            self.production_action_preparation = (\n                ProductionActionPreparationService(\n                    resolver=self.kubernetes_preflight,\n                    artifact_service=self.preflight_artifact_service,\n                    approval_service=self.approval,\n                )\n            )\n\n        self.production_pilot_ceremony_store = None\n        self.production_pilot_ceremony = None\n        if (\n            self.production_pilot_control.config.enabled\n            and self.production_pilot_budget_service is not None\n            and self.preflight_artifact_service is not None\n        ):\n            self.production_pilot_ceremony_store = (\n                ProductionPilotCeremonyStore()\n            )\n            self.production_pilot_ceremony = (\n                ProductionPilotCeremonyService(\n                    store=(\n                        self.production_pilot_ceremony_store\n                    ),\n                    control=(\n                        self.production_pilot_control\n                    ),\n                    rehearsal=(\n                        self.production_pilot_rehearsal\n                    ),\n                    budget_service=(\n                        self.production_pilot_budget_service\n                    ),\n                    approval_service=self.approval,\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                )\n            )\n\n        self.incident_store = IncidentStore()\n\n        if self.preflight_artifact_service is not None:\n            self.production_action_query = (\n                ProductionActionQueryService(\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                    approval_service=self.approval,\n                    incident_store=self.incident_store,\n                )\n            )\n\n        self.incident_service = IncidentService(\n            store=self.incident_store\n        )\n\n        self.workflow_service = WorkflowService(\n            incident_service=self.incident_service\n        )\n\n        self.action_execution_store = ActionExecutionStore()\n\n        self.action_execution_service = ActionExecutionService(\n            store=self.action_execution_store\n        )\n\n        self.action_runtime = ActionRuntime(\n            approval_service=self.approval,\n            incident_store=self.incident_store,\n            action_execution_service=self.action_execution_service,\n            production_action_guard=(\n                self.production_action_guard\n            ),\n            kubernetes_production_executor=(\n                self.kubernetes_production_executor\n            ),\n            preflight_artifact_service=(\n                self.preflight_artifact_service\n                if self.kubernetes_production_executor is not None\n                else None\n            ),\n            production_pilot_control=(\n                self.production_pilot_control\n            ),\n            production_pilot_budget_service=(\n                self.production_pilot_budget_service\n            ),\n            production_pilot_ceremony_service=(\n                self.production_pilot_ceremony\n                if self.kubernetes_production_executor is not None\n                else None\n            ),\n        )\n\n        self.verification_store = VerificationStore()\n\n        self.verification = VerificationService(\n            store=self.verification_store\n        )\n\n        self.verification_runtime = VerificationRuntime(\n            verification_service=self.verification,\n            incident_store=self.incident_store,\n        )\n\n        self.verification_profile_factory = VerificationProfileFactory()\n\n        self.verification_collector = VerificationEvidenceCollector(\n            tools=self.tools\n        )\n\n        self.verification_coordinator = VerificationCoordinator(\n            profile_factory=self.verification_profile_factory,\n            collector=self.verification_collector,\n            verification_runtime=self.verification_runtime,\n        )\n\n        # Final pre-enable evidence is assembled only when every production\n        # preparation component is available. The service is read-only and\n        # deliberately owns no executor or mutable workflow operation.\n        self.production_pilot_pre_enable_evidence = None\n        if all(\n            component is not None\n            for component in (\n                self.production_pilot_ceremony,\n                self.production_pilot_budget_service,\n                self.preflight_artifact_service,\n            )\n        ):\n            self.production_pilot_pre_enable_evidence = (\n                ProductionPilotPreEnableEvidenceService(\n                    readiness_service=(\n                        self.production_pilot_readiness\n                    ),\n                    rehearsal_service=(\n                        self.production_pilot_rehearsal\n                    ),\n                    crash_rehearsal_service=(\n                        self.production_pilot_crash_recovery_rehearsal\n                    ),\n                    ceremony_service=(\n                        self.production_pilot_ceremony\n                    ),\n                    budget_service=(\n                        self.production_pilot_budget_service\n                    ),\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                    approval_service=self.approval,\n                    incident_store=self.incident_store,\n                    action_execution_service=(\n                        self.action_execution_service\n                    ),\n                    verification_service=self.verification,\n                )\n            )\n\n        # The final handoff rehearsal is also strictly read-only. It is\n        # available only with the full prepared Pilot chain and explicitly\n        # records whether production executors remain absent while the gate\n        # is disabled.\n        self.production_pilot_final_handoff_rehearsal = None\n        if self.production_pilot_pre_enable_evidence is not None:\n            self.production_pilot_final_handoff_rehearsal = (\n                ProductionPilotFinalHandoffRehearsalService(\n                    pilot_control=self.production_pilot_control,\n                    pre_enable_evidence_service=(\n                        self.production_pilot_pre_enable_evidence\n                    ),\n                    preflight_resolver=self.kubernetes_preflight,\n                    production_executor_configured=(\n                        self.kubernetes_production_executor is not None\n                    ),\n                    action_runtime_production_executor_configured=(\n                        getattr(\n                            self.action_runtime,\n                            "kubernetes_production_executor",\n                            None,\n                        )\n                        is not None\n                    ),\n                )\n            )\n\n        # A dedicated database is created only when the separately gated live\n        # probe exists and the full zero-write handoff chain is available.\n        self.production_pilot_go_no_go_store = None\n        self.production_pilot_go_no_go = None\n        if (\n            self.production_pilot_live_probe is not None\n            and self.production_pilot_final_handoff_rehearsal is not None\n            and self.preflight_artifact_service is not None\n        ):\n            self.production_pilot_go_no_go_store = (\n                ProductionPilotGoNoGoStore()\n            )\n            self.production_pilot_go_no_go = (\n                ProductionPilotGoNoGoService(\n                    store=self.production_pilot_go_no_go_store,\n                    live_probe=self.production_pilot_live_probe,\n                    final_handoff_service=(\n                        self.production_pilot_final_handoff_rehearsal\n                    ),\n                    artifact_service=self.preflight_artifact_service,\n                    pilot_control=self.production_pilot_control,\n                )\n            )\n\n        self.sandbox = LocalSandboxExecutor()\n\n        self.sandbox_policy = SandboxPolicyValidator()\n\n        if self.llm_gateway is None:\n            self.llm_gateway = create_llm_gateway()\n\n        self.registry = create_agent_registry(\n            llm_gateway=self.llm_gateway,\n        )\n\n        self.planner = AgentPlanner()\n\n        self.pipeline = PlannerPipeline(\n            self.registry,\n            self.planner,\n            self.tracer,\n            self.evaluators,\n            incident_store=self.incident_store,\n            incident_service=self.incident_service,\n            workflow_service=self.workflow_service,\n        )\n\n    async def execute(\n        self,\n        context: AgentContext,\n    ):\n        """\n        Execute the primary PlannerPipeline and, when explicitly enabled,\n        run Investigation automatically as a best-effort Shadow.\n\n        Ordering is deliberate:\n\n        1. PlannerPipeline completes first.\n        2. Investigation receives an isolated AgentContext.\n        3. Only the bounded investigation_shadow snapshot is copied back.\n\n        Investigation can never change the Pipeline result, Incident,\n        variables, results, trace, Approval, executions or evaluations.\n\n        Investigation orchestration failure is sanitized and recorded in\n        metadata without failing an otherwise successful Pipeline execution.\n        """\n\n        if not isinstance(\n            context,\n            AgentContext,\n        ):\n            raise TypeError(\n                "AgentRuntime execution context is invalid"\n            )\n\n        # Reserved Shadow metadata from a previous execution must never be\n        # visible to the primary Pipeline, even when this Runtime currently\n        # has Investigation disabled.\n        for reserved_key in (\n            "investigation_shadow",\n            "investigation_shadow_orchestration",\n            "investigation_rca_comparison",\n        ):\n            context.metadata.pop(\n                reserved_key,\n                None,\n            )\n\n        # Primary workflow semantics remain authoritative. Pipeline failure\n        # propagates normally and Investigation is not attempted afterward.\n        context.metadata.pop(\n            "incident_evidence_recorder",\n            None,\n        )\n\n        results = await self.pipeline.execute(\n            context\n        )\n\n        # Evidence Recorder is evaluation-only and best-effort.\n        await self._record_incident_evidence_shadow(\n            context\n        )\n\n        if self.investigation_coordinator is None:\n            return results\n\n        shadow_context = (\n            self._create_investigation_shadow_context(\n                context\n            )\n        )\n\n        try:\n            await self.run_investigation_shadow(\n                shadow_context\n            )\n\n            snapshot = shadow_context.metadata.get(\n                "investigation_shadow"\n            )\n\n            if (\n                not isinstance(\n                    snapshot,\n                    dict,\n                )\n                or snapshot.get(\n                    "shadow_mode"\n                )\n                is not True\n                or snapshot.get(\n                    "read_only"\n                )\n                is not True\n            ):\n                raise RuntimeError(\n                    "Investigation Shadow snapshot is invalid"\n                )\n\n            context.metadata[\n                "investigation_shadow"\n            ] = deepcopy(\n                snapshot\n            )\n\n        except Exception as exc:\n            # Shadow means Shadow: an Investigation orchestration fault must\n            # never convert a successful PlannerPipeline execution to failed.\n            #\n            # Raw exception text is deliberately excluded because provider,\n            # URL, credential or tool details may be present in it.\n            context.metadata[\n                "investigation_shadow_orchestration"\n            ] = {\n                "shadow_mode": True,\n                "read_only": True,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n\n        # Comparison is evaluation-only. It cannot change the authoritative\n        # RCA stored in context.variables["rca"] and has no Healing authority.\n        try:\n            context.metadata[\n                "investigation_rca_comparison"\n            ] = build_rca_investigation_comparison(\n                rca=context.variables.get(\n                    "rca"\n                ),\n                investigation_snapshot=(\n                    context.metadata.get(\n                        "investigation_shadow"\n                    )\n                ),\n                orchestration_snapshot=(\n                    context.metadata.get(\n                        "investigation_shadow_orchestration"\n                    )\n                ),\n            )\n        except Exception as exc:\n            # A comparison bug must remain weaker than Shadow itself and must\n            # never fail a successful primary Pipeline.\n            context.metadata[\n                "investigation_rca_comparison"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "available": False,\n                "comparison_status": (\n                    "comparison_failed"\n                ),\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n\n        return results\n\n    def _create_investigation_shadow_context(\n        self,\n        context: AgentContext,\n    ) -> AgentContext:\n        """\n        Build the minimum-privilege context for automatic Investigation.\n\n        Copied:\n        - event input\n        - request correlation ID\n\n        Shared:\n        - exact Runtime-owned ToolManager\n\n        Deliberately not shared:\n        - Incident\n        - variables\n        - results\n        - metadata\n        - trace\n        - memory\n        - skills\n        - MCP\n        - sandbox\n        - Approval\n        - executions\n        - evaluations\n        """\n\n        return AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n    async def run_investigation_shadow(\n        self,\n        context: AgentContext,\n    ) -> InvestigationState:\n        """\n        Explicitly execute the enabled read-only Investigation Shadow.\n\n        This method is intentionally separate from PlannerPipeline.\n\n        PlannerPipeline itself never invokes Investigation. AgentRuntime\n        may call this lower-level entry point after a successful Pipeline\n        execution when automatic Shadow Investigation is enabled.\n\n        The supplied AgentContext must use the exact Runtime ToolManager so\n        Investigation probes cannot bypass Runtime-owned tool boundaries.\n        """\n\n        if not isinstance(\n            context,\n            AgentContext,\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation Shadow context is invalid"\n            )\n\n        if self.investigation_coordinator is None:\n            raise RuntimeError(\n                "AgentRuntime Investigation Shadow is disabled"\n            )\n\n        if context.tools is not self.tools:\n            raise TypeError(\n                "AgentRuntime Investigation Shadow requires shared Runtime tools"\n            )\n\n        return await (\n            self.investigation_coordinator.investigate(\n                context\n            )\n        )\n\n    async def _record_incident_evidence_shadow(\n        self,\n        context: AgentContext,\n    ) -> None:\n        """\n        Best-effort, decision-isolated production evidence preservation.\n\n        Runs only after the authoritative PlannerPipeline succeeds.\n        Disabled mode constructs no Recorder and issues no production Probe.\n        """\n\n        try:\n            settings = (\n                IncidentEvidenceRecorderSettings\n                .from_environment()\n            )\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n            return\n\n        if not settings.enabled:\n            return\n\n        recorder_context = AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n        try:\n            recorder = ProductionIncidentEvidenceRecorder(\n                settings.resolve_output_dir()\n            )\n\n            result = await recorder.record(\n                recorder_context\n            )\n\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "captured",\n                "created": result.created,\n                "incident_id": result.incident_id,\n                "observation_count": (\n                    result.observation_count\n                ),\n                "capture_file": result.path.name,\n            }\n\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n'
TEST_SOURCE = 'from __future__ import annotations\n\nfrom pathlib import Path\n\nimport pytest\nfrom pydantic import ValidationError\n\nimport services.agent_runtime.app.runtime.runtime as runtime_module\nimport services.agent_runtime.app.tools.kubernetes.connection_factory as factory_module\n\nfrom common.config.settings import (\n    AppConfig,\n    ConnectionsConfig,\n    KubernetesReadClusterConfig,\n    KubernetesReadMultiClusterConfig,\n    LLMConfig,\n    RuntimeConfig,\n    Settings,\n)\n\nfrom services.agent_runtime.app.investigation.settings import (\n    InvestigationSettings,\n)\nfrom services.agent_runtime.app.security.factory import (\n    create_authentication_service,\n)\nfrom common.config.settings import (\n    AuthenticationConfig,\n)\nfrom services.agent_runtime.app.tools.kubernetes.connection_factory import (\n    KubernetesReadConnectionFactoryConfigurationError,\n    create_kubernetes_cluster_registry,\n)\nfrom services.agent_runtime.app.tools.kubernetes.router import (\n    KubernetesClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.manager import (\n    ToolManager,\n)\nfrom services.agent_runtime.app.tools.registry import (\n    ToolRegistry,\n)\n\n\ndef cluster_config(\n    *,\n    name="prod-sg-17",\n    api_url="https://sg-kubernetes.example.internal",\n    bearer_token_env="K8S_PROD_SG_READ_TOKEN",\n    bearer_token_file=None,\n    ca_file=None,\n):\n    return KubernetesReadClusterConfig(\n        cluster_name=name,\n        api_url=api_url,\n        bearer_token_env=(\n            bearer_token_env\n        ),\n        bearer_token_file=(\n            bearer_token_file\n        ),\n        ca_file=ca_file,\n    )\n\n\ndef enabled_config(\n    *clusters,\n):\n    return KubernetesReadMultiClusterConfig(\n        enabled=True,\n        clusters=clusters,\n    )\n\n\ndef test_settings_default_keeps_multi_cluster_read_disabled():\n    settings = Settings(\n        app=AppConfig(\n            name="test",\n            version="1",\n        ),\n        llm=LLMConfig(\n            provider="mock",\n            temperature=0.0,\n            timeout=30,\n        ),\n        runtime=RuntimeConfig(\n            pipeline="sequential",\n            max_workers=1,\n        ),\n    )\n\n    assert isinstance(\n        settings.connections,\n        ConnectionsConfig,\n    )\n\n    assert (\n        settings\n        .connections\n        .kubernetes_read\n        .enabled\n        is False\n    )\n\n    assert (\n        settings\n        .connections\n        .kubernetes_read\n        .clusters\n        == ()\n    )\n\n\ndef test_cluster_descriptor_rejects_raw_secret_and_insecure_url():\n    with pytest.raises(\n        ValidationError,\n    ):\n        KubernetesReadClusterConfig.model_validate(\n            {\n                "cluster_name": "prod-sg-17",\n                "api_url": (\n                    "https://sg-kubernetes.example.internal"\n                ),\n                "bearer_token": "raw-secret-must-not-be-configurable",\n                "bearer_token_env": "K8S_PROD_SG_READ_TOKEN",\n            }\n        )\n\n    with pytest.raises(\n        ValidationError,\n        match="clean HTTPS origin",\n    ):\n        cluster_config(\n            api_url=(\n                "http://sg-kubernetes.example.internal"\n            ),\n        )\n\n\n@pytest.mark.parametrize(\n    "kwargs",\n    [\n        {\n            "bearer_token_env": None,\n            "bearer_token_file": None,\n        },\n        {\n            "bearer_token_env": "K8S_PROD_SG_READ_TOKEN",\n            "bearer_token_file": "/run/secrets/sg-token",\n        },\n    ],\n)\ndef test_cluster_descriptor_requires_exactly_one_credential_reference(\n    kwargs,\n):\n    with pytest.raises(\n        ValidationError,\n        match="exactly one token source",\n    ):\n        cluster_config(\n            **kwargs,\n        )\n\n\ndef test_enabled_config_requires_clusters_and_unique_connection_identity():\n    with pytest.raises(\n        ValidationError,\n        match="at least one cluster",\n    ):\n        KubernetesReadMultiClusterConfig(\n            enabled=True\n        )\n\n    first = cluster_config()\n\n    with pytest.raises(\n        ValidationError,\n        match="cluster names must be unique",\n    ):\n        enabled_config(\n            first,\n            cluster_config(\n                api_url=(\n                    "https://sg-duplicate.example.internal"\n                ),\n                bearer_token_env=(\n                    "K8S_PROD_SG_DUP_READ_TOKEN"\n                ),\n            ),\n        )\n\n    with pytest.raises(\n        ValidationError,\n        match="API URLs must be unique",\n    ):\n        enabled_config(\n            first,\n            cluster_config(\n                name="prod-us-03",\n                bearer_token_env=(\n                    "K8S_PROD_US_READ_TOKEN"\n                ),\n            ),\n        )\n\n    with pytest.raises(\n        ValidationError,\n        match="distinct credential references",\n    ):\n        enabled_config(\n            first,\n            cluster_config(\n                name="prod-us-03",\n                api_url=(\n                    "https://us-kubernetes.example.internal"\n                ),\n                bearer_token_env=(\n                    "K8S_PROD_SG_READ_TOKEN"\n                ),\n            ),\n        )\n\n\ndef test_disabled_factory_does_not_touch_credentials_or_files():\n    disabled = KubernetesReadMultiClusterConfig(\n        enabled=False,\n        clusters=(\n            cluster_config(),\n        ),\n    )\n\n    class ExplodingEnvironment(\n        dict\n    ):\n        def get(\n            self,\n            *args,\n            **kwargs,\n        ):\n            raise AssertionError(\n                "disabled config must not read environment"\n            )\n\n    def exploding_reader(\n        path,\n    ):\n        raise AssertionError(\n            "disabled config must not read token files"\n        )\n\n    result = create_kubernetes_cluster_registry(\n        disabled,\n        environment=ExplodingEnvironment(),\n        token_file_reader=exploding_reader,\n    )\n\n    assert result is None\n\n\ndef test_enabled_env_config_builds_exact_read_only_registry_without_network():\n    config = enabled_config(\n        cluster_config(),\n        cluster_config(\n            name="prod-us-03",\n            api_url=(\n                "https://us-kubernetes.example.internal"\n            ),\n            bearer_token_env=(\n                "K8S_PROD_US_READ_TOKEN"\n            ),\n        ),\n    )\n\n    registry = create_kubernetes_cluster_registry(\n        config,\n        environment={\n            "K8S_PROD_SG_READ_TOKEN": (\n                "sg-read-token-1234567890"\n            ),\n            "K8S_PROD_US_READ_TOKEN": (\n                "us-read-token-1234567890"\n            ),\n        },\n    )\n\n    assert isinstance(\n        registry,\n        KubernetesClusterRegistry,\n    )\n\n    assert registry.cluster_names == (\n        "prod-sg-17",\n        "prod-us-03",\n    )\n\n    sg = registry.resolve(\n        "prod-sg-17"\n    )\n\n    us = registry.resolve(\n        "prod-us-03"\n    )\n\n    assert sg.api_url == (\n        "https://sg-kubernetes.example.internal"\n    )\n\n    assert us.api_url == (\n        "https://us-kubernetes.example.internal"\n    )\n\n    assert sg.verify_tls is True\n    assert us.verify_tls is True\n\n    assert (\n        sg.allow_dry_run_fallback\n        is False\n    )\n\n    assert (\n        us.allow_dry_run_fallback\n        is False\n    )\n\n    assert sg.client is None\n    assert us.client is None\n\n\ndef test_token_file_and_ca_references_are_resolved_locally(\n    tmp_path: Path,\n):\n    token_file = (\n        tmp_path\n        / "token"\n    )\n\n    token_file.write_text(\n        "file-read-token-1234567890\\n",\n        encoding="utf-8",\n    )\n\n    ca_file = (\n        tmp_path\n        / "ca.crt"\n    )\n\n    ca_file.write_text(\n        "unit-test-ca-placeholder",\n        encoding="utf-8",\n    )\n\n    config = enabled_config(\n        cluster_config(\n            bearer_token_env=None,\n            bearer_token_file=str(\n                token_file\n            ),\n            ca_file=str(\n                ca_file\n            ),\n        )\n    )\n\n    registry = create_kubernetes_cluster_registry(\n        config\n    )\n\n    tool = registry.resolve(\n        "prod-sg-17"\n    )\n\n    assert tool.ca_file == (\n        ca_file\n    )\n\n    assert tool.bearer_token == (\n        "file-read-token-1234567890"\n    )\n\n\ndef test_missing_environment_secret_fails_without_exposing_secret_value():\n    config = enabled_config(\n        cluster_config()\n    )\n\n    with pytest.raises(\n        KubernetesReadConnectionFactoryConfigurationError,\n        match=(\n            "environment variable is missing"\n        ),\n    ) as captured:\n        create_kubernetes_cluster_registry(\n            config,\n            environment={},\n        )\n\n    assert (\n        "sg-read-token-1234567890"\n        not in str(\n            captured.value\n        )\n    )\n\n\ndef test_invalid_ca_reference_fails_before_registry_is_returned(\n    tmp_path: Path,\n):\n    missing = (\n        tmp_path\n        / "missing-ca.crt"\n    )\n\n    config = enabled_config(\n        cluster_config(\n            ca_file=str(\n                missing\n            ),\n        )\n    )\n\n    with pytest.raises(\n        KubernetesReadConnectionFactoryConfigurationError,\n        match="CA file is unavailable",\n    ):\n        create_kubernetes_cluster_registry(\n            config,\n            environment={\n                "K8S_PROD_SG_READ_TOKEN": (\n                    "sg-read-token-1234567890"\n                )\n            },\n        )\n\n\ndef test_config_serialization_contains_references_not_token_values():\n    config = enabled_config(\n        cluster_config()\n    )\n\n    payload = config.model_dump()\n\n    text = str(\n        payload\n    )\n\n    assert (\n        "K8S_PROD_SG_READ_TOKEN"\n        in text\n    )\n\n    assert (\n        "sg-read-token-1234567890"\n        not in text\n    )\n\n    assert (\n        "bearer_token"\n        not in payload[\n            "clusters"\n        ][\n            0\n        ]\n    )\n\n\ndef test_runtime_uses_config_factory_only_when_registry_not_explicit(\n    monkeypatch,\n    tmp_path,\n):\n    monkeypatch.chdir(\n        tmp_path\n    )\n\n    configured_registry = (\n        KubernetesClusterRegistry(\n            [\n                factory_module.KubernetesTool(\n                    api_url=(\n                        "https://sg-kubernetes.example.internal"\n                    ),\n                    cluster_name="prod-sg-17",\n                    bearer_token=(\n                        "sg-read-token-1234567890"\n                    ),\n                    allow_dry_run_fallback=False,\n                )\n            ]\n        )\n    )\n\n    registry_factory_calls = []\n\n    def registry_factory():\n        registry_factory_calls.append(\n            True\n        )\n\n        return configured_registry\n\n    manager_calls = []\n\n    def manager_factory(\n        **kwargs,\n    ):\n        manager_calls.append(\n            dict(\n                kwargs\n            )\n        )\n\n        return ToolManager(\n            ToolRegistry()\n        )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_cluster_registry",\n        registry_factory,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_tool_manager",\n        manager_factory,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_preflight_resolver",\n        lambda: None,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_production_executor",\n        lambda **_: None,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_production_pilot_live_readiness_probe",\n        lambda: None,\n    )\n\n    runtime = runtime_module.AgentRuntime(\n        authentication_service=(\n            create_authentication_service(\n                AuthenticationConfig()\n            )\n        ),\n        investigation_settings=(\n            InvestigationSettings()\n        ),\n    )\n\n    assert registry_factory_calls == [\n        True\n    ]\n\n    assert (\n        runtime.kubernetes_cluster_registry\n        is configured_registry\n    )\n\n    assert manager_calls == [\n        {\n            "kubernetes_cluster_registry": (\n                configured_registry\n            )\n        }\n    ]\n\n\ndef test_explicit_runtime_registry_bypasses_connection_config_factory(\n    monkeypatch,\n    tmp_path,\n):\n    monkeypatch.chdir(\n        tmp_path\n    )\n\n    explicit_registry = (\n        KubernetesClusterRegistry(\n            [\n                factory_module.KubernetesTool(\n                    api_url=(\n                        "https://explicit-kubernetes.example.internal"\n                    ),\n                    cluster_name="prod-explicit-01",\n                    bearer_token=(\n                        "explicit-read-token-123456"\n                    ),\n                    allow_dry_run_fallback=False,\n                )\n            ]\n        )\n    )\n\n    def forbidden_registry_factory():\n        raise AssertionError(\n            "explicit registry must bypass connection config factory"\n        )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_cluster_registry",\n        forbidden_registry_factory,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_tool_manager",\n        lambda **_: ToolManager(\n            ToolRegistry()\n        ),\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_preflight_resolver",\n        lambda: None,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_production_executor",\n        lambda **_: None,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_production_pilot_live_readiness_probe",\n        lambda: None,\n    )\n\n    runtime = runtime_module.AgentRuntime(\n        authentication_service=(\n            create_authentication_service(\n                AuthenticationConfig()\n            )\n        ),\n        kubernetes_cluster_registry=(\n            explicit_registry\n        ),\n        investigation_settings=(\n            InvestigationSettings()\n        ),\n    )\n\n    assert (\n        runtime.kubernetes_cluster_registry\n        is explicit_registry\n    )\n\n\ndef test_connection_factory_module_contains_no_write_authority():\n    source = Path(\n        factory_module.__file__\n    ).read_text(\n        encoding="utf-8"\n    )\n\n    forbidden = [\n        "ActionRuntime",\n        "ApprovalService",\n        "VerificationRuntime",\n        "KubernetesProductionExecutor",\n        "KubernetesPreflightResolver",\n        ".post(",\n        ".patch(",\n        ".put(",\n        ".delete(",\n    ]\n\n    assert [\n        item\n        for item in forbidden\n        if item in source\n    ] == []\n'


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


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


def normalize_text(
    value: str,
) -> str:
    return (
        value
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def read_text(
    path: Path,
) -> str:
    return normalize_text(
        path.read_text(
            encoding="utf-8-sig",
            errors="strict",
        )
    )


def write_text(
    path: Path,
    value: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        normalize_text(
            value
        ),
        encoding="utf-8",
        newline="\n",
    )


def sha256_text(
    value: str,
) -> str:
    return hashlib.sha256(
        normalize_text(
            value
        ).encode(
            "utf-8"
        )
    ).hexdigest()


def backup_file(
    path: Path,
) -> Path:
    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup = path.with_name(
        f"{path.name}.before_{VERSION}_{stamp}.bak"
    )

    shutil.copy2(
        path,
        backup,
    )

    return backup


def run_command(
    *,
    root: Path,
    name: str,
    command: list[str],
) -> CommandResult:
    process = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    return CommandResult(
        name=name,
        command=command,
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )


def section(
    lines: list[str],
    title: str,
) -> None:
    lines.extend(
        [
            "",
            "=" * 120,
            title,
            "=" * 120,
            "",
        ]
    )


def add_command(
    lines: list[str],
    result: CommandResult,
) -> None:
    section(
        lines,
        f"COMMAND: {result.name}",
    )

    lines.extend(
        [
            " ".join(
                result.command
            ),
            "",
            f"ExitCode: {result.returncode}",
            "",
            "STDOUT",
            "-" * 120,
            result.stdout.rstrip()
            or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            result.stderr.rstrip()
            or "<EMPTY>",
        ]
    )


def verify_hash(
    *,
    root: Path,
    relative: str,
) -> None:
    path = root / relative

    if not path.exists():
        raise RuntimeError(
            f"Required current file is missing: {relative}"
        )

    actual = sha256_text(
        read_text(
            path
        )
    )

    expected = EXPECTED_HASHES[
        relative
    ]

    if actual != expected:
        raise RuntimeError(
            (
                f"{relative} changed after the reviewed connection-config snapshot. "
                f"expected_sha256={expected} actual_sha256={actual}. "
                "Refusing stale Multi-Cluster Connection Config installation."
            )
        )


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after = root / AFTER_NAME
    error = root / ERROR_NAME

    for output in (
        after,
        error,
    ):
        try:
            output.unlink()
        except FileNotFoundError:
            pass

    settings_file = (
        root
        / "packages"
        / "common"
        / "src"
        / "common"
        / "config"
        / "settings.py"
    )

    connection_factory_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "tools"
        / "kubernetes"
        / "connection_factory.py"
    )

    runtime_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "runtime"
        / "runtime.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_multi_cluster_connection_config.py"
    )

    sources = {
        settings_file: SETTINGS_SOURCE,
        connection_factory_file: (
            CONNECTION_FACTORY_SOURCE
        ),
        runtime_file: RUNTIME_SOURCE,
        test_file: TEST_SOURCE,
    }

    targets = list(
        sources
    )

    preexisting = {
        path: path.exists()
        for path in targets
    }

    backups = []

    report = [
        "Multi-Cluster Connection Config / Registry Factory v1.1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Configuration location:",
        "- Settings.connections.kubernetes_read",
        "- configs/app.yaml remains valid without modification because the new root has a disabled default",
        "",
        "Cluster descriptor:",
        "- cluster_name",
        "- clean HTTPS api_url",
        "- exactly one bearer_token_env OR bearer_token_file reference",
        "- optional ca_file reference",
        "- bounded request_timeout_seconds",
        "",
        "Secret boundary:",
        "- no raw bearer_token field exists in the Pydantic descriptor",
        "- app.yaml stores credential references only",
        "- token values are resolved only inside the connection factory",
        "- token values are passed directly to cluster-bound KubernetesTool objects and are never included in factory reports",
        "",
        "Factory behavior:",
        "- disabled -> None before reading env/token/CA files",
        "- enabled -> validate local references and build KubernetesTool objects",
        "- allow_dry_run_fallback=False for configured production-read clusters",
        "- verify_tls=True",
        "- no HTTP request is made during registry assembly",
        "",
        "Runtime behavior:",
        "- explicit injected KubernetesClusterRegistry remains authoritative",
        "- otherwise Runtime asks the new config factory for an optional registry",
        "- disabled config preserves the legacy create_tool_manager() path",
        "- enabled config automatically activates the already-installed Multi-Cluster Router",
        "",
        "Production write boundary:",
        "- remediation KubernetesPreflightConfig is unchanged",
        "- KubernetesProductionExecutionConfig is unchanged",
        "- Production Preflight / Executor credentials remain separate",
        "",
        "Installer sends no real Kubernetes/Prometheus/LLM request.",
    ]

    try:
        section(
            report,
            "CURRENT HASH PREFLIGHT",
        )

        for relative in EXPECTED_HASHES:
            verify_hash(
                root=root,
                relative=relative,
            )

            report.append(
                relative
                + "="
                + EXPECTED_HASHES[
                    relative
                ]
            )

        if connection_factory_file.exists():
            raise RuntimeError(
                "connection_factory.py already exists; refusing to overwrite an unreviewed connection factory"
            )

        section(
            report,
            "BACKUP",
        )

        for path in targets:
            if path.exists():
                backup = backup_file(
                    path
                )

                backups.append(
                    (
                        path,
                        backup,
                    )
                )

                report.append(
                    "backup="
                    + str(
                        backup.relative_to(
                            root
                        )
                    )
                )

        for path, source in sources.items():
            write_text(
                path,
                source,
            )

        syntax = run_command(
            root=root,
            name="Python syntax",
            command=[
                "uv",
                "run",
                "python",
                "-m",
                "py_compile",
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
                    for path in targets
                ],
            ],
        )

        add_command(
            report,
            syntax,
        )

        if syntax.returncode != 0:
            raise RuntimeError(
                "Multi-Cluster Connection Config syntax failed"
            )

        focused = run_command(
            root=root,
            name="Connection Config / Registry Factory focused suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_connection_config.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_kubernetes_router.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_production_scope_integrity.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            focused,
        )

        if focused.returncode != 0:
            raise RuntimeError(
                "Multi-Cluster Connection Config focused tests failed"
            )

        settings_compat = run_command(
            root=root,
            name="Settings / security compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_kubernetes_production_settings.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_kubernetes_preflight_factory.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_kubernetes_production_factory.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            settings_compat,
        )

        if settings_compat.returncode != 0:
            raise RuntimeError(
                "Multi-Cluster Connection Config settings/security compatibility failed"
            )

        root_settings_test = (
            root
            / "tests"
            / "test_settings.py"
        )

        if root_settings_test.exists():
            optional_settings = run_command(
                root=root,
                name="Optional root Settings compatibility suite",
                command=[
                    "uv",
                    "run",
                    "pytest",
                    str(
                        root_settings_test.relative_to(
                            root
                        )
                    ),
                    "-q",
                ],
            )

            add_command(
                report,
                optional_settings,
            )

            if optional_settings.returncode != 0:
                raise RuntimeError(
                    "Optional root Settings compatibility tests failed"
                )

        else:
            section(
                report,
                "OPTIONAL ROOT SETTINGS TEST",
            )

            report.append(
                "SKIPPED: tests/test_settings.py does not exist in this repository checkout."
            )

        runtime_compat = run_command(
            root=root,
            name="Runtime / Investigation compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_runtime_investigation_wiring.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_auto_shadow_orchestration.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_production_tool_contract.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_change_capability.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_config_change_capability.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            runtime_compat,
        )

        if runtime_compat.returncode != 0:
            raise RuntimeError(
                "Multi-Cluster Connection Config runtime compatibility failed"
            )

        preflight = run_command(
            root=root,
            name="Connection security / disabled-default preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from common.config import get_settings; "
                    "from pathlib import Path; "
                    "s=get_settings(); "
                    "c=s.connections.kubernetes_read; "
                    "f=Path(r'services/agent_runtime/app/tools/kubernetes/connection_factory.py').read_text(encoding='utf-8'); "
                    "st=Path(r'packages/common/src/common/config/settings.py').read_text(encoding='utf-8'); "
                    "print('enabled='+str(c.enabled)); "
                    "print('clusters='+str(len(c.clusters))); "
                    "print('raw_secret_field='+str('bearer_token: str' in st)); "
                    "print('factory_write_authority='+str(any(x in f for x in ['KubernetesProductionExecutor','KubernetesPreflightResolver','ActionRuntime','ApprovalService']))); "
                    "assert c.enabled is False; "
                    "assert len(c.clusters)==0; "
                    "assert 'class KubernetesReadClusterConfig' in st; "
                    "assert 'class KubernetesReadMultiClusterConfig' in st; "
                    "assert 'bearer_token: str' not in st; "
                    "assert not any(x in f for x in ['KubernetesProductionExecutor','KubernetesPreflightResolver','ActionRuntime','ApprovalService'])"
                ),
            ],
        )

        add_command(
            report,
            preflight,
        )

        if preflight.returncode != 0:
            raise RuntimeError(
                "Multi-Cluster Connection Config safety preflight failed"
            )

        authority = run_command(
            root=root,
            name="Read-only connection authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "f=Path(r'services/agent_runtime/app/tools/kubernetes/connection_factory.py').read_text(encoding='utf-8'); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService','VerificationRuntime','KubernetesProductionExecutor','KubernetesPreflightResolver','.post(','.patch(','.put(','.delete('] if x in f]; "
                    "print('forbidden_matches='+str(bad)); "
                    "raise SystemExit(1 if bad else 0)"
                ),
            ],
        )

        add_command(
            report,
            authority,
        )

        if authority.returncode != 0:
            raise RuntimeError(
                "Multi-Cluster Connection Config authority boundary failed"
            )

        status = run_command(
            root=root,
            name="Git status",
            command=[
                "git",
                "status",
                "--short",
                "--",
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
                    for path in targets
                ],
            ],
        )

        add_command(
            report,
            status,
        )

        section(
            report,
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "Multi-Cluster Connection Config / Registry Factory v1.1 is installed.",
                "",
                "Configured read-plane path:",
                "configs/app.yaml -> Settings.connections.kubernetes_read -> local credential references -> cluster-bound KubernetesTool objects -> KubernetesClusterRegistry -> Multi-Cluster Router -> AgentRuntime",
                "",
                "Default remains disabled and legacy single-cluster behavior remains available.",
                "",
                "Example future YAML (do not paste real token values):",
                "connections:",
                "  kubernetes_read:",
                "    enabled: true",
                "    clusters:",
                "      - cluster_name: prod-sg-17",
                "        api_url: https://sg-kubernetes.example.internal",
                "        bearer_token_env: K8S_PROD_SG_READ_TOKEN",
                "        ca_file: /etc/ai-reliability/k8s/prod-sg-ca.crt",
                "      - cluster_name: prod-us-03",
                "        api_url: https://us-kubernetes.example.internal",
                "        bearer_token_file: /run/secrets/k8s-prod-us-read-token",
                "",
                "Still intentionally not implemented:",
                "- dynamic cluster discovery",
                "- credential rotation/reload",
                "- per-cluster Prometheus routing",
                "- multi-cluster production write routing",
                "",
                "Next recommended step:",
                "- Multi-Cluster Prometheus Read Router v1 so Kubernetes and metrics evidence are routed by the same Incident cluster scope.",
            ]
        )

        write_text(
            after,
            "\n".join(
                report
            )
            + "\n",
        )

        print(
            "=" * 72
        )
        print(
            "MULTI-CLUSTER CONNECTION CONFIG / REGISTRY FACTORY V1.1 PASSED"
        )
        print(
            "=" * 72
        )
        print()
        print(
            "No real LLM/Kubernetes/Prometheus request was sent."
        )
        print()
        print(
            "Upload only:"
        )
        print(
            after
        )

        return 0

    except Exception as exc:
        rollback = []

        for original, backup in reversed(
            backups
        ):
            try:
                shutil.copy2(
                    backup,
                    original,
                )

                rollback.append(
                    "RESTORED "
                    + str(
                        original.relative_to(
                            root
                        )
                    )
                )

            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK FAILED "
                    + str(
                        original.relative_to(
                            root
                        )
                    )
                    + ": "
                    + (
                        f"{type(rollback_exc).__name__}: "
                        f"{rollback_exc}"
                    )
                )

        for path in targets:
            if (
                not preexisting[
                    path
                ]
                and path.exists()
            ):
                try:
                    path.unlink()

                    rollback.append(
                        "REMOVED newly-created "
                        + str(
                            path.relative_to(
                                root
                            )
                        )
                    )

                except Exception as rollback_exc:
                    rollback.append(
                        "ROLLBACK REMOVE FAILED "
                        + str(
                            path.relative_to(
                                root
                            )
                        )
                        + ": "
                        + (
                            f"{type(rollback_exc).__name__}: "
                            f"{rollback_exc}"
                        )
                    )

        write_text(
            error,
            "\n".join(
                [
                    "Multi-Cluster Connection Config / Registry Factory v1.1 FAILED",
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
                    "",
                    "ROLLBACK",
                    "=" * 120,
                    *rollback,
                    "",
                    "PARTIAL REPORT",
                    "=" * 120,
                    *report,
                ]
            )
            + "\n",
        )

        print(
            "=" * 72
        )
        print(
            "MULTI-CLUSTER CONNECTION CONFIG / REGISTRY FACTORY V1.1 FAILED"
        )
        print(
            "=" * 72
        )
        print()
        print(
            "Modified files were rolled back where possible."
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
