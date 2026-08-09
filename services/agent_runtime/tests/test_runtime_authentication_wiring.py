from pathlib import Path

import pytest

from common.config.settings import (
    AuthenticationConfig,
)

import services.agent_runtime.app.runtime.runtime as runtime_module

from services.agent_runtime.app.security.authentication import (
    InvalidAuthenticationCredentialsError,
    MissingAuthenticationCredentialsError,
)
from services.agent_runtime.app.security.factory import (
    AuthenticationFactoryConfigurationError,
    create_authentication_service,
)
from services.agent_runtime.app.security.models import (
    AuthenticationMethod,
    OperatorRole,
)
from services.agent_runtime.app.security.service import (
    AuthenticationService,
)


API_KEY = (
    "runtime-authentication-wiring-key-000000001"
)


def create_isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    authentication_service: AuthenticationService | None = None,
):
    """
    Keep every default runtime database inside pytest's temporary directory.
    """

    monkeypatch.chdir(
        tmp_path
    )

    return runtime_module.AgentRuntime(
        authentication_service=authentication_service
    )


def enabled_authentication_service() -> AuthenticationService:
    config = AuthenticationConfig.model_validate(
        {
            "enabled": True,
            "default_provider": "api_key",
            "api_keys": [
                {
                    "key_id": "runtime-primary",
                    "secret_env": (
                        "AI_RELIABILITY_RUNTIME_API_KEY"
                    ),
                    "principal_id": "runtime-sre-1",
                    "roles": [
                        "viewer",
                        "executor",
                    ],
                    "display_name": "Runtime SRE",
                    "attributes": {
                        "team": "platform",
                    },
                }
            ],
        }
    )

    return create_authentication_service(
        config,
        environment={
            "AI_RELIABILITY_RUNTIME_API_KEY": API_KEY,
        },
    )


def test_runtime_creates_one_shared_authentication_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    expected = create_authentication_service(
        AuthenticationConfig()
    )
    factory_calls = 0

    def create_service() -> AuthenticationService:
        nonlocal factory_calls
        factory_calls += 1
        return expected

    monkeypatch.setattr(
        runtime_module,
        "create_authentication_service",
        create_service,
    )

    runtime = create_isolated_runtime(
        monkeypatch,
        tmp_path,
    )

    assert factory_calls == 1
    assert runtime.authentication is expected
    assert runtime.authentication.default_provider_name == (
        "disabled"
    )

    with pytest.raises(
        MissingAuthenticationCredentialsError
    ):
        runtime.authentication.authenticate(
            None
        )

    with pytest.raises(
        InvalidAuthenticationCredentialsError
    ):
        runtime.authentication.authenticate(
            API_KEY
        )


def test_runtime_preserves_explicit_authentication_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    expected = enabled_authentication_service()

    def unexpected_factory_call():
        raise AssertionError(
            "Explicit authentication injection called the factory"
        )

    monkeypatch.setattr(
        runtime_module,
        "create_authentication_service",
        unexpected_factory_call,
    )

    runtime = create_isolated_runtime(
        monkeypatch,
        tmp_path,
        authentication_service=expected,
    )

    identity = runtime.authentication.authenticate(
        API_KEY
    )

    assert runtime.authentication is expected
    assert identity.principal_id == "runtime-sre-1"
    assert identity.authentication_method == (
        AuthenticationMethod.API_KEY
    )
    assert identity.roles == frozenset(
        {
            OperatorRole.VIEWER,
            OperatorRole.EXECUTOR,
        }
    )
    assert identity.attributes == {
        "team": "platform",
        "key_id": "runtime-primary",
    }
    assert API_KEY not in repr(
        runtime.authentication.__dict__
    )
    assert API_KEY not in identity.model_dump_json()


def test_invalid_injection_fails_before_factory_or_components(
    monkeypatch: pytest.MonkeyPatch,
):
    factory_calls = 0
    component_calls = 0

    def unexpected_factory_call():
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError(
            "Invalid injection called the authentication factory"
        )

    def unexpected_component_creation():
        nonlocal component_calls
        component_calls += 1
        raise AssertionError(
            "Invalid injection created a runtime component"
        )

    monkeypatch.setattr(
        runtime_module,
        "create_authentication_service",
        unexpected_factory_call,
    )
    monkeypatch.setattr(
        runtime_module,
        "MemoryStore",
        unexpected_component_creation,
    )

    with pytest.raises(
        TypeError,
        match="authentication service is invalid",
    ):
        runtime_module.AgentRuntime(
            authentication_service=object()
        )

    assert factory_calls == 0
    assert component_calls == 0


def test_factory_failure_is_fail_fast_before_components(
    monkeypatch: pytest.MonkeyPatch,
):
    component_calls = 0

    def fail_authentication_factory():
        raise AuthenticationFactoryConfigurationError(
            "Authentication startup configuration is invalid"
        )

    def unexpected_component_creation():
        nonlocal component_calls
        component_calls += 1
        raise AssertionError(
            "Factory failure created a runtime component"
        )

    monkeypatch.setattr(
        runtime_module,
        "create_authentication_service",
        fail_authentication_factory,
    )
    monkeypatch.setattr(
        runtime_module,
        "MemoryStore",
        unexpected_component_creation,
    )

    with pytest.raises(
        AuthenticationFactoryConfigurationError,
        match="startup configuration is invalid",
    ):
        runtime_module.AgentRuntime()

    assert component_calls == 0


def test_authentication_wiring_preserves_existing_shared_services(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    authentication = create_authentication_service(
        AuthenticationConfig()
    )

    runtime = create_isolated_runtime(
        monkeypatch,
        tmp_path,
        authentication_service=authentication,
    )

    assert runtime.authentication is authentication
    assert (
        runtime.incident_service.store
        is runtime.incident_store
    )
    assert (
        runtime.workflow_service.incident_service
        is runtime.incident_service
    )
    assert (
        runtime.action_execution_service.store
        is runtime.action_execution_store
    )
    assert (
        runtime.action_runtime.approval
        is runtime.approval
    )
    assert (
        runtime.action_runtime.incident_store
        is runtime.incident_store
    )
    assert (
        runtime.action_runtime.action_execution_service
        is runtime.action_execution_service
    )
    assert (
        runtime.verification.store
        is runtime.verification_store
    )
    assert (
        runtime.verification_runtime.verification_service
        is runtime.verification
    )
    assert (
        runtime.verification_runtime.incident_store
        is runtime.incident_store
    )
    assert (
        runtime.verification_coordinator.profile_factory
        is runtime.verification_profile_factory
    )
    assert (
        runtime.verification_coordinator.collector
        is runtime.verification_collector
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


def test_runtime_authentication_instances_are_not_global_singletons(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    created: list[AuthenticationService] = []

    def create_service() -> AuthenticationService:
        service = create_authentication_service(
            AuthenticationConfig()
        )
        created.append(
            service
        )
        return service

    monkeypatch.setattr(
        runtime_module,
        "create_authentication_service",
        create_service,
    )
    monkeypatch.chdir(
        tmp_path
    )

    first_runtime = runtime_module.AgentRuntime()
    second_runtime = runtime_module.AgentRuntime()

    assert len(created) == 2
    assert first_runtime.authentication is created[0]
    assert second_runtime.authentication is created[1]
    assert (
        first_runtime.authentication
        is not second_runtime.authentication
    )
