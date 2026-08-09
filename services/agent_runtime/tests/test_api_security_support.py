from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.agent_runtime.app.security.api import (
    ApiSecurityAdapter,
)
from services.agent_runtime.app.security.authentication import (
    ApiKeyAuthenticationProvider,
    InvalidAuthenticationCredentialsError,
)
from services.agent_runtime.app.security.models import (
    AuthenticationMethod,
    OperatorRole,
)
from services.agent_runtime.app.security.policy import (
    SecurityPolicyEngine,
)
from services.agent_runtime.app.security.service import (
    AuthenticationService,
)
from services.agent_runtime.tests.api_security_support import (
    API_TEST_CREDENTIALS,
    ApiTestCredential,
    ApiTestSecurityHarness,
    api_test_credential,
    create_api_test_authentication_service,
    wire_api_test_security,
)


def create_harness(
) -> ApiTestSecurityHarness:
    authentication = (
        create_api_test_authentication_service()
    )
    policy = SecurityPolicyEngine()
    runtime = SimpleNamespace(
        authentication=authentication,
        security_policy=policy,
    )
    adapter = ApiSecurityAdapter(
        authentication=authentication,
        policy=policy,
    )

    return ApiTestSecurityHarness(
        runtime=runtime,
        adapter=adapter,
    )


def test_credentials_cover_every_operator_role_exactly_once():
    assert set(
        API_TEST_CREDENTIALS
    ) == set(
        OperatorRole
    )
    assert len(
        API_TEST_CREDENTIALS
    ) == len(
        OperatorRole
    ) == 7

    for role, credential in (
        API_TEST_CREDENTIALS.items()
    ):
        assert isinstance(
            credential,
            ApiTestCredential,
        )
        assert credential.role is role


def test_credential_inventory_is_immutable_and_unique():
    credentials = tuple(
        API_TEST_CREDENTIALS.values()
    )

    assert len(
        {
            item.key_id
            for item in credentials
        }
    ) == len(
        credentials
    )
    assert len(
        {
            item.api_key
            for item in credentials
        }
    ) == len(
        credentials
    )
    assert len(
        {
            item.principal_id
            for item in credentials
        }
    ) == len(
        credentials
    )

    with pytest.raises(
        TypeError
    ):
        API_TEST_CREDENTIALS[
            OperatorRole.VIEWER
        ] = credentials[0]


def test_plaintext_test_keys_are_bounded_and_hidden_from_repr():
    for credential in (
        API_TEST_CREDENTIALS.values()
    ):
        assert len(
            credential.api_key
        ) >= 32
        assert not any(
            character.isspace()
            for character in credential.api_key
        )
        assert credential.api_key not in repr(
            credential
        )
        assert credential.authorization_value == (
            f"ApiKey {credential.api_key}"
        )


def test_api_test_credential_accepts_enum_and_string_role():
    expected = API_TEST_CREDENTIALS[
        OperatorRole.APPROVER
    ]

    assert api_test_credential(
        OperatorRole.APPROVER
    ) is expected
    assert api_test_credential(
        "approver"
    ) is expected


@pytest.mark.parametrize(
    "role",
    [
        "unknown",
        " APPROVER",
        "",
        None,
        123,
        object(),
    ],
)
def test_api_test_credential_rejects_unsupported_role(
    role,
):
    with pytest.raises(
        ValueError,
        match="Unsupported API test role",
    ):
        api_test_credential(
            role
        )


def test_authentication_service_authenticates_all_test_roles():
    service = (
        create_api_test_authentication_service()
    )
    provider = service.registry.get()

    assert isinstance(
        service,
        AuthenticationService,
    )
    assert isinstance(
        provider,
        ApiKeyAuthenticationProvider,
    )
    assert service.default_provider_name == (
        "api_key"
    )
    assert provider.record_count == 7
    assert provider.key_ids == tuple(
        sorted(
            credential.key_id
            for credential
            in API_TEST_CREDENTIALS.values()
        )
    )

    for role, credential in (
        API_TEST_CREDENTIALS.items()
    ):
        identity = service.authenticate(
            credential.api_key
        )

        assert identity.authenticated is True
        assert identity.principal_id == (
            credential.principal_id
        )
        assert identity.authentication_method == (
            AuthenticationMethod.API_KEY
        )
        assert identity.roles == frozenset(
            {
                role,
            }
        )
        assert identity.attributes[
            "test_identity"
        ] is True
        assert identity.attributes[
            "key_id"
        ] == credential.key_id
        assert credential.api_key not in repr(
            identity
        )


def test_authentication_service_rejects_unknown_test_key():
    service = (
        create_api_test_authentication_service()
    )

    with pytest.raises(
        InvalidAuthenticationCredentialsError
    ):
        service.authenticate(
            "unknown-api-test-key-0000000000000001"
        )


