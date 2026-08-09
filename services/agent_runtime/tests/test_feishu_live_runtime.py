from __future__ import annotations

from collections.abc import Mapping

import pytest

from lark_channel import (
    Events,
)

from services.agent_runtime.app.conversation.feishu_live_runtime import (
    FEISHU_LIVE_ACKNOWLEDGEMENT,
    FeishuLiveChannelAcknowledgementError,
    FeishuLiveChannelConfigurationError,
    FeishuLiveChannelSettings,
    acknowledgement_from_environment,
    create_feishu_live_channel_assembly,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)


class ExplodingEnvironment(
    Mapping[str, str]
):
    def __getitem__(
        self,
        key,
    ):
        raise AssertionError(
            "Environment was read before acknowledgement"
        )

    def __iter__(
        self,
    ):
        return iter(())

    def __len__(
        self,
    ):
        return 0

    def get(
        self,
        key,
        default=None,
    ):
        raise AssertionError(
            "Environment was read before acknowledgement"
        )


class FakeChannel:
    def __init__(
        self,
        **kwargs,
    ) -> None:
        self.kwargs = kwargs
        self.handlers = {}
        self.connect_timeouts = []
        self.disconnect_calls = 0

    def on(
        self,
        event,
        handler,
    ) -> None:
        self.handlers[
            event
        ] = handler

    async def send(
        self,
        to,
        message,
        opts=None,
    ):
        raise AssertionError(
            "Live assembly test unexpectedly sent a message"
        )

    async def connect_until_ready(
        self,
        *,
        timeout=30.0,
    ) -> None:
        self.connect_timeouts.append(
            timeout
        )

    async def disconnect(
        self,
    ) -> None:
        self.disconnect_calls += 1


def environment():
    return {
        "AI_RELIABILITY_FEISHU_APP_ID": (
            "cli_test_app"
        ),
        "AI_RELIABILITY_FEISHU_APP_SECRET": (
            "test-secret-value"
        ),
        "AI_RELIABILITY_FEISHU_GROUP_ALLOWLIST": (
            "oc_group_a,oc_group_b,oc_group_a"
        ),
        "AI_RELIABILITY_FEISHU_LIVE_ACK": (
            FEISHU_LIVE_ACKNOWLEDGEMENT
        ),
    }


def build_runtime(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(
        tmp_path
    )

    monkeypatch.setenv(
        "PROMETHEUS_ALLOW_MOCK_FALLBACK",
        "true",
    )
    monkeypatch.setenv(
        "KUBERNETES_ALLOW_DRY_RUN_FALLBACK",
        "true",
    )

    return AgentRuntime()


def test_acknowledgement_is_checked_before_any_environment_read():
    runtime = object()

    with pytest.raises(
        FeishuLiveChannelAcknowledgementError
    ):
        create_feishu_live_channel_assembly(
            runtime=runtime,
            acknowledgement="wrong",
            environment=(
                ExplodingEnvironment()
            ),
            channel_factory=(
                FakeChannel
            ),
        )


def test_acknowledgement_reader_reads_only_named_reference():
    settings = (
        FeishuLiveChannelSettings()
    )

    assert (
        acknowledgement_from_environment(
            settings,
            environment=environment(),
        )
        == FEISHU_LIVE_ACKNOWLEDGEMENT
    )


def test_missing_secret_fails_closed_without_echoing_value(
    monkeypatch,
    tmp_path,
):
    runtime = build_runtime(
        monkeypatch,
        tmp_path,
    )

    values = environment()
    values.pop(
        "AI_RELIABILITY_FEISHU_APP_SECRET"
    )

    with pytest.raises(
        FeishuLiveChannelConfigurationError
    ) as exc:
        create_feishu_live_channel_assembly(
            runtime=runtime,
            acknowledgement=(
                FEISHU_LIVE_ACKNOWLEDGEMENT
            ),
            environment=values,
            channel_factory=(
                FakeChannel
            ),
        )

    assert "test-secret-value" not in (
        str(exc.value)
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "not-a-chat-id",
        "oc_ok, bad id",
    ],
)
def test_group_allowlist_is_required_and_strict(
    monkeypatch,
    tmp_path,
    value,
):
    runtime = build_runtime(
        monkeypatch,
        tmp_path,
    )

    values = environment()
    values[
        "AI_RELIABILITY_FEISHU_GROUP_ALLOWLIST"
    ] = value

    with pytest.raises(
        FeishuLiveChannelConfigurationError
    ):
        create_feishu_live_channel_assembly(
            runtime=runtime,
            acknowledgement=(
                FEISHU_LIVE_ACKNOWLEDGEMENT
            ),
            environment=values,
            channel_factory=(
                FakeChannel
            ),
        )


