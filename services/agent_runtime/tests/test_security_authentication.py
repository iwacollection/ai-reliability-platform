from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256

import pytest
from pydantic import ValidationError

from services.agent_runtime.app.security import (
    authentication as authentication_module,
)
from services.agent_runtime.app.security.authentication import (
    ApiKeyAuthenticationProvider,
    ApiKeyRecord,
    AuthenticationConfigurationError,
    AuthenticationError,
    BaseAuthenticationProvider,
    InvalidAuthenticationCredentialsError,
    MissingAuthenticationCredentialsError,
)
from services.agent_runtime.app.security.models import (
    AuthenticationMethod,
    OperatorIdentity,
    OperatorRole,
)


PRIMARY_KEY = (
    "primary-security-api-key-00000000000001"
)


SECONDARY_KEY = (
    "secondary-security-api-key-000000000001"
)


def api_key_record(
    *,
    key_id: str = "primary",
    api_key: str = PRIMARY_KEY,
    principal_id: str = "sre-operator-1",
    roles: set[OperatorRole | str] | None = None,
    active: bool = True,
    expires_at: datetime | None = None,
    attributes: dict | None = None,
) -> ApiKeyRecord:
    return ApiKeyRecord.from_plaintext(
        key_id=key_id,
        api_key=api_key,
        principal_id=principal_id,
        roles=(
            roles
            or {
                OperatorRole.VIEWER,
                OperatorRole.APPROVER,
            }
        ),
        display_name="Platform SRE",
        active=active,
        expires_at=expires_at,
        attributes=(
            attributes
            or {
                "team": "platform",
                "region": "tw",
            }
        ),
    )


def test_plaintext_key_is_hashed_and_never_serialized():
    record = api_key_record()

    expected_digest = sha256(
        PRIMARY_KEY.encode(
            "utf-8"
        )
    ).hexdigest()

    assert record.key_digest == expected_digest
    assert record.key_digest != PRIMARY_KEY
    assert "api_key" not in (
        ApiKeyRecord.model_fields
    )
    assert PRIMARY_KEY not in repr(
        record
    )
    assert PRIMARY_KEY not in (
        record.model_dump_json()
    )
    assert PRIMARY_KEY not in str(
        record.model_dump()
    )


def test_api_key_record_is_immutable():
    record = api_key_record()

    with pytest.raises(
        ValidationError
    ):
        record.principal_id = "mutated"


def test_authenticate_returns_credential_free_identity():
    provider = ApiKeyAuthenticationProvider(
        [
            api_key_record()
        ]
    )

    identity = provider.authenticate(
        PRIMARY_KEY
    )

    assert identity.principal_id == (
        "sre-operator-1"
    )
    assert identity.authenticated is True
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
    assert identity.session_id == (
        "api-key:primary"
    )
    assert identity.attributes == {
        "team": "platform",
        "region": "tw",
        "key_id": "primary",
    }
    assert PRIMARY_KEY not in (
        identity.model_dump_json()
    )
    assert PRIMARY_KEY not in str(
        identity.audit_context()
    )


@pytest.mark.parametrize(
    (
        "credential",
        "expected_error",
        "expected_code",
        "expected_message",
    ),
    [
        (
            None,
            MissingAuthenticationCredentialsError,
            "authentication_credentials_missing",
            "Authentication credentials are required",
        ),
        (
            "incorrect-credential",
            InvalidAuthenticationCredentialsError,
            "authentication_credentials_invalid",
            "Authentication credentials are invalid",
        ),
        (
            "",
            InvalidAuthenticationCredentialsError,
            "authentication_credentials_invalid",
            "Authentication credentials are invalid",
        ),
        (
            "   ",
            InvalidAuthenticationCredentialsError,
            "authentication_credentials_invalid",
            "Authentication credentials are invalid",
        ),
        (
            "x" * 4097,
            InvalidAuthenticationCredentialsError,
            "authentication_credentials_invalid",
            "Authentication credentials are invalid",
        ),
        (
            12345,
            InvalidAuthenticationCredentialsError,
            "authentication_credentials_invalid",
            "Authentication credentials are invalid",
        ),
    ],
)
def test_invalid_credentials_fail_closed_without_disclosure(
    credential,
    expected_error,
    expected_code: str,
    expected_message: str,
):
    provider = ApiKeyAuthenticationProvider(
        [
            api_key_record()
        ]
    )

    with pytest.raises(
        expected_error
    ) as exc_info:
        provider.authenticate(
            credential
        )

    error = exc_info.value

    assert isinstance(
        error,
        AuthenticationError,
    )
    assert error.code == expected_code
    assert str(
        error
    ) == expected_message
    assert PRIMARY_KEY not in str(
        error
    )

    if isinstance(
        credential,
        str,
    ) and credential:
        assert credential not in str(
            error
        )


