from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest
from types import MappingProxyType
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from services.agent_runtime.app.security.models import (
    AuthenticationMethod,
    OperatorIdentity,
    OperatorRole,
)


class AuthenticationConfigurationError(
    ValueError
):
    """Raised when an authentication provider cannot start safely."""


class AuthenticationError(
    PermissionError
):
    """Credential-safe authentication failure suitable for API mapping."""

    code = "authentication_failed"
    safe_message = "Authentication failed"

    def __init__(
        self,
    ) -> None:
        super().__init__(
            self.safe_message
        )


class MissingAuthenticationCredentialsError(
    AuthenticationError
):
    code = "authentication_credentials_missing"
    safe_message = (
        "Authentication credentials are required"
    )


class InvalidAuthenticationCredentialsError(
    AuthenticationError
):
    code = "authentication_credentials_invalid"
    safe_message = "Authentication credentials are invalid"


def _validate_plaintext_api_key(
    value: Any,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise AuthenticationConfigurationError(
            "API key must be text"
        )

    if not value.strip():
        raise AuthenticationConfigurationError(
            "API key cannot be empty"
        )

    if len(
        value
    ) < 32:
        raise AuthenticationConfigurationError(
            "API key must contain at least 32 characters"
        )

    if len(
        value
    ) > 4096:
        raise AuthenticationConfigurationError(
            "API key exceeds the supported length"
        )

    return value


def _api_key_digest(
    api_key: str,
) -> str:
    return sha256(
        api_key.encode(
            "utf-8"
        )
    ).hexdigest()


class ApiKeyRecord(BaseModel):
    """
    Immutable API key identity record containing only a SHA-256 digest.

    Plaintext credentials are accepted only by from_plaintext(), hashed
    immediately, and never become a model field or part of serialization.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    key_id: str = Field(
        min_length=1,
        max_length=128,
    )

    key_digest: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )

    principal_id: str = Field(
        min_length=1,
        max_length=128,
    )

    roles: frozenset[OperatorRole] = Field(
        min_length=1,
    )

    display_name: str | None = Field(
        default=None,
        max_length=256,
    )

    active: bool = True

    expires_at: datetime | None = None

    attributes: dict[str, Any] = Field(
        default_factory=dict,
    )

    @field_validator(
        "key_id",
        "principal_id",
        mode="before",
    )
    @classmethod
    def normalize_required_text(
        cls,
        value: Any,
    ) -> str:
        if not isinstance(
            value,
            str,
        ):
            raise ValueError(
                "API key identity fields must be text"
            )

        normalized = value.strip()

        if not normalized:
            raise ValueError(
                "API key identity fields cannot be empty"
            )

        return normalized

    @field_validator(
        "key_digest",
        mode="before",
    )
    @classmethod
    def normalize_digest(
        cls,
        value: Any,
    ) -> str:
        if not isinstance(
            value,
            str,
        ):
            raise ValueError(
                "API key digest must be text"
            )

        return value.strip().lower()

    @field_validator(
        "display_name",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(
        cls,
        value: Any,
    ) -> str | None:
        if value is None:
            return None

        if not isinstance(
            value,
            str,
        ):
            raise ValueError(
                "API key display name must be text"
            )

        normalized = value.strip()

        return normalized or None

    @field_validator(
        "expires_at",
        mode="after",
    )
    @classmethod
    def require_timezone_aware_expiry(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None

        if (
            value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(
                "API key expiry must be timezone-aware"
            )

        return value.astimezone(
            UTC
        )

    @model_validator(
        mode="after"
    )
    def validate_identity_metadata(
        self,
    ) -> "ApiKeyRecord":
        OperatorIdentity(
            principal_id=self.principal_id,
            authentication_method=(
                AuthenticationMethod.API_KEY
            ),
            roles=self.roles,
            display_name=self.display_name,
            session_id=(
                f"api-key:{self.key_id}"
            ),
            attributes={
                **self.attributes,
                "key_id": self.key_id,
            },
        )

        return self

    @classmethod
    def from_plaintext(
        cls,
        *,
        key_id: str,
        api_key: str,
        principal_id: str,
        roles: Iterable[
            OperatorRole | str
        ],
        display_name: str | None = None,
        active: bool = True,
        expires_at: datetime | None = None,
        attributes: dict[
            str,
            Any,
        ] | None = None,
    ) -> "ApiKeyRecord":
        validated_api_key = (
            _validate_plaintext_api_key(
                api_key
            )
        )

        return cls(
            key_id=key_id,
            key_digest=(
                _api_key_digest(
                    validated_api_key
                )
            ),
            principal_id=principal_id,
            roles=frozenset(
                OperatorRole(
                    role
                )
                for role in roles
            ),
            display_name=display_name,
            active=active,
            expires_at=expires_at,
            attributes=dict(
                attributes
                or {}
            ),
        )

    def is_expired(
        self,
        *,
        now: datetime | None = None,
    ) -> bool:
        if self.expires_at is None:
            return False

        current_time = (
            now
            or datetime.now(
                UTC
            )
        )

        if (
            current_time.tzinfo is None
            or current_time.utcoffset() is None
        ):
            raise ValueError(
                "Authentication clock must be timezone-aware"
            )

        return current_time.astimezone(
            UTC
        ) >= self.expires_at

    def to_identity(
        self,
        *,
        authenticated_at: datetime | None = None,
    ) -> OperatorIdentity:
        return OperatorIdentity(
            principal_id=self.principal_id,
            authentication_method=(
                AuthenticationMethod.API_KEY
            ),
            roles=self.roles,
            display_name=self.display_name,
            session_id=(
                f"api-key:{self.key_id}"
            ),
            authenticated_at=(
                authenticated_at
                or datetime.now(
                    UTC
                )
            ),
            attributes={
                **self.attributes,
                "key_id": self.key_id,
            },
        )


class BaseAuthenticationProvider(ABC):
    """Request-framework-neutral authentication provider contract."""

    @property
    @abstractmethod
    def name(
        self,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def authenticate(
        self,
        credential: str | None,
    ) -> OperatorIdentity:
        raise NotImplementedError


class ApiKeyAuthenticationProvider(
    BaseAuthenticationProvider
):
    """Authenticate API keys using full-loop constant-time digest checks."""

    def __init__(
        self,
        records: Iterable[
            ApiKeyRecord
        ],
    ) -> None:
        normalized_records = tuple(
            ApiKeyRecord.model_validate(
                record
            )
            for record in records
        )

        if not normalized_records:
            raise AuthenticationConfigurationError(
                "API key provider requires at least one record"
            )

        records_by_id: dict[
            str,
            ApiKeyRecord,
        ] = {}
        record_ids_by_digest: dict[
            str,
            str,
        ] = {}

        for record in normalized_records:
            if record.key_id in records_by_id:
                raise AuthenticationConfigurationError(
                    "Duplicate API key ID"
                )

            if (
                record.key_digest
                in record_ids_by_digest
            ):
                raise AuthenticationConfigurationError(
                    "Duplicate API key credential"
                )

            records_by_id[
                record.key_id
            ] = record
            record_ids_by_digest[
                record.key_digest
            ] = record.key_id

        self._records = normalized_records
        self._records_by_id = MappingProxyType(
            records_by_id
        )

    @property
    def name(
        self,
    ) -> str:
        return "api_key"

    @property
    def record_count(
        self,
    ) -> int:
        return len(
            self._records
        )

    @property
    def key_ids(
        self,
    ) -> tuple[str, ...]:
        """Expose non-secret key IDs for health and configuration checks."""

        return tuple(
            sorted(
                self._records_by_id
            )
        )

    def authenticate(
        self,
        credential: str | None,
    ) -> OperatorIdentity:
        if credential is None:
            raise MissingAuthenticationCredentialsError()

        if not isinstance(
            credential,
            str,
        ):
            raise InvalidAuthenticationCredentialsError()

        if (
            not credential.strip()
            or len(
                credential
            ) > 4096
        ):
            raise InvalidAuthenticationCredentialsError()

        presented_digest = (
            _api_key_digest(
                credential
            )
        )
        matched_record: (
            ApiKeyRecord | None
        ) = None

        # Evaluate every configured digest so the matching record position does
        # not create an early-return timing signal.
        for record in self._records:
            matched = compare_digest(
                presented_digest,
                record.key_digest,
            )

            if matched:
                matched_record = record

        if matched_record is None:
            raise InvalidAuthenticationCredentialsError()

        authenticated_at = datetime.now(
            UTC
        )

        if (
            not matched_record.active
            or matched_record.is_expired(
                now=authenticated_at
            )
        ):
            # Deliberately use the same public error as an unknown credential.
            raise InvalidAuthenticationCredentialsError()

        return matched_record.to_identity(
            authenticated_at=(
                authenticated_at
            )
        )


__all__ = [
    "ApiKeyAuthenticationProvider",
    "ApiKeyRecord",
    "AuthenticationConfigurationError",
    "AuthenticationError",
    "BaseAuthenticationProvider",
    "InvalidAuthenticationCredentialsError",
    "MissingAuthenticationCredentialsError",
]
