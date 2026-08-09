from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from common.config.settings import (
    AuthenticationConfig,
)

from services.agent_runtime.app.security import (
    factory as factory_module,
)
from services.agent_runtime.app.security.authentication import (
    InvalidAuthenticationCredentialsError,
    MissingAuthenticationCredentialsError,
)
from services.agent_runtime.app.security.factory import (
    AuthenticationFactoryConfigurationError,
    create_authentication_provider_registry,
    create_authentication_service,
)
from services.agent_runtime.app.security.models import (
    AuthenticationMethod,
    OperatorRole,
)


PRIMARY_KEY = (
    "authentication-factory-primary-key-000000001"
)


SECONDARY_KEY = (
    "authentication-factory-secondary-key-0000001"
)


def authentication_config(
    *,
    api_keys: list[dict] | None = None,
) -> AuthenticationConfig:
    return AuthenticationConfig.model_validate(
        {
            "enabled": True,
            "default_provider": "api_key",
            "api_keys": (
                api_keys
                or [
                    {
                        "key_id": "primary",
                        "secret_env": (
                            "AI_RELIABILITY_API_KEY_PRIMARY"
                        ),
                        "principal_id": (
                            "sre-operator-1"
                        ),
                        "roles": [
                            "viewer",
                            "approver",
                        ],
                        "display_name": (
                            "Platform SRE"
                        ),
                        "attributes": {
                            "team": "platform",
                            "region": "tw",
                        },
                    }
                ]
            ),
        }
    )


def test_disabled_configuration_creates_reject_all_service():
    service = create_authentication_service(
        AuthenticationConfig(),
        environment=object(),
    )

    assert service.default_provider_name == (
        "disabled"
    )
    assert service.registry.provider_count == 1
    assert service.registry.names == (
        "disabled",
    )

    with pytest.raises(
        MissingAuthenticationCredentialsError
    ):
        service.authenticate(
            None
        )

    with pytest.raises(
        InvalidAuthenticationCredentialsError
    ):
        service.authenticate(
            "any-credential"
        )


def test_disabled_configuration_does_not_read_secret_source():
    class ExplodingSecretSource:
        def get(
            self,
            key,
        ):
            raise AssertionError(
                "Disabled authentication read the secret source"
            )

    service = create_authentication_service(
        AuthenticationConfig(),
        environment=ExplodingSecretSource(),
    )

    assert service.default_provider_name == (
        "disabled"
    )


def test_factory_uses_application_settings_when_config_is_omitted(
    monkeypatch,
):
    settings = SimpleNamespace(
        security=SimpleNamespace(
            authentication=(
                AuthenticationConfig()
            )
        )
    )

    monkeypatch.setattr(
        factory_module,
        "get_settings",
        lambda: settings,
    )

    service = create_authentication_service()

    assert service.default_provider_name == (
        "disabled"
    )


def test_enabled_configuration_builds_api_key_identity():
    config = authentication_config()
    service = create_authentication_service(
        config,
        environment={
            "AI_RELIABILITY_API_KEY_PRIMARY": (
                PRIMARY_KEY
            ),
        },
    )

    identity = service.authenticate(
        PRIMARY_KEY
    )

    assert service.default_provider_name == (
        "api_key"
    )
    assert service.registry.names == (
        "api_key",
    )
    assert identity.principal_id == (
        "sre-operator-1"
    )
    assert identity.authentication_method == (
        AuthenticationMethod.API_KEY
    )
    assert identity.roles == frozenset(
        {
            OperatorRole.VIEWER,
            OperatorRole.APPROVER,
        }
    )
    assert identity.display_name == (
        "Platform SRE"
    )
    assert identity.attributes == {
        "team": "platform",
        "region": "tw",
        "key_id": "primary",
    }
    assert PRIMARY_KEY not in repr(
        service.__dict__
    )
    assert PRIMARY_KEY not in (
        identity.model_dump_json()
    )


def test_registry_factory_and_service_factory_share_provider_shape():
    config = authentication_config()
    environment = {
        "AI_RELIABILITY_API_KEY_PRIMARY": (
            PRIMARY_KEY
        ),
    }

    registry = (
        create_authentication_provider_registry(
            config,
            environment=environment,
        )
    )
    service = create_authentication_service(
        config,
        environment=environment,
    )

    assert registry.default_provider_name == (
        service.default_provider_name
    )
    assert registry.names == (
        service.registry.names
    )
    assert registry.provider_count == (
        service.registry.provider_count
    )


def test_enabled_factory_reads_real_environment_when_not_injected(
    monkeypatch,
):
    config = authentication_config()

    monkeypatch.setenv(
        "AI_RELIABILITY_API_KEY_PRIMARY",
        PRIMARY_KEY,
    )

    service = create_authentication_service(
        config
    )

    assert service.authenticate(
        PRIMARY_KEY
    ).principal_id == "sre-operator-1"


