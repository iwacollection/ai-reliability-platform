from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import TypeVar

from services.agent_runtime.app.security.models import (
    AuthorizationDecision,
    OperatorIdentity,
    OperatorRole,
    ProtectedOperation,
    RuntimePermission,
)


class SecurityPolicyConfigurationError(
    ValueError
):
    """Raised when an RBAC policy is incomplete or invalid."""


class AuthorizationDeniedError(
    PermissionError
):
    """Fail-closed authorization error carrying its immutable decision."""

    def __init__(
        self,
        decision: AuthorizationDecision,
    ) -> None:
        self.decision = decision

        super().__init__(
            "Authorization denied for principal "
            f"{decision.principal_id!r} on operation "
            f"{decision.operation.value!r}"
        )


_EnumType = TypeVar(
    "_EnumType",
    OperatorRole,
    ProtectedOperation,
)


def _normalize_enum_key(
    value,
    enum_type: type[_EnumType],
    label: str,
) -> _EnumType:
    try:
        return enum_type(
            value
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise SecurityPolicyConfigurationError(
            f"Unsupported {label}: {value!r}"
        ) from exc


def _normalize_permissions(
    values: Iterable[
        RuntimePermission | str
    ],
    *,
    label: str,
    allow_empty: bool,
) -> frozenset[RuntimePermission]:
    try:
        normalized = frozenset(
            RuntimePermission(
                value
            )
            for value in values
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise SecurityPolicyConfigurationError(
            f"{label} contains an unsupported permission"
        ) from exc

    if (
        not allow_empty
        and not normalized
    ):
        raise SecurityPolicyConfigurationError(
            f"{label} must require at least one permission"
        )

    return normalized


_READ_PERMISSIONS = frozenset(
    {
        RuntimePermission.INCIDENT_READ,
        RuntimePermission.WORKFLOW_READ,
        RuntimePermission.APPROVAL_READ,
        RuntimePermission.VERIFICATION_READ,
        RuntimePermission.REMEDIATION_PREPARATION_READ,
        RuntimePermission.INVESTIGATION_READ,
    }
)


DEFAULT_ROLE_PERMISSIONS: Mapping[
    OperatorRole,
    frozenset[RuntimePermission],
] = MappingProxyType(
    {
        OperatorRole.VIEWER: (
            _READ_PERMISSIONS
        ),
        OperatorRole.ANALYST: (
            _READ_PERMISSIONS
            | {
                RuntimePermission.RUNTIME_EXECUTE,
                RuntimePermission.REMEDIATION_PREPARE,
                RuntimePermission.INVESTIGATION_ADVANCE,
            }
        ),
        OperatorRole.APPROVER: (
            _READ_PERMISSIONS
            | {
                RuntimePermission.APPROVAL_DECIDE,
            }
        ),
        OperatorRole.EXECUTOR: (
            _READ_PERMISSIONS
            | {
                RuntimePermission.ACTION_EXECUTE,
            }
        ),
        OperatorRole.RECONCILER: (
            _READ_PERMISSIONS
            | {
                RuntimePermission.ACTION_RECONCILE,
            }
        ),
        OperatorRole.ADMIN: frozenset(
            RuntimePermission
        ),
        OperatorRole.SERVICE: frozenset(
            {
                RuntimePermission.RUNTIME_EXECUTE,
                RuntimePermission.REMEDIATION_PREPARE,
                RuntimePermission.INVESTIGATION_ADVANCE,
            }
        ),
    }
)


DEFAULT_OPERATION_PERMISSIONS: Mapping[
    ProtectedOperation,
    frozenset[RuntimePermission],
] = MappingProxyType(
    {
        ProtectedOperation.EXECUTE_RUNTIME_ANALYSIS: (
            frozenset(
                {
                    RuntimePermission.RUNTIME_EXECUTE,
                }
            )
        ),
        ProtectedOperation.PREPARE_REMEDIATION: (
            frozenset(
                {
                    RuntimePermission.REMEDIATION_PREPARE,
                }
            )
        ),
        ProtectedOperation.READ_REMEDIATION_PREPARATION: (
            frozenset(
                {
                    RuntimePermission.REMEDIATION_PREPARATION_READ,
                }
            )
        ),
        ProtectedOperation.READ_INCIDENT: (
            frozenset(
                {
                    RuntimePermission.INCIDENT_READ,
                }
            )
        ),
        ProtectedOperation.READ_INCIDENT_WORKFLOWS: (
            frozenset(
                {
                    RuntimePermission.INCIDENT_READ,
                    RuntimePermission.WORKFLOW_READ,
                }
            )
        ),
        ProtectedOperation.READ_APPROVAL: (
            frozenset(
                {
                    RuntimePermission.APPROVAL_READ,
                }
            )
        ),
        ProtectedOperation.READ_APPROVAL_WORKFLOW: (
            frozenset(
                {
                    RuntimePermission.APPROVAL_READ,
                    RuntimePermission.WORKFLOW_READ,
                }
            )
        ),
        ProtectedOperation.DECIDE_APPROVAL: (
            frozenset(
                {
                    RuntimePermission.APPROVAL_DECIDE,
                }
            )
        ),
        ProtectedOperation.RESUME_ACTION: (
            frozenset(
                {
                    RuntimePermission.ACTION_EXECUTE,
                }
            )
        ),
        ProtectedOperation.RECONCILE_ACTION: (
            frozenset(
                {
                    RuntimePermission.ACTION_RECONCILE,
                }
            )
        ),
        ProtectedOperation.READ_VERIFICATION: (
            frozenset(
                {
                    RuntimePermission.VERIFICATION_READ,
                }
            )
        ),
        ProtectedOperation.CREATE_INVESTIGATION_SESSION: (
            frozenset(
                {
                    RuntimePermission.INVESTIGATION_ADVANCE,
                }
            )
        ),
        ProtectedOperation.READ_INVESTIGATION_SESSION: (
            frozenset(
                {
                    RuntimePermission.INVESTIGATION_READ,
                }
            )
        ),
        ProtectedOperation.ADVANCE_INVESTIGATION_SESSION: (
            frozenset(
                {
                    RuntimePermission.INVESTIGATION_ADVANCE,
                }
            )
        ),
    }
)


class SecurityPolicyEngine:
    """
    Deterministic, immutable RBAC policy evaluator.

    Authentication establishes OperatorIdentity. This engine only maps roles
    to permissions and permissions to protected operations. It performs no I/O
    and has no bypass path for administrators or internal services.
    """

    def __init__(
        self,
        *,
        role_permissions: Mapping[
            OperatorRole | str,
            Iterable[
                RuntimePermission | str
            ],
        ] | None = None,
        operation_permissions: Mapping[
            ProtectedOperation | str,
            Iterable[
                RuntimePermission | str
            ],
        ] | None = None,
        policy_version: str = "v1",
    ) -> None:
        normalized_version = (
            policy_version.strip()
            if isinstance(
                policy_version,
                str,
            )
            else ""
        )

        if not normalized_version:
            raise SecurityPolicyConfigurationError(
                "Security policy version cannot be empty"
            )

        self._policy_version = (
            normalized_version
        )
        self._role_permissions = (
            self._build_role_permissions(
                DEFAULT_ROLE_PERMISSIONS
                if role_permissions is None
                else role_permissions
            )
        )
        self._operation_permissions = (
            self._build_operation_permissions(
                DEFAULT_OPERATION_PERMISSIONS
                if operation_permissions is None
                else operation_permissions
            )
        )

    @property
    def policy_version(
        self,
    ) -> str:
        return self._policy_version

    @property
    def role_permissions(
        self,
    ) -> Mapping[
        OperatorRole,
        frozenset[RuntimePermission],
    ]:
        return self._role_permissions

    @property
    def operation_permissions(
        self,
    ) -> Mapping[
        ProtectedOperation,
        frozenset[RuntimePermission],
    ]:
        return self._operation_permissions

    @staticmethod
    def _build_role_permissions(
        source: Mapping[
            OperatorRole | str,
            Iterable[
                RuntimePermission | str
            ],
        ],
    ) -> Mapping[
        OperatorRole,
        frozenset[RuntimePermission],
    ]:
        normalized: dict[
            OperatorRole,
            frozenset[RuntimePermission],
        ] = {}

        for key, values in source.items():
            role = _normalize_enum_key(
                key,
                OperatorRole,
                "operator role",
            )

            if role in normalized:
                raise SecurityPolicyConfigurationError(
                    "Security role is configured more than once: "
                    f"{role.value}"
                )

            normalized[role] = (
                _normalize_permissions(
                    values,
                    label=(
                        "Permissions for role "
                        f"{role.value}"
                    ),
                    allow_empty=True,
                )
            )

        missing_roles = (
            set(
                OperatorRole
            )
            - set(
                normalized
            )
        )

        if missing_roles:
            raise SecurityPolicyConfigurationError(
                "Security policy is missing roles: "
                + ", ".join(
                    sorted(
                        role.value
                        for role in missing_roles
                    )
                )
            )

        return MappingProxyType(
            normalized
        )

    @staticmethod
    def _build_operation_permissions(
        source: Mapping[
            ProtectedOperation | str,
            Iterable[
                RuntimePermission | str
            ],
        ],
    ) -> Mapping[
        ProtectedOperation,
        frozenset[RuntimePermission],
    ]:
        normalized: dict[
            ProtectedOperation,
            frozenset[RuntimePermission],
        ] = {}

        for key, values in source.items():
            operation = _normalize_enum_key(
                key,
                ProtectedOperation,
                "protected operation",
            )

            if operation in normalized:
                raise SecurityPolicyConfigurationError(
                    "Protected operation is configured more than once: "
                    f"{operation.value}"
                )

            normalized[operation] = (
                _normalize_permissions(
                    values,
                    label=(
                        "Permissions for operation "
                        f"{operation.value}"
                    ),
                    allow_empty=False,
                )
            )

        missing_operations = (
            set(
                ProtectedOperation
            )
            - set(
                normalized
            )
        )

        if missing_operations:
            raise SecurityPolicyConfigurationError(
                "Security policy is missing operations: "
                + ", ".join(
                    sorted(
                        operation.value
                        for operation
                        in missing_operations
                    )
                )
            )

        return MappingProxyType(
            normalized
        )

    def permissions_for(
        self,
        identity: OperatorIdentity,
    ) -> frozenset[RuntimePermission]:
        """Return the union of permissions from all authenticated roles."""

        if not identity.authenticated:
            return frozenset()

        permissions: set[
            RuntimePermission
        ] = set()

        for role in identity.roles:
            permissions.update(
                self._role_permissions[
                    role
                ]
            )

        return frozenset(
            permissions
        )

    def required_permissions_for(
        self,
        operation: ProtectedOperation,
    ) -> frozenset[RuntimePermission]:
        return self._operation_permissions[
            ProtectedOperation(
                operation
            )
        ]

    def authorize(
        self,
        identity: OperatorIdentity,
        operation: ProtectedOperation,
    ) -> AuthorizationDecision:
        normalized_operation = (
            ProtectedOperation(
                operation
            )
        )

        return AuthorizationDecision.evaluate(
            identity=identity,
            operation=normalized_operation,
            required_permissions=(
                self.required_permissions_for(
                    normalized_operation
                )
            ),
            granted_permissions=(
                self.permissions_for(
                    identity
                )
            ),
            policy_version=(
                self._policy_version
            ),
        )

    def require(
        self,
        identity: OperatorIdentity,
        operation: ProtectedOperation,
    ) -> AuthorizationDecision:
        decision = self.authorize(
            identity,
            operation,
        )

        if not decision.allowed:
            raise AuthorizationDeniedError(
                decision
            )

        return decision

    def describe(
        self,
    ) -> dict[str, object]:
        """Return a stable, credential-free policy description."""

        return {
            "policy_version": (
                self._policy_version
            ),
            "roles": {
                role.value: sorted(
                    permission.value
                    for permission
                    in permissions
                )
                for role, permissions
                in sorted(
                    self._role_permissions.items(),
                    key=lambda item: (
                        item[0].value
                    ),
                )
            },
            "operations": {
                operation.value: sorted(
                    permission.value
                    for permission
                    in permissions
                )
                for operation, permissions
                in sorted(
                    self._operation_permissions.items(),
                    key=lambda item: (
                        item[0].value
                    ),
                )
            },
        }


__all__ = [
    "AuthorizationDeniedError",
    "DEFAULT_OPERATION_PERMISSIONS",
    "DEFAULT_ROLE_PERMISSIONS",
    "SecurityPolicyConfigurationError",
    "SecurityPolicyEngine",
]
