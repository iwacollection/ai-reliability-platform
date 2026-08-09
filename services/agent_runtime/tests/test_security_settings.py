from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from common.config.settings import (
    AuthenticationApiKeyConfig,
    AuthenticationConfig,
    SecurityConfig,
    Settings,
)


def legacy_settings_payload() -> dict:
    return {
        "app": {
            "name": "ai-reliability-platform",
            "version": "0.1.0",
        },
        "llm": {
            "provider": "mock",
            "temperature": 0.0,
            "timeout": 30,
        },
        "runtime": {
            "pipeline": "planner",
            "max_workers": 4,
        },
    }


def api_key_payload(
    **overrides,
) -> dict:
    payload = {
        "key_id": "primary",
        "secret_env": (
            "AI_RELIABILITY_API_KEY_PRIMARY"
        ),
        "principal_id": "sre-operator-1",
        "roles": [
            "viewer",
            "approver",
        ],
        "display_name": "Platform SRE",
        "attributes": {
            "team": "platform",
            "region": "tw",
        },
    }
    payload.update(
        overrides
    )
    return payload


def test_legacy_settings_without_security_remain_compatible():
    settings = Settings.model_validate(
        legacy_settings_payload()
    )

    authentication = (
        settings.security.authentication
    )

    assert authentication.enabled is False
    assert authentication.default_provider == (
        "api_key"
    )
    assert authentication.api_keys == ()


def test_enabled_authentication_parses_non_secret_reference():
    payload = legacy_settings_payload()
    payload[
        "security"
    ] = {
        "authentication": {
            "enabled": True,
            "default_provider": "api_key",
            "api_keys": [
                api_key_payload()
            ],
        }
    }

    settings = Settings.model_validate(
        payload
    )
    authentication = (
        settings.security.authentication
    )
    item = authentication.api_keys[
        0
    ]

    assert authentication.enabled is True
    assert authentication.default_provider == (
        "api_key"
    )
    assert item.key_id == "primary"
    assert item.secret_env == (
        "AI_RELIABILITY_API_KEY_PRIMARY"
    )
    assert item.roles == frozenset(
        {
            "viewer",
            "approver",
        }
    )
    assert item.attributes == {
        "team": "platform",
        "region": "tw",
    }


def test_settings_schema_cannot_store_plaintext_api_key():
    payload = api_key_payload(
        api_key=(
            "must-never-enter-settings"
        )
    )

    with pytest.raises(
        ValidationError
    ):
        AuthenticationApiKeyConfig.model_validate(
            payload
        )

    assert "api_key" not in (
        AuthenticationApiKeyConfig.model_fields
    )


def test_serialized_settings_contain_reference_but_not_secret_value():
    actual_secret = (
        "actual-secret-value-must-not-persist"
    )
    config = AuthenticationConfig.model_validate(
        {
            "enabled": True,
            "api_keys": [
                api_key_payload()
            ],
        }
    )

    serialized = config.model_dump_json()

    assert (
        "AI_RELIABILITY_API_KEY_PRIMARY"
        in serialized
    )
    assert actual_secret not in serialized
    assert "actual_secret" not in serialized


@pytest.mark.parametrize(
    "secret_env",
    [
        "",
        "AB",
        "lowercase_api_key",
        "1API_KEY",
        "API-KEY",
        " API_KEY",
        "API_KEY ",
        "A" * 129,
        None,
        12345,
    ],
)
def test_secret_environment_name_must_be_canonical(
    secret_env,
):
    with pytest.raises(
        ValidationError,
        match="secret environment name is invalid",
    ):
        AuthenticationApiKeyConfig.model_validate(
            api_key_payload(
                secret_env=secret_env
            )
        )


def test_roles_are_normalized_and_frozen():
    config = (
        AuthenticationApiKeyConfig.model_validate(
            api_key_payload(
                roles=[
                    " VIEWER ",
                    "Approver",
                    "viewer",
                ]
            )
        )
    )

    assert config.roles == frozenset(
        {
            "viewer",
            "approver",
        }
    )


