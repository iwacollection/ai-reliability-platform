import pytest

from fastapi import HTTPException
from pydantic import ValidationError

from services.agent_runtime.app.security.api import (
    ApiSecurityAdapter,
    ApiSecurityConfigurationError,
    ApiSecurityContext,
)
from services.agent_runtime.app.security.authentication import (
    ApiKeyAuthenticationProvider,
    ApiKeyRecord,
    BaseAuthenticationProvider,
)
from services.agent_runtime.app.security.models import (
    OperatorIdentity,
    OperatorRole,
    ProtectedOperation,
    RuntimePermission,
)
from services.agent_runtime.app.security.policy import (
    SecurityPolicyEngine,
)
from services.agent_runtime.app.security.service import (
    AuthenticationProviderRegistry,
    AuthenticationService,
)


API_KEY = (
    "api-security-adapter-test-key-00000000001"
)


INVALID_API_KEY = (
    "api-security-adapter-wrong-key-000000001"
)


def build_adapter(
    *roles: OperatorRole,
) -> ApiSecurityAdapter:
    configured_roles = (
        frozenset(
            roles
        )
        or frozenset(
            {
                OperatorRole.VIEWER,
            }
        )
    )
    record = ApiKeyRecord.from_plaintext(
        key_id="api-test-key",
        api_key=API_KEY,
        principal_id="api-operator-1",
        roles=configured_roles,
        display_name="API Operator",
        attributes={
            "team": "platform",
        },
    )
    provider = ApiKeyAuthenticationProvider(
        [
            record,
        ]
    )
    service = AuthenticationService(
        AuthenticationProviderRegistry(
            [
                provider,
            ]
        )
    )

    return ApiSecurityAdapter(
        authentication=service,
        policy=SecurityPolicyEngine(),
    )


def assert_authentication_failed(
    captured: pytest.ExceptionInfo[
        HTTPException
    ],
) -> None:
    error = captured.value

    assert error.status_code == 401
    assert error.detail == (
        "Authentication failed"
    )
    assert error.headers == {
        "WWW-Authenticate": "ApiKey",
    }
    assert API_KEY not in repr(
        error
    )
    assert INVALID_API_KEY not in repr(
        error
    )


def test_adapter_requires_valid_shared_components():
    service = build_adapter().authentication
    policy = SecurityPolicyEngine()

    with pytest.raises(
        ApiSecurityConfigurationError,
        match="AuthenticationService",
    ):
        ApiSecurityAdapter(
            authentication=object(),
            policy=policy,
        )

    with pytest.raises(
        ApiSecurityConfigurationError,
        match="SecurityPolicyEngine",
    ):
        ApiSecurityAdapter(
            authentication=service,
            policy=object(),
        )


def test_valid_api_key_builds_credential_free_context():
    adapter = build_adapter(
        OperatorRole.VIEWER
    )

    context = adapter.require(
        f"ApiKey {API_KEY}",
        ProtectedOperation.READ_INCIDENT,
    )

    assert isinstance(
        context,
        ApiSecurityContext,
    )
    assert context.principal_id == (
        "api-operator-1"
    )
    assert context.operation == (
        ProtectedOperation.READ_INCIDENT
    )
    assert context.authorization.allowed is True
    assert (
        RuntimePermission.INCIDENT_READ
        in context.authorization.granted_permissions
    )
    assert context.identity.attributes == {
        "team": "platform",
        "key_id": "api-test-key",
    }
    assert API_KEY not in context.model_dump_json()
    assert API_KEY not in repr(
        adapter.__dict__
    )


def test_authorization_scheme_is_case_insensitive():
    adapter = build_adapter()

    context = adapter.require(
        f"apikey {API_KEY}",
        ProtectedOperation.READ_APPROVAL,
    )

    assert context.principal_id == (
        "api-operator-1"
    )
    assert adapter.authorization_scheme == (
        "ApiKey"
    )


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        " ",
        API_KEY,
        "ApiKey",
        "Bearer " + API_KEY,
        "ApiKey  " + API_KEY,
        "ApiKey " + API_KEY + " ",
        "ApiKey " + API_KEY + "\tother",
        12345,
    ],
)
def test_missing_and_malformed_headers_return_safe_401(
    authorization,
):
    adapter = build_adapter()

    with pytest.raises(
        HTTPException
    ) as captured:
        adapter.require(
            authorization,
            ProtectedOperation.READ_INCIDENT,
        )

    assert_authentication_failed(
        captured
    )


def test_invalid_credential_returns_safe_401():
    adapter = build_adapter()

    with pytest.raises(
        HTTPException
    ) as captured:
        adapter.require(
            f"ApiKey {INVALID_API_KEY}",
            ProtectedOperation.READ_INCIDENT,
        )

    assert_authentication_failed(
        captured
    )


