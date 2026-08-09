from __future__ import annotations

import hashlib
import json
import os

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    model_validator,
)

from services.agent_runtime.app.conversation.chatops import (
    ChatOpsInboundMessage,
)
from services.agent_runtime.app.security.authentication import (
    AuthenticationError,
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


ShortText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=256,
    ),
]


class ChatOpsIdentityError(RuntimeError):
    """Base fail-closed ChatOps identity error."""


class ChatOpsActorVerificationError(
    ChatOpsIdentityError
):
    """A channel actor could not be verified."""


class ChatOpsIdentityBindingError(
    ChatOpsIdentityError
):
    """A verified channel actor has no safe Runtime identity binding."""


class ChatOpsIdentityAuthenticationError(
    ChatOpsIdentityError
):
    """The configured Runtime credential could not authenticate safely."""


class ChatOpsAuthorizationDeniedError(
    ChatOpsIdentityError
):
    """The authenticated Runtime principal is not allowed to write."""


class ChatOpsVerifiedActor(BaseModel):
    """
    Channel actor identity emitted only by a trusted verifier.

    No Runtime role or permission is carried here. Those remain owned by the
    existing AuthenticationService and SecurityPolicyEngine.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    channel: ShortText
    tenant_id: ShortText | None = None
    external_actor_id: ShortText
    verification_method: ShortText

    def actor_fingerprint(
        self,
    ) -> str:
        payload = json.dumps(
            [
                self.channel,
                self.tenant_id or "",
                self.external_actor_id,
            ],
            ensure_ascii=False,
            separators=(
                ",",
                ":",
            ),
        )

        return hashlib.sha256(
            payload.encode(
                "utf-8"
            )
        ).hexdigest()


class BaseChatOpsActorVerifier(
    ABC
):
    """
    Trusted channel-transport actor verifier contract.

    Concrete Feishu/DingTalk/Slack adapters must verify their transport before
    returning ChatOpsVerifiedActor. The core has no permissive default.
    """

    @abstractmethod
    async def verify(
        self,
        message: ChatOpsInboundMessage,
    ) -> ChatOpsVerifiedActor:
        raise NotImplementedError


class ChatOpsIdentityBinding(BaseModel):
    """
    Operator-configured bridge from one verified channel actor to an existing
    Runtime authentication credential reference.

    credential_env contains only an environment variable name. It never stores
    the credential value and does not assign roles or permissions.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    channel: ShortText
    tenant_id: ShortText | None = None
    external_actor_id: ShortText
    expected_principal_id: ShortText
    credential_env: ShortText
    provider_name: ShortText | None = None

    @model_validator(
        mode="after"
    )
    def validate_environment_reference(
        self,
    ) -> "ChatOpsIdentityBinding":
        if any(
            character.isspace()
            for character
            in self.credential_env
        ):
            raise ValueError(
                "ChatOps credential environment reference is invalid"
            )

        return self

    def key(
        self,
    ) -> tuple[
        str,
        str,
        str,
    ]:
        return (
            self.channel,
            self.tenant_id
            or "",
            self.external_actor_id,
        )


class ChatOpsIdentityBindingRegistry:
    """Immutable exact-match registry for verified actor identity bindings."""

    def __init__(
        self,
        bindings: Iterable[
            ChatOpsIdentityBinding
        ],
    ) -> None:
        values = tuple(
            ChatOpsIdentityBinding
            .model_validate(
                item
            )
            for item in bindings
        )

        if not values:
            raise ValueError(
                "ChatOps identity binding registry cannot be empty"
            )

        mapping: dict[
            tuple[
                str,
                str,
                str,
            ],
            ChatOpsIdentityBinding,
        ] = {}

        for item in values:
            key = item.key()

            if key in mapping:
                raise ValueError(
                    "Duplicate ChatOps identity binding"
                )

            mapping[
                key
            ] = item

        self._bindings = values
        self._mapping = mapping

    @property
    def binding_count(
        self,
    ) -> int:
        return len(
            self._bindings
        )

    def get(
        self,
        actor: ChatOpsVerifiedActor,
    ) -> ChatOpsIdentityBinding:
        if not isinstance(
            actor,
            ChatOpsVerifiedActor,
        ):
            raise ChatOpsIdentityBindingError(
                "ChatOps verified actor is invalid"
            )

        key = (
            actor.channel,
            actor.tenant_id
            or "",
            actor.external_actor_id,
        )

        binding = self._mapping.get(
            key
        )

        if binding is None:
            raise ChatOpsIdentityBindingError(
                "Verified ChatOps actor is not bound to a Runtime identity"
            )

        return binding


class ChatOpsIdentityAuthenticator:
    """
    Authenticate one verified actor through the existing AuthenticationService.

    The configured environment credential is read only for the duration of the
    authentication call and is never retained or serialized by this object.
    """

    def __init__(
        self,
        *,
        authentication: AuthenticationService,
        bindings: ChatOpsIdentityBindingRegistry,
    ) -> None:
        if not isinstance(
            authentication,
            AuthenticationService,
        ):
            raise TypeError(
                "ChatOps identity authenticator requires AuthenticationService"
            )

        if not isinstance(
            bindings,
            ChatOpsIdentityBindingRegistry,
        ):
            raise TypeError(
                "ChatOps identity authenticator requires binding registry"
            )

        self.authentication = authentication
        self.bindings = bindings

    def authenticate(
        self,
        actor: ChatOpsVerifiedActor,
    ) -> OperatorIdentity:
        binding = self.bindings.get(
            actor
        )

        credential = os.environ.get(
            binding.credential_env
        )

        if (
            not isinstance(
                credential,
                str,
            )
            or not credential
        ):
            raise ChatOpsIdentityAuthenticationError(
                "ChatOps Runtime authentication is unavailable"
            )

        try:
            identity = (
                self.authentication
                .authenticate(
                    credential,
                    provider_name=(
                        binding.provider_name
                    ),
                )
            )

        except (
            AuthenticationError,
            AuthenticationProviderUnavailableError,
            AuthenticationProviderExecutionError,
            AuthenticationProviderContractError,
            AuthenticationServiceConfigurationError,
        ):
            raise ChatOpsIdentityAuthenticationError(
                "ChatOps Runtime authentication failed"
            ) from None

        except Exception:
            raise ChatOpsIdentityAuthenticationError(
                "ChatOps Runtime authentication is unavailable"
            ) from None

        if (
            identity.principal_id
            != binding.expected_principal_id
        ):
            raise ChatOpsIdentityAuthenticationError(
                "ChatOps Runtime principal binding does not match"
            )

        return identity