@pytest.mark.parametrize(
    "record",
    [
        api_key_record(
            active=False
        ),
        api_key_record(
            expires_at=(
                datetime.now(
                    UTC
                )
                - timedelta(
                    seconds=1
                )
            )
        ),
    ],
)
def test_inactive_and_expired_keys_use_generic_invalid_error(
    record: ApiKeyRecord,
):
    provider = ApiKeyAuthenticationProvider(
        [
            record
        ]
    )

    with pytest.raises(
        InvalidAuthenticationCredentialsError
    ) as exc_info:
        provider.authenticate(
            PRIMARY_KEY
        )

    assert exc_info.value.code == (
        "authentication_credentials_invalid"
    )
    assert str(
        exc_info.value
    ) == "Authentication credentials are invalid"
    assert record.key_id not in str(
        exc_info.value
    )
    assert PRIMARY_KEY not in str(
        exc_info.value
    )


@pytest.mark.parametrize(
    "api_key",
    [
        "",
        "   ",
        "too-short",
        "x" * 4097,
        None,
        12345,
    ],
)
def test_plaintext_key_configuration_rejects_unsafe_values(
    api_key,
):
    with pytest.raises(
        AuthenticationConfigurationError
    ):
        ApiKeyRecord.from_plaintext(
            key_id="unsafe",
            api_key=api_key,
            principal_id="operator",
            roles={
                OperatorRole.VIEWER
            },
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "key_id": "invalid-digest",
            "key_digest": "not-a-sha256-digest",
            "principal_id": "operator",
            "roles": [
                "viewer"
            ],
        },
        {
            "key_id": "missing-roles",
            "key_digest": "a" * 64,
            "principal_id": "operator",
            "roles": [],
        },
        {
            "key_id": "naive-expiry",
            "key_digest": "a" * 64,
            "principal_id": "operator",
            "roles": [
                "viewer"
            ],
            "expires_at": (
                "2026-08-08T12:00:00"
            ),
        },
        {
            "key_id": "secret-attribute",
            "key_digest": "a" * 64,
            "principal_id": "operator",
            "roles": [
                "viewer"
            ],
            "attributes": {
                "access_token": "must-not-persist",
            },
        },
        {
            "key_id": "extra-field",
            "key_digest": "a" * 64,
            "principal_id": "operator",
            "roles": [
                "viewer"
            ],
            "api_key": PRIMARY_KEY,
        },
    ],
)
def test_api_key_record_rejects_unsafe_stored_configuration(
    payload: dict,
):
    with pytest.raises(
        ValidationError
    ):
        ApiKeyRecord.model_validate(
            payload
        )


def test_expiry_is_normalized_to_utc_and_checked_at_boundary():
    expiry = datetime(
        2026,
        8,
        8,
        20,
        0,
        tzinfo=timezone(
            timedelta(
                hours=8
            )
        ),
    )

    record = api_key_record(
        expires_at=expiry
    )

    assert record.expires_at == datetime(
        2026,
        8,
        8,
        12,
        0,
        tzinfo=UTC,
    )
    assert record.is_expired(
        now=(
            record.expires_at
            - timedelta(
                microseconds=1
            )
        )
    ) is False
    assert record.is_expired(
        now=record.expires_at
    ) is True

    with pytest.raises(
        ValueError,
        match="clock must be timezone-aware",
    ):
        record.is_expired(
            now=datetime(
                2026,
                8,
                8,
                12,
                0,
            )
        )


