from collections.abc import Mapping
from dataclasses import (
    dataclass,
    field,
)
from types import MappingProxyType
from typing import Any

from services.agent_runtime.app.security.api import (
    ApiSecurityAdapter,
)
from services.agent_runtime.app.security.authentication import (
    ApiKeyAuthenticationProvider,
    ApiKeyRecord,
)
from services.agent_runtime.app.security.models import (
    OperatorRole,
)
from services.agent_runtime.app.security.policy import (
    SecurityPolicyEngine,
)
from services.agent_runtime.app.security.service import (
    AuthenticationProviderRegistry,
    AuthenticationService,
)


@dataclass(
    frozen=True
)
class ApiTestCredential:
    """One deterministic credential used only by API security tests."""

    key_id: str

    api_key: str = field(
        repr=False
    )

    principal_id: str

    role: OperatorRole

    @property
    def authorization_value(
        self,
    ) -> str:
        return f"ApiKey {self.api_key}"


def _build_test_credentials() -> Mapping[
    OperatorRole,
    ApiTestCredential,
]:
    credentials = {
        role: ApiTestCredential(
            key_id=(
                f"api-test-{role.value}"
            ),
            api_key=(
                "ai-reliability-api-test-"
                f"{role.value}-key-0000000001"
            ),
            principal_id=(
                f"test-{role.value}-operator"
            ),
            role=role,
        )
        for role in OperatorRole
    }

    return MappingProxyType(
        credentials
    )


API_TEST_CREDENTIALS = (
    _build_test_credentials()
)


def api_test_credential(
    role: OperatorRole | str,
) -> ApiTestCredential:
    try:
        normalized_role = OperatorRole(
            role
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError(
            "Unsupported API test role"
        ) from exc

    return API_TEST_CREDENTIALS[
        normalized_role
    ]


def create_api_test_authentication_service(
) -> AuthenticationService:
    """Build a seven-role in-memory API key service without environment I/O."""

    records = [
        ApiKeyRecord.from_plaintext(
            key_id=credential.key_id,
            api_key=credential.api_key,
            principal_id=(
                credential.principal_id
            ),
            roles={
                credential.role,
            },
            display_name=(
                "API Test "
                f"{credential.role.value.title()}"
            ),
            attributes={
                "test_identity": True,
            },
        )
        for credential
        in API_TEST_CREDENTIALS.values()
    ]
    provider = ApiKeyAuthenticationProvider(
        records
    )
    registry = AuthenticationProviderRegistry(
        [
            provider,
        ]
    )

    return AuthenticationService(
        registry
    )


@dataclass(
    frozen=True
)
class ApiTestSecurityHarness:
    """Shared authenticated Runtime and request-header support for API tests."""

    runtime: Any = field(
        repr=False
    )

    adapter: ApiSecurityAdapter = field(
        repr=False
    )

    credentials: Mapping[
        OperatorRole,
        ApiTestCredential,
    ] = field(
        default_factory=lambda: (
            API_TEST_CREDENTIALS
        ),
        repr=False,
    )

    def credential(
        self,
        role: OperatorRole | str,
    ) -> ApiTestCredential:
        normalized_role = OperatorRole(
            role
        )
        return self.credentials[
            normalized_role
        ]

    def authorization_header(
        self,
        role: OperatorRole | str,
    ) -> str:
        return self.credential(
            role
        ).authorization_value

    def principal_id(
        self,
        role: OperatorRole | str,
    ) -> str:
        return self.credential(
            role
        ).principal_id

    def headers(
        self,
        role: OperatorRole | str,
        *,
        include_operator_id: bool = False,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, str]:
        credential = self.credential(
            role
        )
        headers = {
            "Authorization": (
                credential.authorization_value
            ),
        }

        if include_operator_id:
            headers[
                "X-Operator-ID"
            ] = credential.principal_id

        if idempotency_key is not None:
            normalized_key = (
                idempotency_key.strip()
                if isinstance(
                    idempotency_key,
                    str,
                )
                else ""
            )

            if (
                not normalized_key
                or normalized_key
                != idempotency_key
                or len(
                    normalized_key
                ) > 128
            ):
                raise ValueError(
                    "API test idempotency key is invalid"
                )

            headers[
                "Idempotency-Key"
            ] = normalized_key

        if request_id is not None:
            normalized_request_id = (
                request_id.strip()
                if isinstance(
                    request_id,
                    str,
                )
                else ""
            )

            if (
                not normalized_request_id
                or normalized_request_id
                != request_id
                or len(
                    normalized_request_id
                ) > 128
            ):
                raise ValueError(
                    "API test request ID is invalid"
                )

            headers[
                "X-Request-ID"
            ] = normalized_request_id

        return headers

    def safe_summary(
        self,
    ) -> dict[str, object]:
        return {
            "roles": [
                role.value
                for role in sorted(
                    self.credentials,
                    key=lambda item: (
                        item.value
                    ),
                )
            ],
            "principals": {
                role.value: (
                    credential.principal_id
                )
                for role, credential
                in self.credentials.items()
            },
            "authentication_provider": (
                self.adapter
                .authentication
                .default_provider_name
            ),
            "policy_version": (
                self.adapter
                .policy
                .policy_version
            ),
        }


def wire_api_test_security(
    monkeypatch,
    api_module,
    runtime,
    *,
    authentication: AuthenticationService | None = None,
    policy: SecurityPolicyEngine | None = None,
) -> ApiTestSecurityHarness:
    """
    Replace one test Runtime's reject-all service with bounded test identities.

    The caller remains responsible for changing into pytest's temporary
    directory before constructing Runtime so every SQLite store stays isolated.
    """

    resolved_authentication = (
        authentication
        or create_api_test_authentication_service()
    )
    resolved_policy = (
        policy
        or runtime.security_policy
    )
    adapter = ApiSecurityAdapter(
        authentication=(
            resolved_authentication
        ),
        policy=resolved_policy,
    )

    monkeypatch.setattr(
        runtime,
        "authentication",
        resolved_authentication,
    )
    monkeypatch.setattr(
        runtime,
        "security_policy",
        resolved_policy,
    )
    monkeypatch.setattr(
        api_module,
        "runtime",
        runtime,
    )
    monkeypatch.setattr(
        api_module,
        "api_security",
        adapter,
    )

    return ApiTestSecurityHarness(
        runtime=runtime,
        adapter=adapter,
    )


__all__ = [
    "API_TEST_CREDENTIALS",
    "ApiTestCredential",
    "ApiTestSecurityHarness",
    "api_test_credential",
    "create_api_test_authentication_service",
    "wire_api_test_security",
]
