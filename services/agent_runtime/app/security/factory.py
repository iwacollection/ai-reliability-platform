from collections.abc import Mapping
from os import environ

from common.config import get_settings
from common.config.settings import (
    AuthenticationConfig,
)

from services.agent_runtime.app.security.authentication import (
    ApiKeyAuthenticationProvider,
    ApiKeyRecord,
    AuthenticationConfigurationError,
    BaseAuthenticationProvider,
    InvalidAuthenticationCredentialsError,
    MissingAuthenticationCredentialsError,
)
from services.agent_runtime.app.security.models import (
    OperatorIdentity,
    OperatorRole,
)
from services.agent_runtime.app.security.service import (
    AuthenticationProviderRegistry,
    AuthenticationService,
    AuthenticationServiceConfigurationError,
)


class AuthenticationFactoryConfigurationError(
    AuthenticationServiceConfigurationError
):
    """Raised when authentication cannot be assembled safely at startup."""


class _RejectAllAuthenticationProvider(
    BaseAuthenticationProvider
):
    """Fail-closed provider used while authentication is not enabled."""

    @property
    def name(
        self,
    ) -> str:
        return "disabled"

    def authenticate(
        self,
        credential: str | None,
    ) -> OperatorIdentity:
        if credential is None:
            raise MissingAuthenticationCredentialsError()

        raise InvalidAuthenticationCredentialsError()


def _resolve_authentication_config(
    config: AuthenticationConfig | None,
) -> AuthenticationConfig:
    if config is None:
        resolved_config = (
            get_settings()
            .security
            .authentication
        )
    else:
        resolved_config = config

    if not isinstance(
        resolved_config,
        AuthenticationConfig,
    ):
        raise AuthenticationFactoryConfigurationError(
            "Authentication factory requires validated configuration"
        )

    return resolved_config


def _resolve_environment(
    environment: Mapping[
        str,
        str,
    ] | None,
) -> Mapping[str, str]:
    resolved_environment = (
        environ
        if environment is None
        else environment
    )

    if not isinstance(
        resolved_environment,
        Mapping,
    ):
        raise AuthenticationFactoryConfigurationError(
            "Authentication secret source must be a mapping"
        )

    return resolved_environment


def _build_api_key_provider(
    config: AuthenticationConfig,
    environment: Mapping[
        str,
        str,
    ],
) -> ApiKeyAuthenticationProvider:
    records: list[
        ApiKeyRecord
    ] = []

    for item in config.api_keys:
        secret_value = environment.get(
            item.secret_env
        )

        if secret_value is None:
            raise AuthenticationFactoryConfigurationError(
                "Authentication secret environment variable is "
                f"missing: {item.secret_env}"
            )

        try:
            record = ApiKeyRecord.from_plaintext(
                key_id=item.key_id,
                api_key=secret_value,
                principal_id=item.principal_id,
                roles={
                    OperatorRole(
                        role
                    )
                    for role in item.roles
                },
                display_name=item.display_name,
                active=item.active,
                expires_at=item.expires_at,
                attributes=dict(
                    item.attributes
                ),
            )
        except Exception:
            # Underlying validation errors must never serialize or expose the
            # environment value used as the credential.
            raise AuthenticationFactoryConfigurationError(
                "Authentication API key configuration is invalid: "
                f"{item.key_id}"
            ) from None

        records.append(
            record
        )

    try:
        provider = ApiKeyAuthenticationProvider(
            records
        )
    except AuthenticationConfigurationError:
        raise AuthenticationFactoryConfigurationError(
            "Authentication API key provider configuration is invalid"
        ) from None

    if not any(
        record.active
        and not record.is_expired()
        for record in records
    ):
        raise AuthenticationFactoryConfigurationError(
            "Authentication requires at least one active, unexpired API key"
        )

    return provider


def create_authentication_provider_registry(
    config: AuthenticationConfig | None = None,
    *,
    environment: Mapping[
        str,
        str,
    ] | None = None,
) -> AuthenticationProviderRegistry:
    """
    Build an immutable provider registry from validated application settings.

    When authentication is disabled, return a reject-all registry. No secret
    source is read in that state, and anonymous access is never introduced.
    """

    resolved_config = (
        _resolve_authentication_config(
            config
        )
    )

    if not resolved_config.enabled:
        return AuthenticationProviderRegistry(
            [
                _RejectAllAuthenticationProvider()
            ]
        )

    resolved_environment = (
        _resolve_environment(
            environment
        )
    )

    api_key_provider = (
        _build_api_key_provider(
            resolved_config,
            resolved_environment,
        )
    )

    try:
        return AuthenticationProviderRegistry(
            [
                api_key_provider
            ],
            default_provider_name=(
                resolved_config.default_provider
            ),
        )
    except AuthenticationServiceConfigurationError:
        raise AuthenticationFactoryConfigurationError(
            "Authentication provider registry configuration is invalid"
        ) from None


def create_authentication_service(
    config: AuthenticationConfig | None = None,
    *,
    environment: Mapping[
        str,
        str,
    ] | None = None,
) -> AuthenticationService:
    """Create the shared authentication service used by runtime adapters."""

    registry = (
        create_authentication_provider_registry(
            config,
            environment=environment,
        )
    )

    return AuthenticationService(
        registry
    )


__all__ = [
    "AuthenticationFactoryConfigurationError",
    "create_authentication_provider_registry",
    "create_authentication_service",
]
