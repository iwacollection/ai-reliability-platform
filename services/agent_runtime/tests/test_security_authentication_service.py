from types import MappingProxyType

import pytest

from services.agent_runtime.app.security.authentication import (
    ApiKeyAuthenticationProvider,
    ApiKeyRecord,
    BaseAuthenticationProvider,
    InvalidAuthenticationCredentialsError,
    MissingAuthenticationCredentialsError,
)
from services.agent_runtime.app.security.models import (
    AuthenticationMethod,
    OperatorIdentity,
    OperatorRole,
)
from services.agent_runtime.app.security.service import (
    AuthenticationProviderContractError,
    AuthenticationProviderExecutionError,
    AuthenticationProviderRegistry,
    AuthenticationProviderUnavailableError,
    AuthenticationService,
    AuthenticationServiceConfigurationError,
)


PRIMARY_KEY = (
    "authentication-service-primary-key-000000001"
)


class StubAuthenticationProvider(
    BaseAuthenticationProvider
):
    def __init__(
        self,
        *,
        provider_name: str,
        identity: object = None,
        error: Exception | None = None,
    ) -> None:
        self._provider_name = provider_name
        self._identity = identity
        self._error = error
        self.call_count = 0

    @property
    def name(
        self,
    ) -> str:
        return self._provider_name

    def authenticate(
        self,
        credential: str | None,
    ) -> OperatorIdentity:
        self.call_count += 1

        if self._error is not None:
            raise self._error

        return self._identity


class ExplodingNameProvider(
    BaseAuthenticationProvider
):
    @property
    def name(
        self,
    ) -> str:
        raise RuntimeError(
            "provider-name-secret"
        )

    def authenticate(
        self,
        credential: str | None,
    ) -> OperatorIdentity:
        raise AssertionError(
            "Provider must not be called"
        )


def api_key_provider() -> ApiKeyAuthenticationProvider:
    record = ApiKeyRecord.from_plaintext(
        key_id="primary",
        api_key=PRIMARY_KEY,
        principal_id="sre-operator-1",
        roles={
            OperatorRole.APPROVER
        },
        attributes={
            "team": "platform"
        },
    )

    return ApiKeyAuthenticationProvider(
        [
            record
        ]
    )


def oidc_identity() -> OperatorIdentity:
    return OperatorIdentity(
        principal_id="oidc-operator-1",
        authentication_method=(
            AuthenticationMethod.OIDC_JWT
        ),
        roles={
            OperatorRole.VIEWER
        },
    )


def test_single_provider_becomes_default_automatically():
    provider = api_key_provider()
    registry = AuthenticationProviderRegistry(
        [
            provider
        ]
    )

    assert registry.default_provider_name == (
        "api_key"
    )
    assert registry.provider_count == 1
    assert registry.names == (
        "api_key",
    )
    assert registry.get() is provider
    assert registry.get(
        "api_key"
    ) is provider


def test_multiple_providers_require_and_use_explicit_default():
    api_key = api_key_provider()
    oidc = StubAuthenticationProvider(
        provider_name="oidc",
        identity=oidc_identity(),
    )

    with pytest.raises(
        AuthenticationServiceConfigurationError,
        match="explicit default provider",
    ):
        AuthenticationProviderRegistry(
            [
                api_key,
                oidc,
            ]
        )

    registry = AuthenticationProviderRegistry(
        [
            api_key,
            oidc,
        ],
        default_provider_name="oidc",
    )

    assert registry.default_provider_name == (
        "oidc"
    )
    assert registry.provider_count == 2
    assert registry.names == (
        "api_key",
        "oidc",
    )
    assert registry.get() is oidc
    assert registry.get(
        "api_key"
    ) is api_key


@pytest.mark.parametrize(
    "providers",
    [
        [],
        [
            object()
        ],
        [
            ExplodingNameProvider()
        ],
        [
            StubAuthenticationProvider(
                provider_name="Invalid Name",
                identity=oidc_identity(),
            )
        ],
        [
            StubAuthenticationProvider(
                provider_name=" api_key",
                identity=oidc_identity(),
            )
        ],
        [
            StubAuthenticationProvider(
                provider_name="1provider",
                identity=oidc_identity(),
            )
        ],
    ],
)
def test_registry_rejects_invalid_provider_configuration(
    providers,
):
    with pytest.raises(
        AuthenticationServiceConfigurationError
    ) as exc_info:
        AuthenticationProviderRegistry(
            providers
        )

    assert "provider-name-secret" not in str(
        exc_info.value
    )


def test_registry_rejects_duplicate_provider_names():
    first = StubAuthenticationProvider(
        provider_name="oidc",
        identity=oidc_identity(),
    )
    second = StubAuthenticationProvider(
        provider_name="oidc",
        identity=oidc_identity(),
    )

    with pytest.raises(
        AuthenticationServiceConfigurationError,
        match="Duplicate authentication provider name",
    ):
        AuthenticationProviderRegistry(
            [
                first,
                second,
            ]
        )


@pytest.mark.parametrize(
    "default_provider_name",
    [
        "missing",
        "",
        " oidc",
        "OIDC",
        12345,
    ],
)
def test_registry_rejects_invalid_or_unregistered_default(
    default_provider_name,
):
    provider = StubAuthenticationProvider(
        provider_name="oidc",
        identity=oidc_identity(),
    )

    with pytest.raises(
        AuthenticationServiceConfigurationError
    ):
        AuthenticationProviderRegistry(
            [
                provider
            ],
            default_provider_name=(
                default_provider_name
            ),
        )


