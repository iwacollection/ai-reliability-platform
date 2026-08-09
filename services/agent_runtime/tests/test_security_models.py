import pytest
from pydantic import ValidationError

from services.agent_runtime.app.security.models import (
    AuthenticationMethod,
    AuthorizationDecision,
    OperatorIdentity,
    OperatorRole,
    ProtectedOperation,
    RuntimePermission,
)


def authenticated_identity() -> OperatorIdentity:
    return OperatorIdentity(
        principal_id="  sre-operator-1  ",
        authentication_method=(
            AuthenticationMethod.TRUSTED_PROXY
        ),
        roles={
            OperatorRole.VIEWER,
            OperatorRole.APPROVER,
        },
        display_name="  Platform SRE  ",
        session_id="  session-001  ",
        attributes={
            "team": "platform",
            "region": "tw",
        },
    )


def test_authenticated_identity_normalizes_and_freezes_roles():
    identity = authenticated_identity()

    assert identity.principal_id == (
        "sre-operator-1"
    )
    assert identity.display_name == "Platform SRE"
    assert identity.session_id == "session-001"
    assert identity.authenticated is True
    assert identity.roles == frozenset(
        {
            OperatorRole.VIEWER,
            OperatorRole.APPROVER,
        }
    )
    assert identity.has_role(
        OperatorRole.APPROVER
    ) is True
    assert identity.has_role(
        OperatorRole.ADMIN
    ) is False


def test_anonymous_factory_is_only_valid_unauthenticated_shape():
    identity = OperatorIdentity.anonymous()

    assert identity.principal_id == "anonymous"
    assert identity.authenticated is False
    assert identity.authentication_method == (
        AuthenticationMethod.ANONYMOUS
    )
    assert identity.roles == frozenset()


@pytest.mark.parametrize(
    "payload",
    [
        {
            "principal_id": "authenticated-anonymous",
            "authenticated": True,
            "authentication_method": "anonymous",
            "roles": ["viewer"],
        },
        {
            "principal_id": "missing-role",
            "authenticated": True,
            "authentication_method": "api_key",
            "roles": [],
        },
        {
            "principal_id": "anonymous",
            "authenticated": False,
            "authentication_method": "api_key",
            "roles": [],
        },
        {
            "principal_id": "not-anonymous",
            "authenticated": False,
            "authentication_method": "anonymous",
            "roles": [],
        },
        {
            "principal_id": "anonymous",
            "authenticated": False,
            "authentication_method": "anonymous",
            "roles": ["viewer"],
        },
    ],
)
def test_invalid_authentication_state_fails_closed(
    payload,
):
    with pytest.raises(
        ValidationError
    ):
        OperatorIdentity.model_validate(
            payload
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
        "private_key_pem",
        "credential_id",
    ],
)
def test_identity_attributes_reject_credentials_and_secrets(
    attribute_name: str,
):
    with pytest.raises(
        ValidationError,
        match=(
            "must not contain credentials or secrets"
        ),
    ):
        OperatorIdentity(
            principal_id="unsafe-identity",
            authentication_method=(
                AuthenticationMethod.API_KEY
            ),
            roles={
                OperatorRole.ADMIN
            },
            attributes={
                attribute_name: "must-not-be-stored",
            },
        )


def test_audit_context_excludes_arbitrary_attributes():
    identity = authenticated_identity()
    audit = identity.audit_context()

    assert audit == {
        "principal_id": "sre-operator-1",
        "authenticated": True,
        "authentication_method": "trusted_proxy",
        "roles": [
            "approver",
            "viewer",
        ],
        "display_name": "Platform SRE",
        "session_id": "session-001",
        "authenticated_at": (
            identity.authenticated_at.isoformat()
        ),
    }
    assert "attributes" not in audit
    assert "team" not in audit


def test_identity_is_immutable():
    identity = authenticated_identity()

    with pytest.raises(
        ValidationError
    ):
        identity.principal_id = "mutated"


def test_authorization_decision_allows_complete_permission_set():
    identity = authenticated_identity()
    required = frozenset(
        {
            RuntimePermission.APPROVAL_DECIDE,
        }
    )
    granted = frozenset(
        {
            RuntimePermission.APPROVAL_READ,
            RuntimePermission.APPROVAL_DECIDE,
        }
    )

    decision = AuthorizationDecision.evaluate(
        identity=identity,
        operation=(
            ProtectedOperation.DECIDE_APPROVAL
        ),
        required_permissions=required,
        granted_permissions=granted,
    )

    assert decision.principal_id == (
        identity.principal_id
    )
    assert decision.authenticated is True
    assert decision.allowed is True
    assert decision.missing_permissions == (
        frozenset()
    )
    assert decision.reason == (
        "Principal has all required permissions"
    )