def test_assembly_is_audit_allowlisted_read_only_and_registers_handlers(
    monkeypatch,
    tmp_path,
):
    runtime = build_runtime(
        monkeypatch,
        tmp_path,
    )

    assembly = (
        create_feishu_live_channel_assembly(
            runtime=runtime,
            acknowledgement=(
                FEISHU_LIVE_ACKNOWLEDGEMENT
            ),
            environment=environment(),
            channel_factory=(
                FakeChannel
            ),
        )
    )

    channel = assembly.channel

    assert channel.kwargs[
        "transport"
    ] == "ws"

    assert channel.kwargs[
        "app_id"
    ] == "cli_test_app"

    assert channel.kwargs[
        "app_secret"
    ] == "test-secret-value"

    policy = channel.kwargs[
        "policy"
    ]

    assert policy.dm_policy == (
        "disabled"
    )
    assert policy.group_policy == (
        "allowlist"
    )
    assert policy.require_mention is True
    assert (
        policy.respond_to_mention_all
        is False
    )
    assert policy.group_allowlist == [
        "oc_group_a",
        "oc_group_b",
    ]
    assert policy.sender_identity_fields == [
        "open_id",
    ]

    security = channel.kwargs[
        "security"
    ]

    assert security.mode == "audit"
    assert (
        security.allow_insecure_ws
        is False
    )
    assert (
        security.allow_local_insecure_ws
        is False
    )
    assert (
        security.max_ws_fragment_parts
        == 128
    )
    assert (
        security.max_ws_fragment_bytes
        == 8 * 1024 * 1024
    )
    assert (
        security.max_concurrent_ws_handlers
        == 64
    )
    assert (
        security.resource_overflow_policy
        == "drop"
    )

    assert assembly.transport.write_bridge is None

    assert set(
        channel.handlers
    ) == {
        Events.MESSAGE,
        Events.CARD_ACTION,
    }


@pytest.mark.asyncio
async def test_connect_and_disconnect_are_explicit_and_acknowledged(
    monkeypatch,
    tmp_path,
):
    runtime = build_runtime(
        monkeypatch,
        tmp_path,
    )

    assembly = (
        create_feishu_live_channel_assembly(
            runtime=runtime,
            acknowledgement=(
                FEISHU_LIVE_ACKNOWLEDGEMENT
            ),
            environment=environment(),
            channel_factory=(
                FakeChannel
            ),
        )
    )

    with pytest.raises(
        FeishuLiveChannelAcknowledgementError
    ):
        await assembly.connect(
            acknowledgement="wrong"
        )

    assert (
        assembly.channel
        .connect_timeouts
        == []
    )

    await assembly.connect(
        acknowledgement=(
            FEISHU_LIVE_ACKNOWLEDGEMENT
        )
    )

    assert (
        assembly.channel
        .connect_timeouts
        == [30.0]
    )

    await assembly.disconnect()

    assert (
        assembly.channel
        .disconnect_calls
        == 1
    )


def test_live_runtime_source_has_no_direct_domain_write_authority():
    from pathlib import Path

    import services.agent_runtime.app.conversation.feishu_live_runtime as module

    source = Path(
        module.__file__
    ).read_text(
        encoding="utf-8"
    )

    required = [
        "SecurityConfig(",
        'mode="audit"',
        'group_policy="allowlist"',
        'dm_policy="disabled"',
        "connect_until_ready",
        "write_bridge=None",
    ]

    assert [
        item
        for item in required
        if item not in source
    ] == []

    forbidden = [
        "ApprovalService",
        "ActionRuntime",
        "VerificationCoordinator",
        ".approve(",
        ".reject(",
        ".resume(",
        "KubernetesProductionExecutor",
    ]

    assert [
        item
        for item in forbidden
        if item in source
    ] == []


def test_manual_runner_is_not_runtime_startup_wiring():
    from pathlib import Path

    source = Path(
        "scripts/dev/run_feishu_live_channel_v1.py"
    ).read_text(
        encoding="utf-8"
    )

    assert (
        'if __name__ == "__main__":'
        in source
    )
    assert (
        "FEISHU_LIVE_ACKNOWLEDGEMENT"
        in source
    )
    assert (
        "create_feishu_live_channel_assembly"
        in source
    )

    forbidden = [
        "app_secret=",
        "cli_",
        "ApprovalService",
        "ActionRuntime",
    ]

    assert [
        item
        for item in forbidden
        if item in source
    ] == []
