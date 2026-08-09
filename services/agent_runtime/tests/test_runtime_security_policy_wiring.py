from pathlib import Path

import pytest

from common.config.settings import (
    AuthenticationConfig,
)

import services.agent_runtime.app.runtime.runtime as runtime_module

from services.agent_runtime.app.security.factory import (
    create_authentication_service,
)
from services.agent_runtime.app.security.models import (
    AuthenticationMethod,
    OperatorIdentity,
    OperatorRole,
    ProtectedOperation,
)
from services.agent_runtime.app.security.policy import (
    SecurityPolicyConfigurationError,
    SecurityPolicyEngine,
)
from services.agent_runtime.app.security.service import (
    AuthenticationService,
)


def create_isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    authentication_service: AuthenticationService | None = None,
    security_policy: SecurityPolicyEngine | None = None,
):
    """
    Keep every default runtime database inside pytest's temporary directory.
    """

    monkeypatch.chdir(
        tmp_path
    )

    return runtime_module.AgentRuntime(
        authentication_service=authentication_service,
        security_policy=security_policy,
    )


def viewer_identity() -> OperatorIdentity:
    return OperatorIdentity(
        principal_id="runtime-viewer",
        authentication_method=(
            AuthenticationMethod.TRUSTED_PROXY
        ),
        roles=frozenset(
            {
                OperatorRole.VIEWER,
            }
        ),
    )


def disabled_authentication_service() -> AuthenticationService:
    return create_authentication_service(
        AuthenticationConfig()
    )


def test_runtime_creates_default_shared_security_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    runtime = create_isolated_runtime(
        monkeypatch,
        tmp_path,
        authentication_service=(
            disabled_authentication_service()
        ),
    )
    viewer = viewer_identity()

    assert isinstance(
        runtime.security_policy,
        SecurityPolicyEngine,
    )
    assert runtime.security_policy.policy_version == "v1"
    assert (
        runtime.security_policy
        is not runtime.policy
    )
    assert runtime.security_policy.authorize(
        viewer,
        ProtectedOperation.READ_INCIDENT,
    ).allowed is True
    assert runtime.security_policy.authorize(
        viewer,
        ProtectedOperation.RESUME_ACTION,
    ).allowed is False


def test_runtime_preserves_explicit_security_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    expected = SecurityPolicyEngine(
        policy_version="runtime-rbac-v2"
    )

    runtime = create_isolated_runtime(
        monkeypatch,
        tmp_path,
        authentication_service=(
            disabled_authentication_service()
        ),
        security_policy=expected,
    )

    assert runtime.security_policy is expected
    assert runtime.security_policy.policy_version == (
        "runtime-rbac-v2"
    )
    assert runtime.security_policy.describe()[
        "policy_version"
    ] == "runtime-rbac-v2"


def test_invalid_security_policy_fails_before_factory_or_components(
    monkeypatch: pytest.MonkeyPatch,
):
    authentication_factory_calls = 0
    component_calls = 0

    def unexpected_authentication_factory():
        nonlocal authentication_factory_calls
        authentication_factory_calls += 1
        raise AssertionError(
            "Invalid policy injection called authentication factory"
        )

    def unexpected_component_creation():
        nonlocal component_calls
        component_calls += 1
        raise AssertionError(
            "Invalid policy injection created a runtime component"
        )

    monkeypatch.setattr(
        runtime_module,
        "create_authentication_service",
        unexpected_authentication_factory,
    )
    monkeypatch.setattr(
        runtime_module,
        "MemoryStore",
        unexpected_component_creation,
    )

    with pytest.raises(
        TypeError,
        match="security policy is invalid",
    ):
        runtime_module.AgentRuntime(
            security_policy=object()
        )

    assert authentication_factory_calls == 0
    assert component_calls == 0


def test_default_policy_failure_is_fail_fast_before_components(
    monkeypatch: pytest.MonkeyPatch,
):
    component_calls = 0

    def fail_security_policy():
        raise SecurityPolicyConfigurationError(
            "Runtime security policy configuration is invalid"
        )

    def unexpected_component_creation():
        nonlocal component_calls
        component_calls += 1
        raise AssertionError(
            "Policy failure created a runtime component"
        )

    monkeypatch.setattr(
        runtime_module,
        "SecurityPolicyEngine",
        fail_security_policy,
    )
    monkeypatch.setattr(
        runtime_module,
        "MemoryStore",
        unexpected_component_creation,
    )

    with pytest.raises(
        SecurityPolicyConfigurationError,
        match="policy configuration is invalid",
    ):
        runtime_module.AgentRuntime(
            authentication_service=(
                disabled_authentication_service()
            )
        )

    assert component_calls == 0


def test_security_policy_does_not_replace_business_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    business_policy = object()
    security_policy = SecurityPolicyEngine(
        policy_version="security-v3"
    )

    monkeypatch.setattr(
        runtime_module,
        "create_policy_engine",
        lambda: business_policy,
    )

    runtime = create_isolated_runtime(
        monkeypatch,
        tmp_path,
        authentication_service=(
            disabled_authentication_service()
        ),
        security_policy=security_policy,
    )

    assert runtime.policy is business_policy
    assert runtime.security_policy is security_policy
    assert runtime.policy is not runtime.security_policy


def test_security_wiring_preserves_existing_shared_services(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    authentication = disabled_authentication_service()
    security_policy = SecurityPolicyEngine()

    runtime = create_isolated_runtime(
        monkeypatch,
        tmp_path,
        authentication_service=authentication,
        security_policy=security_policy,
    )

    assert runtime.authentication is authentication
    assert runtime.security_policy is security_policy
    assert (
        runtime.action_runtime.incident_store
        is runtime.incident_store
    )
    assert (
        runtime.action_runtime.action_execution_service
        is runtime.action_execution_service
    )
    assert (
        runtime.verification_runtime.incident_store
        is runtime.incident_store
    )
    assert (
        runtime.verification_runtime.verification_service
        is runtime.verification
    )
    assert (
        runtime.verification_coordinator.verification_runtime
        is runtime.verification_runtime
    )
    assert (
        runtime.pipeline.incident_store
        is runtime.incident_store
    )
    assert (
        runtime.pipeline.incident_service
        is runtime.incident_service
    )
    assert (
        runtime.pipeline.workflow_service
        is runtime.workflow_service
    )


def test_default_security_policies_are_not_global_singletons(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    authentication = disabled_authentication_service()

    monkeypatch.chdir(
        tmp_path
    )

    first_runtime = runtime_module.AgentRuntime(
        authentication_service=authentication
    )
    second_runtime = runtime_module.AgentRuntime(
        authentication_service=authentication
    )

    assert isinstance(
        first_runtime.security_policy,
        SecurityPolicyEngine,
    )
    assert isinstance(
        second_runtime.security_policy,
        SecurityPolicyEngine,
    )
    assert (
        first_runtime.security_policy
        is not second_runtime.security_policy
    )
    assert (
        first_runtime.security_policy.describe()
        == second_runtime.security_policy.describe()
    )
