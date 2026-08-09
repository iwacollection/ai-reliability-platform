from typing import Any

from fastapi import HTTPException
from pydantic import (
    BaseModel,
    ConfigDict,
    model_validator,
)

from services.agent_runtime.app.security.authentication import (
    AuthenticationError,
    InvalidAuthenticationCredentialsError,
    MissingAuthenticationCredentialsError,
)
from services.agent_runtime.app.security.models import (
    AuthorizationDecision,
    OperatorIdentity,
    ProtectedOperation,
)
from services.agent_runtime.app.security.policy import (
    AuthorizationDeniedError,
    SecurityPolicyEngine,
)
from services.agent_runtime.app.security.service import (
    AuthenticationProviderContractError,
    AuthenticationProviderExecutionError,
    AuthenticationProviderUnavailableError,
    AuthenticationService,
    AuthenticationServiceConfigurationError,
)


_AUTHORIZATION_SCHEME = "ApiKey"
_MAX_AUTHORIZATION_LENGTH = 8192
_MAX_CREDENTIAL_LENGTH = 4096


class ApiSecurityConfigurationError(
    ValueError
):
    """Raised when the API security adapter cannot be assembled safely."""


class ApiSecurityContext(BaseModel):
    """Credential-free authenticated and authorized request context."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    identity: OperatorIdentity

    authorization: AuthorizationDecision

    @model_validator(
        mode="after"
    )
    def validate_authorized_identity(
        self,
    ) -> "ApiSecurityContext":
        if not self.identity.authenticated:
            raise ValueError(
                "API security context requires an authenticated identity"
            )

        if not self.authorization.allowed:
            raise ValueError(
                "API security context requires an allowed decision"
            )

        if (
            self.authorization.principal_id
            != self.identity.principal_id
        ):
            raise ValueError(
                "API security identity and decision do not match"
            )

        return self

    @property
    def principal_id(
        self,
    ) -> str:
        return self.identity.principal_id

    @property
    def operation(
        self,
    ) -> ProtectedOperation:
        return self.authorization.operation

    def audit_context(
        self,
    ) -> dict[str, Any]:
        """Return bounded data safe for workflow and authorization audit."""

        return {
            **self.identity.audit_context(),
            "operation": (
                self.authorization.operation.value
            ),
            "authorization_allowed": True,
            "policy_version": (
                self.authorization.policy_version
            ),
            "authorization_evaluated_at": (
                self.authorization.evaluated_at.isoformat()
            ),
        }


class ApiSecurityAdapter:
    """
    Map HTTP Authorization credentials to trusted RBAC request context.

    The adapter accepts only ``Authorization: ApiKey <credential>``. Provider
    selection is never controlled by the caller. Raw credentials are passed
    directly to AuthenticationService and are not retained or serialized.
    """

    def __init__(
        self,
        *,
        authentication: AuthenticationService,
        policy: SecurityPolicyEngine,
    ) -> None:
        if not isinstance(
            authentication,
            AuthenticationService,
        ):
            raise ApiSecurityConfigurationError(
                "API security requires AuthenticationService"
            )

        if not isinstance(
            policy,
            SecurityPolicyEngine,
        ):
            raise ApiSecurityConfigurationError(
                "API security requires SecurityPolicyEngine"
            )

        self._authentication = authentication
        self._policy = policy

    @property
    def authentication(
        self,
    ) -> AuthenticationService:
        return self._authentication

    @property
    def policy(
        self,
    ) -> SecurityPolicyEngine:
        return self._policy

    @property
    def authorization_scheme(
        self,
    ) -> str:
        return _AUTHORIZATION_SCHEME

    def authenticate(
        self,
        authorization: str | None,
    ) -> OperatorIdentity:
        """Authenticate one bounded ApiKey authorization header."""

        try:
            credential = self._extract_credential(
                authorization
            )
            return self._authentication.authenticate(
                credential
            )

        except (
            MissingAuthenticationCredentialsError,
            InvalidAuthenticationCredentialsError,
        ):
            raise self._authentication_failed() from None

        except (
            AuthenticationProviderUnavailableError,
            AuthenticationProviderExecutionError,
            AuthenticationProviderContractError,
            AuthenticationServiceConfigurationError,
        ):
            raise self._authentication_unavailable() from None

        except AuthenticationError:
            raise self._authentication_unavailable() from None

        except Exception:
            # Request adapters must not serialize unexpected provider or
            # orchestration failures, which may contain credential material.
            raise self._authentication_unavailable() from None

    def authorize(
        self,
        identity: OperatorIdentity,
        operation: ProtectedOperation,
    ) -> AuthorizationDecision:
        """Require one operation and return its credential-free decision."""

        try:
            normalized_operation = ProtectedOperation(
                operation
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise ApiSecurityConfigurationError(
                "API route uses an unsupported protected operation"
            ) from exc

        try:
            return self._policy.require(
                identity,
                normalized_operation,
            )
        except AuthorizationDeniedError:
            raise HTTPException(
                status_code=403,
                detail="Authorization denied",
            ) from None

    def require(
        self,
        authorization: str | None,
        operation: ProtectedOperation,
    ) -> ApiSecurityContext:
        """Authenticate and authorize one request without retaining secrets."""

        identity = self.authenticate(
            authorization
        )
        decision = self.authorize(
            identity,
            operation,
        )

        return ApiSecurityContext(
            identity=identity,
            authorization=decision,
        )

    @staticmethod
    def operator_id(
        context: ApiSecurityContext,
        claimed_operator_id: str | None = None,
    ) -> str:
        """
        Resolve the audit principal from authentication, never from a header.

        A temporary X-Operator-ID compatibility value may be supplied during
        migration, but it must exactly match the authenticated principal.
        """

        if not isinstance(
            context,
            ApiSecurityContext,
        ):
            raise ApiSecurityConfigurationError(
                "Operator resolution requires ApiSecurityContext"
            )

        if claimed_operator_id is None:
            return context.principal_id

        if not isinstance(
            claimed_operator_id,
            str,
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Authenticated operator identity does not match "
                    "X-Operator-ID"
                ),
            )

        normalized_claim = (
            claimed_operator_id.strip()
        )

        if (
            not normalized_claim
            or normalized_claim
            != context.principal_id
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Authenticated operator identity does not match "
                    "X-Operator-ID"
                ),
            )

        return context.principal_id

    @staticmethod
    def _extract_credential(
        authorization: str | None,
    ) -> str:
        if authorization is None:
            raise MissingAuthenticationCredentialsError()

        if (
            not isinstance(
                authorization,
                str,
            )
            or not authorization
            or len(
                authorization
            ) > _MAX_AUTHORIZATION_LENGTH
        ):
            raise InvalidAuthenticationCredentialsError()

        scheme, separator, credential = (
            authorization.partition(
                " "
            )
        )

        if (
            not separator
            or scheme.casefold()
            != _AUTHORIZATION_SCHEME.casefold()
            or not credential
            or credential != credential.strip()
            or len(
                credential
            ) > _MAX_CREDENTIAL_LENGTH
            or any(
                character.isspace()
                for character in credential
            )
        ):
            raise InvalidAuthenticationCredentialsError()

        return credential

    @staticmethod
    def _authentication_failed() -> HTTPException:
        return HTTPException(
            status_code=401,
            detail="Authentication failed",
            headers={
                "WWW-Authenticate": (
                    _AUTHORIZATION_SCHEME
                ),
            },
        )

    @staticmethod
    def _authentication_unavailable() -> HTTPException:
        return HTTPException(
            status_code=503,
            detail="Authentication service unavailable",
        )


__all__ = [
    "ApiSecurityAdapter",
    "ApiSecurityConfigurationError",
    "ApiSecurityContext",
]
