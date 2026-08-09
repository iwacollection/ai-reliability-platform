from collections.abc import Iterable
from re import fullmatch
from types import MappingProxyType

from services.agent_runtime.app.security.authentication import (
    AuthenticationError,
    BaseAuthenticationProvider,
)
from services.agent_runtime.app.security.models import (
    AuthenticationMethod,
    OperatorIdentity,
)


_PROVIDER_NAME_PATTERN = (
    r"[a-z][a-z0-9_.-]{0,127}"
)


_PROVIDER_EXECUTION_FAILED = object()


class AuthenticationServiceConfigurationError(
    ValueError
):
    """Raised when provider orchestration cannot start safely."""


class AuthenticationProviderUnavailableError(
    AuthenticationError
):
    """Credential-safe failure for an unknown or unavailable provider."""

    code = "authentication_provider_unavailable"
    safe_message = "Authentication failed"


class AuthenticationProviderExecutionError(
    AuthenticationError
):
    """Credential-safe failure for an unexpected provider exception."""

    code = "authentication_provider_execution_failed"
    safe_message = "Authentication failed"


class AuthenticationProviderContractError(
    AuthenticationError
):
    """Credential-safe failure for an invalid provider result."""

    code = "authentication_provider_contract_invalid"
    safe_message = "Authentication failed"


def _configured_provider_name(
    value: object,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise AuthenticationServiceConfigurationError(
            "Authentication provider name must be text"
        )

    if (
        value != value.strip()
        or fullmatch(
            _PROVIDER_NAME_PATTERN,
            value,
        )
        is None
    ):
        raise AuthenticationServiceConfigurationError(
            "Authentication provider name is invalid"
        )

    return value


class AuthenticationProviderRegistry:
    """
    Immutable startup registry for authentication providers.

    The registry exposes only provider names and explicit resolution. Provider
    objects cannot be added or replaced after construction, preventing runtime
    policy drift and accidental fallback to a different credential type.
    """

    def __init__(
        self,
        providers: Iterable[
            BaseAuthenticationProvider
        ],
        *,
        default_provider_name: str | None = None,
    ) -> None:
        try:
            configured_providers = tuple(
                providers
            )
        except Exception:
            raise AuthenticationServiceConfigurationError(
                "Authentication provider configuration is invalid"
            ) from None

        if not configured_providers:
            raise AuthenticationServiceConfigurationError(
                "At least one authentication provider is required"
            )

        providers_by_name: dict[
            str,
            BaseAuthenticationProvider,
        ] = {}

        for provider in configured_providers:
            if not isinstance(
                provider,
                BaseAuthenticationProvider,
            ):
                raise AuthenticationServiceConfigurationError(
                    "Authentication provider does not implement "
                    "the required contract"
                )

            try:
                provider_name = (
                    _configured_provider_name(
                        provider.name
                    )
                )
            except AuthenticationServiceConfigurationError:
                raise
            except Exception:
                raise AuthenticationServiceConfigurationError(
                    "Authentication provider name cannot be resolved"
                ) from None

            if provider_name in providers_by_name:
                raise AuthenticationServiceConfigurationError(
                    "Duplicate authentication provider name"
                )

            providers_by_name[
                provider_name
            ] = provider

        if default_provider_name is None:
            if len(
                providers_by_name
            ) != 1:
                raise AuthenticationServiceConfigurationError(
                    "Multiple authentication providers require an "
                    "explicit default provider"
                )

            resolved_default_name = next(
                iter(
                    providers_by_name
                )
            )
        else:
            resolved_default_name = (
                _configured_provider_name(
                    default_provider_name
                )
            )

            if (
                resolved_default_name
                not in providers_by_name
            ):
                raise AuthenticationServiceConfigurationError(
                    "Default authentication provider is not registered"
                )

        self._providers = MappingProxyType(
            providers_by_name
        )
        self._default_provider_name = (
            resolved_default_name
        )

    @property
    def default_provider_name(
        self,
    ) -> str:
        return self._default_provider_name

    @property
    def provider_count(
        self,
    ) -> int:
        return len(
            self._providers
        )

    @property
    def names(
        self,
    ) -> tuple[str, ...]:
        return tuple(
            sorted(
                self._providers
            )
        )

    def get(
        self,
        provider_name: str | None = None,
    ) -> BaseAuthenticationProvider:
        if provider_name is None:
            resolved_name = (
                self._default_provider_name
            )
        else:
            if (
                not isinstance(
                    provider_name,
                    str,
                )
                or provider_name
                != provider_name.strip()
                or fullmatch(
                    _PROVIDER_NAME_PATTERN,
                    provider_name,
                )
                is None
            ):
                raise AuthenticationProviderUnavailableError()

            resolved_name = provider_name

        provider = self._providers.get(
            resolved_name
        )

        if provider is None:
            raise AuthenticationProviderUnavailableError()

        return provider


class AuthenticationService:
    """
    Request-framework-neutral authentication orchestration service.

    Credential extraction belongs to an API adapter. This service receives a
    credential, resolves the selected provider, and returns a validated trusted
    identity without retaining the credential.
    """

    def __init__(
        self,
        registry: AuthenticationProviderRegistry,
    ) -> None:
        if not isinstance(
            registry,
            AuthenticationProviderRegistry,
        ):
            raise AuthenticationServiceConfigurationError(
                "Authentication service requires a provider registry"
            )

        self._registry = registry

    @property
    def registry(
        self,
    ) -> AuthenticationProviderRegistry:
        return self._registry

    @property
    def default_provider_name(
        self,
    ) -> str:
        return (
            self._registry.default_provider_name
        )

    def authenticate(
        self,
        credential: str | None,
        *,
        provider_name: str | None = None,
    ) -> OperatorIdentity:
        provider = self._registry.get(
            provider_name
        )

        identity = self._authenticate_provider(
            provider,
            credential,
        )

        if identity is _PROVIDER_EXECUTION_FAILED:
            raise AuthenticationProviderExecutionError()

        if (
            not isinstance(
                identity,
                OperatorIdentity,
            )
            or not identity.authenticated
            or identity.authentication_method
            == AuthenticationMethod.ANONYMOUS
        ):
            raise AuthenticationProviderContractError()

        return identity

    @staticmethod
    def _authenticate_provider(
        provider: BaseAuthenticationProvider,
        credential: str | None,
    ) -> OperatorIdentity | object:
        try:
            return provider.authenticate(
                credential
            )
        except AuthenticationError:
            raise
        except Exception:
            # Return a private sentinel so the safe public error is raised
            # after leaving the exception handler. This prevents a provider
            # exception containing credentials from becoming __context__.
            return _PROVIDER_EXECUTION_FAILED


__all__ = [
    "AuthenticationProviderContractError",
    "AuthenticationProviderExecutionError",
    "AuthenticationProviderRegistry",
    "AuthenticationProviderUnavailableError",
    "AuthenticationService",
    "AuthenticationServiceConfigurationError",
]