@pytest.mark.parametrize(
    "roles",
    [
        "viewer",
        [],
        None,
        [
            "owner"
        ],
        [
            "viewer",
            "super-admin",
        ],
        12345,
    ],
)
def test_invalid_or_unknown_roles_fail_closed(
    roles,
):
    with pytest.raises(
        ValidationError
    ):
        AuthenticationApiKeyConfig.model_validate(
            api_key_payload(
                roles=roles
            )
        )


def test_identity_text_and_expiry_are_normalized():
    expiry = datetime(
        2026,
        8,
        9,
        8,
        0,
        tzinfo=timezone(
            timedelta(
                hours=8
            )
        ),
    )
    config = (
        AuthenticationApiKeyConfig.model_validate(
            api_key_payload(
                key_id=" primary ",
                principal_id=" operator-1 ",
                display_name=" Platform SRE ",
                expires_at=expiry,
            )
        )
    )

    assert config.key_id == "primary"
    assert config.principal_id == (
        "operator-1"
    )
    assert config.display_name == (
        "Platform SRE"
    )
    assert config.expires_at == datetime(
        2026,
        8,
        9,
        0,
        0,
        tzinfo=UTC,
    )


@pytest.mark.parametrize(
    "expires_at",
    [
        datetime(
            2026,
            8,
            9,
            0,
            0,
        ),
        "2026-08-09T00:00:00",
    ],
)
def test_expiry_requires_timezone(
    expires_at,
):
    with pytest.raises(
        ValidationError,
        match="expiry must be timezone-aware",
    ):
        AuthenticationApiKeyConfig.model_validate(
            api_key_payload(
                expires_at=expires_at
            )
        )


@pytest.mark.parametrize(
    "attribute_name",
    [
        "authorization",
        "access_token",
        "refresh-token",
        "api_key",
        "apiKey",
        "client_secret",
        "password_hash",
        "credential_id",
        "private_key_pem",
    ],
)
def test_attributes_reject_credentials_and_secrets(
    attribute_name: str,
):
    with pytest.raises(
        ValidationError,
        match="must not contain credentials or secrets",
    ):
        AuthenticationApiKeyConfig.model_validate(
            api_key_payload(
                attributes={
                    attribute_name: (
                        "must-not-persist"
                    ),
                }
            )
        )


def test_enabled_authentication_requires_at_least_one_key():
    with pytest.raises(
        ValidationError,
        match="requires at least one key",
    ):
        AuthenticationConfig(
            enabled=True
        )


@pytest.mark.parametrize(
    "api_keys",
    [
        [
            api_key_payload(
                key_id="duplicate",
                secret_env="API_KEY_ONE",
            ),
            api_key_payload(
                key_id="duplicate",
                secret_env="API_KEY_TWO",
            ),
        ],
        [
            api_key_payload(
                key_id="one",
                secret_env="API_KEY_SHARED",
            ),
            api_key_payload(
                key_id="two",
                secret_env="API_KEY_SHARED",
            ),
        ],
    ],
)
def test_key_ids_and_environment_references_must_be_unique(
    api_keys: list[dict],
):
    with pytest.raises(
        ValidationError
    ):
        AuthenticationConfig.model_validate(
            {
                "enabled": True,
                "api_keys": api_keys,
            }
        )


@pytest.mark.parametrize(
    "default_provider",
    [
        "",
        " api_key",
        "api_key ",
        "API_KEY",
        "oidc",
        "1provider",
        None,
        12345,
    ],
)
def test_default_provider_must_be_valid_and_configured(
    default_provider,
):
    with pytest.raises(
        ValidationError
    ):
        AuthenticationConfig.model_validate(
            {
                "enabled": False,
                "default_provider": (
                    default_provider
                ),
            }
        )


def test_security_configuration_models_are_immutable_and_forbid_extra():
    key_config = (
        AuthenticationApiKeyConfig.model_validate(
            api_key_payload()
        )
    )
    authentication = AuthenticationConfig(
        enabled=True,
        api_keys=(
            key_config,
        ),
    )

    with pytest.raises(
        ValidationError
    ):
        key_config.principal_id = "mutated"

    with pytest.raises(
        ValidationError
    ):
        authentication.enabled = False

    with pytest.raises(
        ValidationError
    ):
        SecurityConfig.model_validate(
            {
                "authentication": {},
                "unexpected": True,
            }
        )