def test_oversized_credential_returns_safe_401():
    adapter = build_adapter()
    oversized = "x" * 4097

    with pytest.raises(
        HTTPException
    ) as captured:
        adapter.require(
            f"ApiKey {oversized}",
            ProtectedOperation.READ_INCIDENT,
        )

    assert_authentication_failed(
        captured
    )
    assert oversized not in repr(
        captured.value
    )


def test_viewer_is_denied_write_operation_without_detail_leak():
    adapter = build_adapter(
        OperatorRole.VIEWER
    )

    with pytest.raises(
        HTTPException
    ) as captured:
        adapter.require(
            f"ApiKey {API_KEY}",
            ProtectedOperation.RESUME_ACTION,
        )

    error = captured.value

    assert error.status_code == 403
    assert error.detail == (
        "Authorization denied"
    )
    assert API_KEY not in repr(
        error
    )
    assert "action:execute" not in repr(
        error
    )


def test_executor_is_allowed_to_resume_action():
    adapter = build_adapter(
        OperatorRole.EXECUTOR
    )

    context = adapter.require(
        f"ApiKey {API_KEY}",
        ProtectedOperation.RESUME_ACTION,
    )

    assert context.authorization.allowed is True
    assert context.operation == (
        ProtectedOperation.RESUME_ACTION
    )
    assert (
        RuntimePermission.ACTION_EXECUTE
        in context.authorization.granted_permissions
    )


class ExplodingProvider(
    BaseAuthenticationProvider
):
    @property
    def name(
        self,
    ) -> str:
        return "exploding"

    def authenticate(
        self,
        credential: str | None,
    ) -> OperatorIdentity:
        raise RuntimeError(
            f"provider leaked {credential}"
        )


class InvalidContractProvider(
    BaseAuthenticationProvider
):
    @property
    def name(
        self,
    ) -> str:
        return "invalid_contract"

    def authenticate(
        self,
        credential: str | None,
    ):
        return OperatorIdentity.anonymous()


@pytest.mark.parametrize(
    "provider",
    [
        ExplodingProvider(),
        InvalidContractProvider(),
    ],
)
def test_provider_failure_returns_safe_503(
    provider: BaseAuthenticationProvider,
):
    service = AuthenticationService(
        AuthenticationProviderRegistry(
            [
                provider,
            ]
        )
    )
    adapter = ApiSecurityAdapter(
        authentication=service,
        policy=SecurityPolicyEngine(),
    )

    with pytest.raises(
        HTTPException
    ) as captured:
        adapter.require(
            f"ApiKey {API_KEY}",
            ProtectedOperation.READ_INCIDENT,
        )

    error = captured.value

    assert error.status_code == 503
    assert error.detail == (
        "Authentication service unavailable"
    )
    assert error.headers is None
    assert API_KEY not in repr(
        error
    )
    assert error.__suppress_context__ is True
    assert API_KEY not in repr(
        error.__context__
    )


def test_operator_id_always_comes_from_authenticated_identity():
    adapter = build_adapter()
    context = adapter.require(
        f"ApiKey {API_KEY}",
        ProtectedOperation.READ_INCIDENT,
    )

    assert adapter.operator_id(
        context
    ) == "api-operator-1"
    assert adapter.operator_id(
        context,
        "api-operator-1",
    ) == "api-operator-1"

    for claimed_identity in (
        "other-operator",
        "",
        "   ",
        12345,
    ):
        with pytest.raises(
            HTTPException
        ) as captured:
            adapter.operator_id(
                context,
                claimed_identity,
            )

        assert captured.value.status_code == 403
        assert API_KEY not in repr(
            captured.value
        )


def test_audit_context_contains_no_credential():
    adapter = build_adapter(
        OperatorRole.APPROVER
    )
    context = adapter.require(
        f"ApiKey {API_KEY}",
        ProtectedOperation.DECIDE_APPROVAL,
    )

    audit = context.audit_context()

    assert audit["principal_id"] == (
        "api-operator-1"
    )
    assert audit["operation"] == (
        "approval.decide"
    )
    assert audit["authorization_allowed"] is True
    assert audit["policy_version"] == "v1"
    assert API_KEY not in repr(
        audit
    )


def test_context_rejects_denied_authorization_decision():
    adapter = build_adapter(
        OperatorRole.VIEWER
    )
    identity = adapter.authenticate(
        f"ApiKey {API_KEY}"
    )
    denied = adapter.policy.authorize(
        identity,
        ProtectedOperation.RESUME_ACTION,
    )

    with pytest.raises(
        ValidationError,
        match="allowed decision",
    ):
        ApiSecurityContext(
            identity=identity,
            authorization=denied,
        )


def test_unknown_operation_is_configuration_error():
    adapter = build_adapter()
    identity = adapter.authenticate(
        f"ApiKey {API_KEY}"
    )

    with pytest.raises(
        ApiSecurityConfigurationError,
        match="unsupported protected operation",
    ):
        adapter.authorize(
            identity,
            "unknown-operation",
        )