class ChatOpsSecurityContext(BaseModel):
    """Credential-free authenticated and authorized ChatOps write context."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    verified_actor: ChatOpsVerifiedActor
    identity: OperatorIdentity
    authorization: AuthorizationDecision

    @model_validator(
        mode="after"
    )
    def validate_context(
        self,
    ) -> "ChatOpsSecurityContext":
        if not self.identity.authenticated:
            raise ValueError(
                "ChatOps security context requires authenticated identity"
            )

        if not self.authorization.allowed:
            raise ValueError(
                "ChatOps security context requires allowed authorization"
            )

        if (
            self.authorization.principal_id
            != self.identity.principal_id
        ):
            raise ValueError(
                "ChatOps identity and authorization do not match"
            )

        return self

    @property
    def principal_id(
        self,
    ) -> str:
        return self.identity.principal_id

    def stable_audit_context(
        self,
    ) -> dict[str, Any]:
        """
        Stable credential-free audit metadata suitable for idempotent decisions.

        Dynamic authentication timestamps are deliberately excluded because the
        exact same ChatOps webhook replay must produce the same decision data.
        """

        return {
            "principal_id": (
                self.identity.principal_id
            ),
            "authentication_method": (
                self.identity.authentication_method.value
            ),
            "roles": sorted(
                role.value
                for role
                in self.identity.roles
            ),
            "protected_operation": (
                self.authorization.operation.value
            ),
            "policy_version": (
                self.authorization.policy_version
            ),
            "chatops_channel": (
                self.verified_actor.channel
            ),
            "chatops_actor_fingerprint": (
                self.verified_actor
                .actor_fingerprint()
            ),
            "chatops_verification_method": (
                self.verified_actor
                .verification_method
            ),
        }


class ChatOpsSecurityAdapter:
    """
    Verify channel actor, authenticate via Runtime AuthenticationService, then
    authorize with the existing SecurityPolicyEngine before domain work.
    """

    def __init__(
        self,
        *,
        verifier: BaseChatOpsActorVerifier,
        authenticator: ChatOpsIdentityAuthenticator,
        policy: SecurityPolicyEngine,
    ) -> None:
        if not isinstance(
            verifier,
            BaseChatOpsActorVerifier,
        ):
            raise TypeError(
                "ChatOps actor verifier is invalid"
            )

        if not isinstance(
            authenticator,
            ChatOpsIdentityAuthenticator,
        ):
            raise TypeError(
                "ChatOps identity authenticator is invalid"
            )

        if not isinstance(
            policy,
            SecurityPolicyEngine,
        ):
            raise TypeError(
                "ChatOps security policy is invalid"
            )

        self.verifier = verifier
        self.authenticator = authenticator
        self.policy = policy

    async def require(
        self,
        message: ChatOpsInboundMessage,
        operation: ProtectedOperation,
    ) -> ChatOpsSecurityContext:
        if not isinstance(
            message,
            ChatOpsInboundMessage,
        ):
            raise ChatOpsActorVerificationError(
                "ChatOps inbound message is invalid"
            )

        if message.external_actor_id is None:
            raise ChatOpsActorVerificationError(
                "ChatOps write requires a verified external actor"
            )

        try:
            actor = await self.verifier.verify(
                message
            )

        except ChatOpsIdentityError:
            raise

        except Exception:
            raise ChatOpsActorVerificationError(
                "ChatOps actor verification failed"
            ) from None

        if not isinstance(
            actor,
            ChatOpsVerifiedActor,
        ):
            raise ChatOpsActorVerificationError(
                "ChatOps actor verifier returned an invalid result"
            )

        if (
            actor.channel
            != message.conversation.channel
            or actor.tenant_id
            != message.conversation.tenant_id
            or actor.external_actor_id
            != message.external_actor_id
        ):
            raise ChatOpsActorVerificationError(
                "Verified ChatOps actor does not match the inbound message"
            )

        identity = (
            self.authenticator
            .authenticate(
                actor
            )
        )

        try:
            decision = self.policy.require(
                identity,
                ProtectedOperation(
                    operation
                ),
            )

        except AuthorizationDeniedError:
            raise ChatOpsAuthorizationDeniedError(
                "ChatOps operator is not authorized for this operation"
            ) from None

        return ChatOpsSecurityContext(
            verified_actor=actor,
            identity=identity,
            authorization=decision,
        )


__all__ = [
    "BaseChatOpsActorVerifier",
    "ChatOpsActorVerificationError",
    "ChatOpsAuthorizationDeniedError",
    "ChatOpsIdentityAuthenticationError",
    "ChatOpsIdentityBinding",
    "ChatOpsIdentityBindingError",
    "ChatOpsIdentityBindingRegistry",
    "ChatOpsIdentityAuthenticator",
    "ChatOpsIdentityError",
    "ChatOpsSecurityAdapter",
    "ChatOpsSecurityContext",
    "ChatOpsVerifiedActor",
]