def test_registry_inventory_is_sorted_and_mapping_is_immutable():
    registry = AuthenticationProviderRegistry(
        [
            StubAuthenticationProvider(
                provider_name="z_provider",
                identity=oidc_identity(),
            ),
            StubAuthenticationProvider(
                provider_name="a-provider",
                identity=oidc_identity(),
            ),
        ],
        default_provider_name="a-provider",
    )

    assert registry.names == (
        "a-provider",
        "z_provider",
    )
    assert isinstance(
        registry._providers,
        MappingProxyType,
    )

    with pytest.raises(
        TypeError
    ):
        registry._providers[
            "new-provider"
        ] = api_key_provider()


@pytest.mark.parametrize(
    "provider_name",
    [
        "unknown",
        "",
        "   ",
        " api_key",
        "api_key ",
        "API_KEY",
        12345,
    ],
)
def test_unknown_provider_selection_fails_closed_without_enumeration(
    provider_name,
):
    registry = AuthenticationProviderRegistry(
        [
            api_key_provider()
        ]
    )

    with pytest.raises(
        AuthenticationProviderUnavailableError
    ) as exc_info:
        registry.get(
            provider_name
        )

    assert exc_info.value.code == (
        "authentication_provider_unavailable"
    )
    assert str(
        exc_info.value
    ) == "Authentication failed"
    assert "api_key" not in str(
        exc_info.value
    )

    if isinstance(
        provider_name,
        str,
    ) and provider_name:
        assert provider_name not in str(
            exc_info.value
        )


def test_authentication_service_authenticates_with_default_provider():
    registry = AuthenticationProviderRegistry(
        [
            api_key_provider()
        ]
    )
    service = AuthenticationService(
        registry
    )

    identity = service.authenticate(
        PRIMARY_KEY
    )

    assert service.registry is registry
    assert service.default_provider_name == (
        "api_key"
    )
    assert identity.principal_id == (
        "sre-operator-1"
    )
    assert identity.authentication_method == (
        AuthenticationMethod.API_KEY
    )
    assert PRIMARY_KEY not in repr(
        service.__dict__
    )


def test_authentication_service_supports_explicit_provider_selection():
    api_key = api_key_provider()
    expected_identity = oidc_identity()
    oidc = StubAuthenticationProvider(
        provider_name="oidc",
        identity=expected_identity,
    )
    service = AuthenticationService(
        AuthenticationProviderRegistry(
            [
                api_key,
                oidc,
            ],
            default_provider_name="oidc",
        )
    )

    assert service.authenticate(
        "valid-oidc-token"
    ) is expected_identity
    assert oidc.call_count == 1

    api_key_identity = service.authenticate(
        PRIMARY_KEY,
        provider_name="api_key",
    )

    assert api_key_identity.authentication_method == (
        AuthenticationMethod.API_KEY
    )
    assert oidc.call_count == 1


def test_service_requires_provider_registry():
    with pytest.raises(
        AuthenticationServiceConfigurationError,
        match="requires a provider registry",
    ):
        AuthenticationService(
            object()
        )


@pytest.mark.parametrize(
    "provider_error",
    [
        MissingAuthenticationCredentialsError(),
        InvalidAuthenticationCredentialsError(),
    ],
)
def test_service_preserves_safe_authentication_errors(
    provider_error,
):
    provider = StubAuthenticationProvider(
        provider_name="safe-error",
        error=provider_error,
    )
    service = AuthenticationService(
        AuthenticationProviderRegistry(
            [
                provider
            ]
        )
    )

    with pytest.raises(
        type(
            provider_error
        )
    ) as exc_info:
        service.authenticate(
            None
        )

    assert exc_info.value is provider_error


def test_unexpected_provider_exception_is_sanitized_and_detached():
    secret = "unexpected-provider-secret-credential"
    provider = StubAuthenticationProvider(
        provider_name="exploding",
        error=RuntimeError(
            secret
        ),
    )
    service = AuthenticationService(
        AuthenticationProviderRegistry(
            [
                provider
            ]
        )
    )

    with pytest.raises(
        AuthenticationProviderExecutionError
    ) as exc_info:
        service.authenticate(
            secret
        )

    error = exc_info.value

    assert error.code == (
        "authentication_provider_execution_failed"
    )
    assert str(
        error
    ) == "Authentication failed"
    assert secret not in str(
        error
    )
    assert error.__context__ is None
    assert error.__cause__ is None


@pytest.mark.parametrize(
    "provider_result",
    [
        None,
        "not-an-identity",
        object(),
        OperatorIdentity.anonymous(),
    ],
)
def test_invalid_provider_result_fails_closed(
    provider_result,
):
    provider = StubAuthenticationProvider(
        provider_name="invalid-result",
        identity=provider_result,
    )
    service = AuthenticationService(
        AuthenticationProviderRegistry(
            [
                provider
            ]
        )
    )

    with pytest.raises(
        AuthenticationProviderContractError
    ) as exc_info:
        service.authenticate(
            "credential"
        )

    assert exc_info.value.code == (
        "authentication_provider_contract_invalid"
    )
    assert str(
        exc_info.value
    ) == "Authentication failed"


def test_provider_contract_allows_future_oidc_replacement():
    expected_identity = oidc_identity()
    provider = StubAuthenticationProvider(
        provider_name="oidc",
        identity=expected_identity,
    )
    service = AuthenticationService(
        AuthenticationProviderRegistry(
            [
                provider
            ]
        )
    )

    identity = service.authenticate(
        "signed-oidc-token"
    )

    assert identity is expected_identity
    assert identity.authentication_method == (
        AuthenticationMethod.OIDC_JWT
    )
    assert provider.call_count == 1