def test_authorization_decision_denies_missing_permission():
    identity = authenticated_identity()
    required = frozenset(
        {
            RuntimePermission.ACTION_EXECUTE,
            RuntimePermission.ACTION_RECONCILE,
        }
    )

    decision = AuthorizationDecision.evaluate(
        identity=identity,
        operation=(
            ProtectedOperation.RECONCILE_ACTION
        ),
        required_permissions=required,
        granted_permissions=frozenset(
            {
                RuntimePermission.ACTION_EXECUTE,
            }
        ),
    )

    assert decision.allowed is False
    assert decision.missing_permissions == (
        frozenset(
            {
                RuntimePermission.ACTION_RECONCILE,
            }
        )
    )
    assert "action:reconcile" in (
        decision.reason
    )


def test_anonymous_identity_is_denied_even_if_permissions_are_supplied():
    required = frozenset(
        {
            RuntimePermission.INCIDENT_READ,
        }
    )

    decision = AuthorizationDecision.evaluate(
        identity=OperatorIdentity.anonymous(),
        operation=(
            ProtectedOperation.READ_INCIDENT
        ),
        required_permissions=required,
        granted_permissions=required,
    )

    assert decision.authenticated is False
    assert decision.allowed is False
    assert decision.missing_permissions == (
        frozenset()
    )
    assert decision.reason == (
        "Authentication is required for the "
        "protected operation"
    )


@pytest.mark.parametrize(
    (
        "authenticated",
        "allowed",
        "missing_permissions",
    ),
    [
        (
            True,
            True,
            frozenset(
                {
                    RuntimePermission.ACTION_EXECUTE,
                }
            ),
        ),
        (
            True,
            False,
            frozenset(),
        ),
        (
            False,
            True,
            frozenset(),
        ),
    ],
)
def test_inconsistent_authorization_decision_is_rejected(
    authenticated: bool,
    allowed: bool,
    missing_permissions: frozenset[
        RuntimePermission
    ],
):
    with pytest.raises(
        ValidationError
    ):
        AuthorizationDecision(
            principal_id="decision-test",
            operation=(
                ProtectedOperation.RESUME_ACTION
            ),
            authenticated=authenticated,
            allowed=allowed,
            required_permissions=frozenset(
                {
                    RuntimePermission.ACTION_EXECUTE,
                }
            ),
            granted_permissions=frozenset(
                {
                    RuntimePermission.ACTION_EXECUTE,
                }
            ),
            missing_permissions=(
                missing_permissions
            ),
            reason="Injected inconsistent decision",
        )


def test_authorization_requires_at_least_one_permission():
    with pytest.raises(
        ValidationError
    ):
        AuthorizationDecision(
            principal_id="empty-policy",
            operation=(
                ProtectedOperation.READ_INCIDENT
            ),
            authenticated=True,
            allowed=True,
            required_permissions=frozenset(),
            granted_permissions=frozenset(),
            missing_permissions=frozenset(),
            reason="Empty permission policy",
        )


def test_security_models_round_trip_without_losing_enum_types():
    identity = authenticated_identity()
    restored_identity = (
        OperatorIdentity.model_validate_json(
            identity.model_dump_json()
        )
    )
    decision = AuthorizationDecision.evaluate(
        identity=identity,
        operation=(
            ProtectedOperation.READ_APPROVAL_WORKFLOW
        ),
        required_permissions=frozenset(
            {
                RuntimePermission.WORKFLOW_READ,
            }
        ),
        granted_permissions=frozenset(
            {
                RuntimePermission.WORKFLOW_READ,
            }
        ),
    )
    restored_decision = (
        AuthorizationDecision.model_validate_json(
            decision.model_dump_json()
        )
    )

    assert restored_identity == identity
    assert restored_decision == decision
    assert all(
        isinstance(
            role,
            OperatorRole,
        )
        for role in restored_identity.roles
    )
    assert all(
        isinstance(
            permission,
            RuntimePermission,
        )
        for permission
        in restored_decision.required_permissions
    )