def test_provider_rejects_empty_and_duplicate_configuration():
    primary = api_key_record()
    same_id = api_key_record(
        api_key=SECONDARY_KEY
    )
    same_credential = api_key_record(
        key_id="secondary",
        principal_id="sre-operator-2",
    )

    with pytest.raises(
        AuthenticationConfigurationError,
        match="at least one record",
    ):
        ApiKeyAuthenticationProvider(
            []
        )

    with pytest.raises(
        AuthenticationConfigurationError,
        match="Duplicate API key ID",
    ):
        ApiKeyAuthenticationProvider(
            [
                primary,
                same_id,
            ]
        )

    with pytest.raises(
        AuthenticationConfigurationError,
        match="Duplicate API key credential",
    ):
        ApiKeyAuthenticationProvider(
            [
                primary,
                same_credential,
            ]
        )


def test_provider_exposes_only_non_secret_inventory():
    provider = ApiKeyAuthenticationProvider(
        [
            api_key_record(
                key_id="z-key"
            ),
            api_key_record(
                key_id="a-key",
                api_key=SECONDARY_KEY,
                principal_id="sre-operator-2",
            ),
        ]
    )

    assert provider.name == "api_key"
    assert provider.record_count == 2
    assert provider.key_ids == (
        "a-key",
        "z-key",
    )
    assert PRIMARY_KEY not in repr(
        provider.key_ids
    )
    assert SECONDARY_KEY not in repr(
        provider.key_ids
    )


def test_digest_comparison_scans_all_records_without_early_return(
    monkeypatch,
):
    records = [
        api_key_record(
            key_id="first"
        ),
        api_key_record(
            key_id="second",
            api_key=SECONDARY_KEY,
            principal_id="sre-operator-2",
        ),
    ]
    provider = ApiKeyAuthenticationProvider(
        records
    )
    original_compare_digest = (
        authentication_module.compare_digest
    )
    comparisons: list[
        tuple[str, str]
    ] = []

    def observed_compare_digest(
        presented: str,
        configured: str,
    ) -> bool:
        comparisons.append(
            (
                presented,
                configured,
            )
        )
        return original_compare_digest(
            presented,
            configured,
        )

    monkeypatch.setattr(
        authentication_module,
        "compare_digest",
        observed_compare_digest,
    )

    identity = provider.authenticate(
        PRIMARY_KEY
    )

    assert identity.session_id == (
        "api-key:first"
    )
    assert len(
        comparisons
    ) == len(
        records
    )
    assert {
        configured
        for _, configured in comparisons
    } == {
        record.key_digest
        for record in records
    }


def test_string_roles_are_normalized_to_role_enum():
    record = api_key_record(
        roles={
            "viewer",
            "executor",
        }
    )

    assert record.roles == frozenset(
        {
            OperatorRole.VIEWER,
            OperatorRole.EXECUTOR,
        }
    )


def test_authentication_provider_contract_is_replaceable():
    expected_identity = OperatorIdentity(
        principal_id="oidc-operator",
        authentication_method=(
            AuthenticationMethod.OIDC_JWT
        ),
        roles={
            OperatorRole.VIEWER
        },
    )

    class StubAuthenticationProvider(
        BaseAuthenticationProvider
    ):
        @property
        def name(
            self,
        ) -> str:
            return "stub_oidc"

        def authenticate(
            self,
            credential: str | None,
        ) -> OperatorIdentity:
            if credential != "valid-token":
                raise InvalidAuthenticationCredentialsError()

            return expected_identity

    provider = StubAuthenticationProvider()

    assert isinstance(
        provider,
        BaseAuthenticationProvider,
    )
    assert provider.name == "stub_oidc"
    assert provider.authenticate(
        "valid-token"
    ) is expected_identity

    with pytest.raises(
        InvalidAuthenticationCredentialsError
    ):
        provider.authenticate(
            "invalid-token"
        )


def test_base_authentication_provider_cannot_be_instantiated():
    with pytest.raises(
        TypeError
    ):
        BaseAuthenticationProvider()