def test_harness_builds_bounded_request_headers():
    harness = create_harness()

    read_headers = harness.headers(
        OperatorRole.VIEWER
    )
    write_headers = harness.headers(
        "approver",
        include_operator_id=True,
        idempotency_key=(
            "approval-decision-0001"
        ),
        request_id=(
            "request-approval-0001"
        ),
    )

    assert read_headers == {
        "Authorization": (
            harness.authorization_header(
                OperatorRole.VIEWER
            )
        ),
    }
    assert write_headers == {
        "Authorization": (
            harness.authorization_header(
                OperatorRole.APPROVER
            )
        ),
        "X-Operator-ID": (
            harness.principal_id(
                OperatorRole.APPROVER
            )
        ),
        "Idempotency-Key": (
            "approval-decision-0001"
        ),
        "X-Request-ID": (
            "request-approval-0001"
        ),
    }

    second_headers = harness.headers(
        OperatorRole.VIEWER
    )
    assert second_headers == read_headers
    assert second_headers is not read_headers


@pytest.mark.parametrize(
    "invalid_value",
    [
        "",
        "   ",
        " leading",
        "trailing ",
        "x" * 129,
        123,
        object(),
    ],
)
def test_harness_rejects_invalid_idempotency_key(
    invalid_value,
):
    harness = create_harness()

    with pytest.raises(
        ValueError,
        match="idempotency key is invalid",
    ):
        harness.headers(
            OperatorRole.EXECUTOR,
            idempotency_key=invalid_value,
        )


@pytest.mark.parametrize(
    "invalid_value",
    [
        "",
        "   ",
        " leading",
        "trailing ",
        "x" * 129,
        123,
        object(),
    ],
)
def test_harness_rejects_invalid_request_id(
    invalid_value,
):
    harness = create_harness()

    with pytest.raises(
        ValueError,
        match="request ID is invalid",
    ):
        harness.headers(
            OperatorRole.VIEWER,
            request_id=invalid_value,
        )


def test_safe_summary_contains_inventory_without_plaintext_keys():
    harness = create_harness()
    summary = harness.safe_summary()
    serialized = repr(
        summary
    )

    assert summary[
        "roles"
    ] == sorted(
        role.value
        for role in OperatorRole
    )
    assert summary[
        "principals"
    ] == {
        role.value: credential.principal_id
        for role, credential
        in API_TEST_CREDENTIALS.items()
    }
    assert summary[
        "authentication_provider"
    ] == "api_key"
    assert summary[
        "policy_version"
    ] == harness.adapter.policy.policy_version

    for credential in (
        API_TEST_CREDENTIALS.values()
    ):
        assert credential.api_key not in serialized


def test_wire_support_shares_runtime_and_module_security_components(
    monkeypatch: pytest.MonkeyPatch,
):
    policy = SecurityPolicyEngine()
    runtime = SimpleNamespace(
        authentication=object(),
        security_policy=policy,
    )
    api_module = SimpleNamespace(
        runtime=object(),
        api_security=object(),
    )

    harness = wire_api_test_security(
        monkeypatch,
        api_module,
        runtime,
    )

    assert harness.runtime is runtime
    assert api_module.runtime is runtime
    assert api_module.api_security is (
        harness.adapter
    )
    assert runtime.authentication is (
        harness.adapter.authentication
    )
    assert runtime.security_policy is (
        harness.adapter.policy
    )
    assert runtime.security_policy is policy
    assert isinstance(
        runtime.authentication,
        AuthenticationService,
    )


def test_wire_support_preserves_explicit_authentication_and_policy(
    monkeypatch: pytest.MonkeyPatch,
):
    authentication = (
        create_api_test_authentication_service()
    )
    policy = SecurityPolicyEngine(
        policy_version=(
            "api-test-policy-v2"
        )
    )
    runtime = SimpleNamespace(
        authentication=object(),
        security_policy=(
            SecurityPolicyEngine()
        ),
    )
    api_module = SimpleNamespace(
        runtime=object(),
        api_security=object(),
    )

    harness = wire_api_test_security(
        monkeypatch,
        api_module,
        runtime,
        authentication=authentication,
        policy=policy,
    )

    assert runtime.authentication is authentication
    assert runtime.security_policy is policy
    assert harness.adapter.authentication is (
        authentication
    )
    assert harness.adapter.policy is policy


def test_test_credentials_and_harness_are_frozen():
    credential = api_test_credential(
        OperatorRole.ADMIN
    )
    harness = create_harness()

    with pytest.raises(
        FrozenInstanceError
    ):
        credential.principal_id = (
            "changed-principal"
        )

    with pytest.raises(
        FrozenInstanceError
    ):
        harness.runtime = object()


def test_support_does_not_create_database_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.chdir(
        tmp_path
    )
    runtime = SimpleNamespace(
        authentication=object(),
        security_policy=(
            SecurityPolicyEngine()
        ),
    )
    api_module = SimpleNamespace(
        runtime=object(),
        api_security=object(),
    )

    harness = wire_api_test_security(
        monkeypatch,
        api_module,
        runtime,
    )

    for role in OperatorRole:
        harness.adapter.authentication.authenticate(
            harness.credential(
                role
            ).api_key
        )

    assert list(
        tmp_path.rglob(
            "*.db"
        )
    ) == []
