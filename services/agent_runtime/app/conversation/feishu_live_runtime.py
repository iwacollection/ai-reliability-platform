from __future__ import annotations

import os

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from lark_channel import (
    FeishuChannel,
    PolicyConfig,
    SecurityConfig,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from services.agent_runtime.app.conversation.feishu import (
    FeishuActorAttestationRegistry,
    FeishuChatOpsActorVerifier,
    FeishuChatOpsAdapter,
    FeishuLongConnectionTrustBoundary,
)
from services.agent_runtime.app.conversation.feishu_channel_transport import (
    FeishuOfficialChannelTransport,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)


FEISHU_LIVE_ACKNOWLEDGEMENT = (
    "I_ACKNOWLEDGE_REAL_FEISHU_NETWORK_CONNECTION"
)


class FeishuLiveChannelConfigurationError(
    RuntimeError
):
    """Live Feishu Channel configuration is missing or unsafe."""


class FeishuLiveChannelAcknowledgementError(
    FeishuLiveChannelConfigurationError
):
    """The exact acknowledgement for real Feishu network access is absent."""


class FeishuLiveChannelSettings(
    BaseModel
):
    """
    Non-secret configuration for the explicit Feishu live runner.

    The object stores only environment-variable references, never App ID or
    App Secret values. Secret values are resolved only after the exact live
    acknowledgement is validated.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    app_id_env: str = (
        "AI_RELIABILITY_FEISHU_APP_ID"
    )
    app_secret_env: str = (
        "AI_RELIABILITY_FEISHU_APP_SECRET"
    )
    group_allowlist_env: str = (
        "AI_RELIABILITY_FEISHU_GROUP_ALLOWLIST"
    )
    acknowledgement_env: str = (
        "AI_RELIABILITY_FEISHU_LIVE_ACK"
    )

    connect_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        le=120.0,
    )

    @field_validator(
        "app_id_env",
        "app_secret_env",
        "group_allowlist_env",
        "acknowledgement_env",
    )
    @classmethod
    def validate_environment_reference(
        cls,
        value: str,
    ) -> str:
        if (
            not isinstance(
                value,
                str,
            )
            or not value
            or value
            != value.strip()
            or len(value) > 128
            or "\x00" in value
            or any(
                character.isspace()
                for character in value
            )
        ):
            raise ValueError(
                "Feishu live environment reference is invalid"
            )

        return value


@dataclass(frozen=True)
class FeishuLiveChannelAssembly:
    """
    Explicitly assembled read-only live Feishu ChatOps channel.

    No authenticated write bridge is attached in v1. The assembly can receive
    real messages and send read-only ChatOps replies, but Approval/Action/
    Verification writes remain unavailable until a later separately gated
    stage.
    """

    channel: Any
    transport: FeishuOfficialChannelTransport
    actor_verifier: FeishuChatOpsActorVerifier
    settings: FeishuLiveChannelSettings

    async def connect(
        self,
        *,
        acknowledgement: str,
    ) -> None:
        _require_acknowledgement(
            acknowledgement
        )

        connect_until_ready = getattr(
            self.channel,
            "connect_until_ready",
            None,
        )

        if not callable(
            connect_until_ready
        ):
            raise FeishuLiveChannelConfigurationError(
                "Feishu live channel does not support connect_until_ready"
            )

        await connect_until_ready(
            timeout=(
                self.settings
                .connect_timeout_seconds
            )
        )

    async def disconnect(
        self,
    ) -> None:
        disconnect = getattr(
            self.channel,
            "disconnect",
            None,
        )

        if not callable(
            disconnect
        ):
            raise FeishuLiveChannelConfigurationError(
                "Feishu live channel does not support disconnect"
            )

        await disconnect()


def create_feishu_live_channel_assembly(
    *,
    runtime: AgentRuntime,
    settings: (
        FeishuLiveChannelSettings
        | None
    ) = None,
    acknowledgement: str,
    environment: (
        Mapping[str, str]
        | None
    ) = None,
    channel_factory: (
        Callable[..., Any]
        | None
    ) = None,
) -> FeishuLiveChannelAssembly:
    """
    Assemble, but do not connect, one real Feishu Channel.

    Security order is deliberate:
    1. validate exact live-network acknowledgement;
    2. validate Runtime identity;
    3. resolve required secret values from environment references;
    4. require a non-empty exact group allowlist;
    5. construct SDK PolicyConfig/SecurityConfig;
    6. construct FeishuChannel;
    7. register the existing typed Transport handlers.

    This function never calls connect(), connect_until_ready(), start(), or
    start_background(). It also never attaches ChatOpsAuthenticatedWriteBridge.
    """

    _require_acknowledgement(
        acknowledgement
    )

    if not isinstance(
        runtime,
        AgentRuntime,
    ):
        raise TypeError(
            "Feishu live assembly requires AgentRuntime"
        )

    resolved_settings = (
        settings
        if settings is not None
        else FeishuLiveChannelSettings()
    )

    if not isinstance(
        resolved_settings,
        FeishuLiveChannelSettings,
    ):
        raise TypeError(
            "Feishu live settings are invalid"
        )

    env = (
        environment
        if environment is not None
        else os.environ
    )

    app_id = _required_secret(
        env.get(
            resolved_settings.app_id_env
        ),
        label="Feishu App ID",
    )

    app_secret = _required_secret(
        env.get(
            resolved_settings
            .app_secret_env
        ),
        label="Feishu App Secret",
    )

    group_allowlist = (
        _resolve_group_allowlist(
            env.get(
                resolved_settings
                .group_allowlist_env
            )
        )
    )

    policy = PolicyConfig(
        dm_policy="disabled",
        group_policy="allowlist",
        require_mention=True,
        respond_to_mention_all=False,
        group_allowlist=list(
            group_allowlist
        ),
        sender_identity_fields=[
            "open_id",
        ],
    )

    security = SecurityConfig(
        mode="audit",
        allow_insecure_ws=False,
        allow_local_insecure_ws=False,
        max_ws_fragment_parts=128,
        max_ws_fragment_bytes=(
            8 * 1024 * 1024
        ),
        max_concurrent_ws_handlers=64,
        resource_overflow_policy="drop",
    )

    factory = (
        channel_factory
        if channel_factory is not None
        else FeishuChannel
    )

    if not callable(
        factory
    ):
        raise TypeError(
            "Feishu live channel factory is invalid"
        )

    channel = factory(
        app_id=app_id,
        app_secret=app_secret,
        transport="ws",
        policy=policy,
        security=security,
    )

    trust_boundary = (
        FeishuLongConnectionTrustBoundary()
    )

    attestations = (
        FeishuActorAttestationRegistry()
    )

    adapter = FeishuChatOpsAdapter(
        trust_boundary=trust_boundary,
        attestations=attestations,
    )

    actor_verifier = (
        FeishuChatOpsActorVerifier(
            attestations
        )
    )

    transport = (
        FeishuOfficialChannelTransport(
            channel=channel,
            adapter=adapter,
            gateway=runtime.chatops,
            # v1 real-network rollout is read-only.
            write_bridge=None,
        )
    )

    transport.register()

    return FeishuLiveChannelAssembly(
        channel=channel,
        transport=transport,
        actor_verifier=actor_verifier,
        settings=resolved_settings,
    )


def acknowledgement_from_environment(
    settings: (
        FeishuLiveChannelSettings
        | None
    ) = None,
    *,
    environment: (
        Mapping[str, str]
        | None
    ) = None,
) -> str:
    """
    Read only the acknowledgement field.

    Callers can validate the live-network gate before constructing AgentRuntime
    or resolving App ID/App Secret values.
    """

    resolved_settings = (
        settings
        if settings is not None
        else FeishuLiveChannelSettings()
    )

    env = (
        environment
        if environment is not None
        else os.environ
    )

    value = env.get(
        resolved_settings
        .acknowledgement_env
    )

    if not isinstance(
        value,
        str,
    ):
        return ""

    return value


def _require_acknowledgement(
    value: str,
) -> None:
    if (
        not isinstance(
            value,
            str,
        )
        or value
        != FEISHU_LIVE_ACKNOWLEDGEMENT
    ):
        raise FeishuLiveChannelAcknowledgementError(
            "Exact Feishu live-network acknowledgement is required"
        )


def _required_secret(
    value: Any,
    *,
    label: str,
) -> str:
    if (
        not isinstance(
            value,
            str,
        )
        or not value
        or value
        != value.strip()
        or len(value) > 1024
        or "\x00" in value
    ):
        raise FeishuLiveChannelConfigurationError(
            label
            + " is unavailable"
        )

    return value


def _resolve_group_allowlist(
    value: Any,
) -> tuple[str, ...]:
    if not isinstance(
        value,
        str,
    ):
        raise FeishuLiveChannelConfigurationError(
            "Feishu group allowlist is unavailable"
        )

    raw_items = [
        item.strip()
        for item in value.split(",")
    ]

    items: list[str] = []
    seen: set[str] = set()

    for item in raw_items:
        if not item:
            continue

        if (
            len(item) > 256
            or "\x00" in item
            or any(
                character.isspace()
                for character in item
            )
            or not item.startswith(
                "oc_"
            )
        ):
            raise FeishuLiveChannelConfigurationError(
                "Feishu group allowlist contains an invalid chat ID"
            )

        if item in seen:
            continue

        seen.add(
            item
        )
        items.append(
            item
        )

    if not items:
        raise FeishuLiveChannelConfigurationError(
            "Feishu group allowlist cannot be empty"
        )

    return tuple(
        items
    )


__all__ = [
    "FEISHU_LIVE_ACKNOWLEDGEMENT",
    "FeishuLiveChannelAcknowledgementError",
    "FeishuLiveChannelAssembly",
    "FeishuLiveChannelConfigurationError",
    "FeishuLiveChannelSettings",
    "acknowledgement_from_environment",
    "create_feishu_live_channel_assembly",
]