def test_enabled_factory_requires_mapping_secret_source():
    with pytest.raises(
        AuthenticationFactoryConfigurationError,
        match="secret source must be a mapping",
    ):
        create_authentication_service(
            authentication_config(),
            environment=object(),
        )


def test_missing_secret_reference_fails_startup_without_credential():
    config = authentication_config()

    with pytest.raises(
        AuthenticationFactoryConfigurationError
    ) as exc_info:
        create_authentication_service(
            config,
            environment={},
        )

    error_text = str(
        exc_info.value
    )

    assert (
        "AI_RELIABILITY_API_KEY_PRIMARY"
        in error_text
    )
    assert PRIMARY_KEY not in error_text


@pytest.mark.parametrize(
    "secret_value",
    [
        "",
        "   ",
        "too-short",
        "x" * 4097,
        12345,
    ],
)
def test_invalid_secret_value_fails_startup_without_disclosure(
    secret_value,
):
    with pytest.raises(
        AuthenticationFactoryConfigurationError
    ) as exc_info:
        create_authentication_service(
            authentication_config(),
            environment={
                "AI_RELIABILITY_API_KEY_PRIMARY": (
                    secret_value
                ),
            },
        )

    error_text = str(
        exc_info.value
    )

    assert error_text == (
        "Authentication API key configuration is "
        "invalid: primary"
    )

    if isinstance(
        secret_value,
        str,
    ) and secret_value:
        assert secret_value not in error_text


def test_duplicate_actual_credentials_fail_startup_without_disclosure():
    config = authentication_config(
        api_keys=[
            {
                "key_id": "primary",
                "secret_env": "API_KEY_PRIMARY",
                "principal_id": "operator-primary",
                "roles": [
                    "viewer"
                ],
            },
            {
                "key_id": "secondary",
                "secret_env": "API_KEY_SECONDARY",
                "principal_id": "operator-secondary",
                "roles": [
                    "viewer"
                ],
            },
        ]
    )

    with pytest.raises(
        AuthenticationFactoryConfigurationError
    ) as exc_info:
        create_authentication_service(
            config,
            environment={
                "API_KEY_PRIMARY": PRIMARY_KEY,
                "API_KEY_SECONDARY": PRIMARY_KEY,
            },
        )

    assert str(
        exc_info.value
    ) == (
        "Authentication API key provider "
        "configuration is invalid"
    )
    assert PRIMARY_KEY not in str(
        exc_info.value
    )


@pytest.mark.parametrize(
    "key_state",
    [
        {
            "active": False
        },
        {
            "expires_at": (
                datetime.now(
                    UTC
                )
                - timedelta(
                    seconds=1
                )
            )
        },
    ],
)
def test_factory_rejects_configuration_without_usable_key(
    key_state: dict,
):
    key_config = {
        "key_id": "unusable",
        "secret_env": "API_KEY_UNUSABLE",
        "principal_id": "operator",
        "roles": [
            "viewer"
        ],
        **key_state,
    }
    config = authentication_config(
        api_keys=[
            key_config
        ]
    )

    with pytest.raises(
        AuthenticationFactoryConfigurationError,
        match="active, unexpired API key",
    ):
        create_authentication_service(
            config,
            environment={
                "API_KEY_UNUSABLE": PRIMARY_KEY,
            },
        )


def test_key_rotation_accepts_active_key_and_rejects_inactive_key():
    config = authentication_config(
        api_keys=[
            {
                "key_id": "old",
                "secret_env": "API_KEY_OLD",
                "principal_id": "old-operator",
                "roles": [
                    "viewer"
                ],
                "active": False,
            },
            {
                "key_id": "current",
                "secret_env": "API_KEY_CURRENT",
                "principal_id": "current-operator",
                "roles": [
                    "executor"
                ],
            },
        ]
    )
    service = create_authentication_service(
        config,
        environment={
            "API_KEY_OLD": PRIMARY_KEY,
            "API_KEY_CURRENT": SECONDARY_KEY,
        },
    )

    identity = service.authenticate(
        SECONDARY_KEY
    )

    assert identity.principal_id == (
        "current-operator"
    )
    assert identity.roles == frozenset(
        {
            OperatorRole.EXECUTOR
        }
    )

    with pytest.raises(
        InvalidAuthenticationCredentialsError
    ) as exc_info:
        service.authenticate(
            PRIMARY_KEY
        )

    assert str(
        exc_info.value
    ) == "Authentication credentials are invalid"


def test_factory_rejects_unvalidated_configuration_object():
    with pytest.raises(
        AuthenticationFactoryConfigurationError,
        match="requires validated configuration",
    ):
        create_authentication_service(
            object(),
            environment={},
        )
