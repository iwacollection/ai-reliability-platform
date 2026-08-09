from __future__ import annotations

import hashlib
import subprocess
import traceback

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "feishu-live-channel-runner-runtime-assembly-v1"

AFTER_NAME = "feishu_live_channel_runner_runtime_assembly_v1_after.txt"
ERROR_NAME = "feishu_live_channel_runner_runtime_assembly_v1_error.txt"

EXPECTED_RAW_HASHES = {'pyproject.toml': 'cc2f73d19fd71c810ebf23429e5ecb4f9bd8cf6fe65ece91ba3569ce2b7e82ce', 'uv.lock': 'e2bef32ca96b736bc104ea3f3999316223f1793c4b2663c30175ae5f5fce5722', 'services/agent_runtime/app/conversation/feishu.py': 'd3869bf3fb7e6e0a7ce43934979887106a380caf90cca615414d33a7560eeea1', 'services/agent_runtime/app/conversation/feishu_channel_transport.py': '17e7cb678de5b478a0ba61f650bdb6c9c004272a23096e026b7f3cba1f34bcd8', 'services/agent_runtime/app/conversation/chatops.py': '3c73a9a86bc34712a77ac3ea3196e44ee355989f0b869b73500e83d791d80966', 'services/agent_runtime/app/conversation/identity.py': '440318d59d17155cd6e24763736243624ba758ae8eace41627ea12a5d175ec76', 'services/agent_runtime/app/conversation/write_bridge.py': 'fc9dd30b0771672d66b75a4bd0f1eb34fad7e57677c0ccba8a66a12186fd5e7c', 'services/agent_runtime/app/conversation/orchestrator.py': 'f41d09ae583479d65c486fea4d1e4d667fe81be0330a2c66c32225208a4789d1', 'services/agent_runtime/app/runtime/runtime.py': 'dfe189a4c25f0c5c48393935360956f55bfe12afe2c7d273d6d57ba330db4650', 'services/agent_runtime/app/main.py': '92a4e10087c122b8438594f6c6457f4080965edbfea43b9ffb708cb0eaa317ae', 'services/agent_runtime/tests/test_feishu_chatops_adapter.py': '856fccf71c19f78153c612e828b1ba5785a8a1b5f37c83b645505dfc206a4435', 'services/agent_runtime/tests/test_feishu_channel_transport.py': '366f528e7052c86d74b853e7448a3a8518bd164881dfdf6a56d401afd423d4fe'}

SOURCES = {'services/agent_runtime/app/conversation/feishu_live_runtime.py': 'from __future__ import annotations\n\nimport os\n\nfrom collections.abc import Callable, Mapping\nfrom dataclasses import dataclass\nfrom typing import Any\n\nfrom lark_channel import (\n    FeishuChannel,\n    PolicyConfig,\n    SecurityConfig,\n)\nfrom pydantic import (\n    BaseModel,\n    ConfigDict,\n    Field,\n    field_validator,\n)\n\nfrom services.agent_runtime.app.conversation.feishu import (\n    FeishuActorAttestationRegistry,\n    FeishuChatOpsActorVerifier,\n    FeishuChatOpsAdapter,\n    FeishuLongConnectionTrustBoundary,\n)\nfrom services.agent_runtime.app.conversation.feishu_channel_transport import (\n    FeishuOfficialChannelTransport,\n)\nfrom services.agent_runtime.app.runtime.runtime import (\n    AgentRuntime,\n)\n\n\nFEISHU_LIVE_ACKNOWLEDGEMENT = (\n    "I_ACKNOWLEDGE_REAL_FEISHU_NETWORK_CONNECTION"\n)\n\n\nclass FeishuLiveChannelConfigurationError(\n    RuntimeError\n):\n    """Live Feishu Channel configuration is missing or unsafe."""\n\n\nclass FeishuLiveChannelAcknowledgementError(\n    FeishuLiveChannelConfigurationError\n):\n    """The exact acknowledgement for real Feishu network access is absent."""\n\n\nclass FeishuLiveChannelSettings(\n    BaseModel\n):\n    """\n    Non-secret configuration for the explicit Feishu live runner.\n\n    The object stores only environment-variable references, never App ID or\n    App Secret values. Secret values are resolved only after the exact live\n    acknowledgement is validated.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    app_id_env: str = (\n        "AI_RELIABILITY_FEISHU_APP_ID"\n    )\n    app_secret_env: str = (\n        "AI_RELIABILITY_FEISHU_APP_SECRET"\n    )\n    group_allowlist_env: str = (\n        "AI_RELIABILITY_FEISHU_GROUP_ALLOWLIST"\n    )\n    acknowledgement_env: str = (\n        "AI_RELIABILITY_FEISHU_LIVE_ACK"\n    )\n\n    connect_timeout_seconds: float = Field(\n        default=30.0,\n        gt=0.0,\n        le=120.0,\n    )\n\n    @field_validator(\n        "app_id_env",\n        "app_secret_env",\n        "group_allowlist_env",\n        "acknowledgement_env",\n    )\n    @classmethod\n    def validate_environment_reference(\n        cls,\n        value: str,\n    ) -> str:\n        if (\n            not isinstance(\n                value,\n                str,\n            )\n            or not value\n            or value\n            != value.strip()\n            or len(value) > 128\n            or "\\x00" in value\n            or any(\n                character.isspace()\n                for character in value\n            )\n        ):\n            raise ValueError(\n                "Feishu live environment reference is invalid"\n            )\n\n        return value\n\n\n@dataclass(frozen=True)\nclass FeishuLiveChannelAssembly:\n    """\n    Explicitly assembled read-only live Feishu ChatOps channel.\n\n    No authenticated write bridge is attached in v1. The assembly can receive\n    real messages and send read-only ChatOps replies, but Approval/Action/\n    Verification writes remain unavailable until a later separately gated\n    stage.\n    """\n\n    channel: Any\n    transport: FeishuOfficialChannelTransport\n    actor_verifier: FeishuChatOpsActorVerifier\n    settings: FeishuLiveChannelSettings\n\n    async def connect(\n        self,\n        *,\n        acknowledgement: str,\n    ) -> None:\n        _require_acknowledgement(\n            acknowledgement\n        )\n\n        connect_until_ready = getattr(\n            self.channel,\n            "connect_until_ready",\n            None,\n        )\n\n        if not callable(\n            connect_until_ready\n        ):\n            raise FeishuLiveChannelConfigurationError(\n                "Feishu live channel does not support connect_until_ready"\n            )\n\n        await connect_until_ready(\n            timeout=(\n                self.settings\n                .connect_timeout_seconds\n            )\n        )\n\n    async def disconnect(\n        self,\n    ) -> None:\n        disconnect = getattr(\n            self.channel,\n            "disconnect",\n            None,\n        )\n\n        if not callable(\n            disconnect\n        ):\n            raise FeishuLiveChannelConfigurationError(\n                "Feishu live channel does not support disconnect"\n            )\n\n        await disconnect()\n\n\ndef create_feishu_live_channel_assembly(\n    *,\n    runtime: AgentRuntime,\n    settings: (\n        FeishuLiveChannelSettings\n        | None\n    ) = None,\n    acknowledgement: str,\n    environment: (\n        Mapping[str, str]\n        | None\n    ) = None,\n    channel_factory: (\n        Callable[..., Any]\n        | None\n    ) = None,\n) -> FeishuLiveChannelAssembly:\n    """\n    Assemble, but do not connect, one real Feishu Channel.\n\n    Security order is deliberate:\n    1. validate exact live-network acknowledgement;\n    2. validate Runtime identity;\n    3. resolve required secret values from environment references;\n    4. require a non-empty exact group allowlist;\n    5. construct SDK PolicyConfig/SecurityConfig;\n    6. construct FeishuChannel;\n    7. register the existing typed Transport handlers.\n\n    This function never calls connect(), connect_until_ready(), start(), or\n    start_background(). It also never attaches ChatOpsAuthenticatedWriteBridge.\n    """\n\n    _require_acknowledgement(\n        acknowledgement\n    )\n\n    if not isinstance(\n        runtime,\n        AgentRuntime,\n    ):\n        raise TypeError(\n            "Feishu live assembly requires AgentRuntime"\n        )\n\n    resolved_settings = (\n        settings\n        if settings is not None\n        else FeishuLiveChannelSettings()\n    )\n\n    if not isinstance(\n        resolved_settings,\n        FeishuLiveChannelSettings,\n    ):\n        raise TypeError(\n            "Feishu live settings are invalid"\n        )\n\n    env = (\n        environment\n        if environment is not None\n        else os.environ\n    )\n\n    app_id = _required_secret(\n        env.get(\n            resolved_settings.app_id_env\n        ),\n        label="Feishu App ID",\n    )\n\n    app_secret = _required_secret(\n        env.get(\n            resolved_settings\n            .app_secret_env\n        ),\n        label="Feishu App Secret",\n    )\n\n    group_allowlist = (\n        _resolve_group_allowlist(\n            env.get(\n                resolved_settings\n                .group_allowlist_env\n            )\n        )\n    )\n\n    policy = PolicyConfig(\n        dm_policy="disabled",\n        group_policy="allowlist",\n        require_mention=True,\n        respond_to_mention_all=False,\n        group_allowlist=list(\n            group_allowlist\n        ),\n        sender_identity_fields=[\n            "open_id",\n        ],\n    )\n\n    security = SecurityConfig(\n        mode="audit",\n        allow_insecure_ws=False,\n        allow_local_insecure_ws=False,\n        max_ws_fragment_parts=128,\n        max_ws_fragment_bytes=(\n            8 * 1024 * 1024\n        ),\n        max_concurrent_ws_handlers=64,\n        resource_overflow_policy="drop",\n    )\n\n    factory = (\n        channel_factory\n        if channel_factory is not None\n        else FeishuChannel\n    )\n\n    if not callable(\n        factory\n    ):\n        raise TypeError(\n            "Feishu live channel factory is invalid"\n        )\n\n    channel = factory(\n        app_id=app_id,\n        app_secret=app_secret,\n        transport="ws",\n        policy=policy,\n        security=security,\n    )\n\n    trust_boundary = (\n        FeishuLongConnectionTrustBoundary()\n    )\n\n    attestations = (\n        FeishuActorAttestationRegistry()\n    )\n\n    adapter = FeishuChatOpsAdapter(\n        trust_boundary=trust_boundary,\n        attestations=attestations,\n    )\n\n    actor_verifier = (\n        FeishuChatOpsActorVerifier(\n            attestations\n        )\n    )\n\n    transport = (\n        FeishuOfficialChannelTransport(\n            channel=channel,\n            adapter=adapter,\n            gateway=runtime.chatops,\n            # v1 real-network rollout is read-only.\n            write_bridge=None,\n        )\n    )\n\n    transport.register()\n\n    return FeishuLiveChannelAssembly(\n        channel=channel,\n        transport=transport,\n        actor_verifier=actor_verifier,\n        settings=resolved_settings,\n    )\n\n\ndef acknowledgement_from_environment(\n    settings: (\n        FeishuLiveChannelSettings\n        | None\n    ) = None,\n    *,\n    environment: (\n        Mapping[str, str]\n        | None\n    ) = None,\n) -> str:\n    """\n    Read only the acknowledgement field.\n\n    Callers can validate the live-network gate before constructing AgentRuntime\n    or resolving App ID/App Secret values.\n    """\n\n    resolved_settings = (\n        settings\n        if settings is not None\n        else FeishuLiveChannelSettings()\n    )\n\n    env = (\n        environment\n        if environment is not None\n        else os.environ\n    )\n\n    value = env.get(\n        resolved_settings\n        .acknowledgement_env\n    )\n\n    if not isinstance(\n        value,\n        str,\n    ):\n        return ""\n\n    return value\n\n\ndef _require_acknowledgement(\n    value: str,\n) -> None:\n    if (\n        not isinstance(\n            value,\n            str,\n        )\n        or value\n        != FEISHU_LIVE_ACKNOWLEDGEMENT\n    ):\n        raise FeishuLiveChannelAcknowledgementError(\n            "Exact Feishu live-network acknowledgement is required"\n        )\n\n\ndef _required_secret(\n    value: Any,\n    *,\n    label: str,\n) -> str:\n    if (\n        not isinstance(\n            value,\n            str,\n        )\n        or not value\n        or value\n        != value.strip()\n        or len(value) > 1024\n        or "\\x00" in value\n    ):\n        raise FeishuLiveChannelConfigurationError(\n            label\n            + " is unavailable"\n        )\n\n    return value\n\n\ndef _resolve_group_allowlist(\n    value: Any,\n) -> tuple[str, ...]:\n    if not isinstance(\n        value,\n        str,\n    ):\n        raise FeishuLiveChannelConfigurationError(\n            "Feishu group allowlist is unavailable"\n        )\n\n    raw_items = [\n        item.strip()\n        for item in value.split(",")\n    ]\n\n    items: list[str] = []\n    seen: set[str] = set()\n\n    for item in raw_items:\n        if not item:\n            continue\n\n        if (\n            len(item) > 256\n            or "\\x00" in item\n            or any(\n                character.isspace()\n                for character in item\n            )\n            or not item.startswith(\n                "oc_"\n            )\n        ):\n            raise FeishuLiveChannelConfigurationError(\n                "Feishu group allowlist contains an invalid chat ID"\n            )\n\n        if item in seen:\n            continue\n\n        seen.add(\n            item\n        )\n        items.append(\n            item\n        )\n\n    if not items:\n        raise FeishuLiveChannelConfigurationError(\n            "Feishu group allowlist cannot be empty"\n        )\n\n    return tuple(\n        items\n    )\n\n\n__all__ = [\n    "FEISHU_LIVE_ACKNOWLEDGEMENT",\n    "FeishuLiveChannelAcknowledgementError",\n    "FeishuLiveChannelAssembly",\n    "FeishuLiveChannelConfigurationError",\n    "FeishuLiveChannelSettings",\n    "acknowledgement_from_environment",\n    "create_feishu_live_channel_assembly",\n]\n', 'services/agent_runtime/tests/test_feishu_live_runtime.py': 'from __future__ import annotations\n\nfrom collections.abc import Mapping\n\nimport pytest\n\nfrom lark_channel import (\n    Events,\n)\n\nfrom services.agent_runtime.app.conversation.feishu_live_runtime import (\n    FEISHU_LIVE_ACKNOWLEDGEMENT,\n    FeishuLiveChannelAcknowledgementError,\n    FeishuLiveChannelConfigurationError,\n    FeishuLiveChannelSettings,\n    acknowledgement_from_environment,\n    create_feishu_live_channel_assembly,\n)\nfrom services.agent_runtime.app.runtime.runtime import (\n    AgentRuntime,\n)\n\n\nclass ExplodingEnvironment(\n    Mapping[str, str]\n):\n    def __getitem__(\n        self,\n        key,\n    ):\n        raise AssertionError(\n            "Environment was read before acknowledgement"\n        )\n\n    def __iter__(\n        self,\n    ):\n        return iter(())\n\n    def __len__(\n        self,\n    ):\n        return 0\n\n    def get(\n        self,\n        key,\n        default=None,\n    ):\n        raise AssertionError(\n            "Environment was read before acknowledgement"\n        )\n\n\nclass FakeChannel:\n    def __init__(\n        self,\n        **kwargs,\n    ) -> None:\n        self.kwargs = kwargs\n        self.handlers = {}\n        self.connect_timeouts = []\n        self.disconnect_calls = 0\n\n    def on(\n        self,\n        event,\n        handler,\n    ) -> None:\n        self.handlers[\n            event\n        ] = handler\n\n    async def send(\n        self,\n        to,\n        message,\n        opts=None,\n    ):\n        raise AssertionError(\n            "Live assembly test unexpectedly sent a message"\n        )\n\n    async def connect_until_ready(\n        self,\n        *,\n        timeout=30.0,\n    ) -> None:\n        self.connect_timeouts.append(\n            timeout\n        )\n\n    async def disconnect(\n        self,\n    ) -> None:\n        self.disconnect_calls += 1\n\n\ndef environment():\n    return {\n        "AI_RELIABILITY_FEISHU_APP_ID": (\n            "cli_test_app"\n        ),\n        "AI_RELIABILITY_FEISHU_APP_SECRET": (\n            "test-secret-value"\n        ),\n        "AI_RELIABILITY_FEISHU_GROUP_ALLOWLIST": (\n            "oc_group_a,oc_group_b,oc_group_a"\n        ),\n        "AI_RELIABILITY_FEISHU_LIVE_ACK": (\n            FEISHU_LIVE_ACKNOWLEDGEMENT\n        ),\n    }\n\n\ndef build_runtime(\n    monkeypatch,\n    tmp_path,\n):\n    monkeypatch.chdir(\n        tmp_path\n    )\n\n    monkeypatch.setenv(\n        "PROMETHEUS_ALLOW_MOCK_FALLBACK",\n        "true",\n    )\n    monkeypatch.setenv(\n        "KUBERNETES_ALLOW_DRY_RUN_FALLBACK",\n        "true",\n    )\n\n    return AgentRuntime()\n\n\ndef test_acknowledgement_is_checked_before_any_environment_read():\n    runtime = object()\n\n    with pytest.raises(\n        FeishuLiveChannelAcknowledgementError\n    ):\n        create_feishu_live_channel_assembly(\n            runtime=runtime,\n            acknowledgement="wrong",\n            environment=(\n                ExplodingEnvironment()\n            ),\n            channel_factory=(\n                FakeChannel\n            ),\n        )\n\n\ndef test_acknowledgement_reader_reads_only_named_reference():\n    settings = (\n        FeishuLiveChannelSettings()\n    )\n\n    assert (\n        acknowledgement_from_environment(\n            settings,\n            environment=environment(),\n        )\n        == FEISHU_LIVE_ACKNOWLEDGEMENT\n    )\n\n\ndef test_missing_secret_fails_closed_without_echoing_value(\n    monkeypatch,\n    tmp_path,\n):\n    runtime = build_runtime(\n        monkeypatch,\n        tmp_path,\n    )\n\n    values = environment()\n    values.pop(\n        "AI_RELIABILITY_FEISHU_APP_SECRET"\n    )\n\n    with pytest.raises(\n        FeishuLiveChannelConfigurationError\n    ) as exc:\n        create_feishu_live_channel_assembly(\n            runtime=runtime,\n            acknowledgement=(\n                FEISHU_LIVE_ACKNOWLEDGEMENT\n            ),\n            environment=values,\n            channel_factory=(\n                FakeChannel\n            ),\n        )\n\n    assert "test-secret-value" not in (\n        str(exc.value)\n    )\n\n\n@pytest.mark.parametrize(\n    "value",\n    [\n        "",\n        "   ",\n        "not-a-chat-id",\n        "oc_ok, bad id",\n    ],\n)\ndef test_group_allowlist_is_required_and_strict(\n    monkeypatch,\n    tmp_path,\n    value,\n):\n    runtime = build_runtime(\n        monkeypatch,\n        tmp_path,\n    )\n\n    values = environment()\n    values[\n        "AI_RELIABILITY_FEISHU_GROUP_ALLOWLIST"\n    ] = value\n\n    with pytest.raises(\n        FeishuLiveChannelConfigurationError\n    ):\n        create_feishu_live_channel_assembly(\n            runtime=runtime,\n            acknowledgement=(\n                FEISHU_LIVE_ACKNOWLEDGEMENT\n            ),\n            environment=values,\n            channel_factory=(\n                FakeChannel\n            ),\n        )\n\n\ndef test_assembly_is_audit_allowlisted_read_only_and_registers_handlers(\n    monkeypatch,\n    tmp_path,\n):\n    runtime = build_runtime(\n        monkeypatch,\n        tmp_path,\n    )\n\n    assembly = (\n        create_feishu_live_channel_assembly(\n            runtime=runtime,\n            acknowledgement=(\n                FEISHU_LIVE_ACKNOWLEDGEMENT\n            ),\n            environment=environment(),\n            channel_factory=(\n                FakeChannel\n            ),\n        )\n    )\n\n    channel = assembly.channel\n\n    assert channel.kwargs[\n        "transport"\n    ] == "ws"\n\n    assert channel.kwargs[\n        "app_id"\n    ] == "cli_test_app"\n\n    assert channel.kwargs[\n        "app_secret"\n    ] == "test-secret-value"\n\n    policy = channel.kwargs[\n        "policy"\n    ]\n\n    assert policy.dm_policy == (\n        "disabled"\n    )\n    assert policy.group_policy == (\n        "allowlist"\n    )\n    assert policy.require_mention is True\n    assert (\n        policy.respond_to_mention_all\n        is False\n    )\n    assert policy.group_allowlist == [\n        "oc_group_a",\n        "oc_group_b",\n    ]\n    assert policy.sender_identity_fields == [\n        "open_id",\n    ]\n\n    security = channel.kwargs[\n        "security"\n    ]\n\n    assert security.mode == "audit"\n    assert (\n        security.allow_insecure_ws\n        is False\n    )\n    assert (\n        security.allow_local_insecure_ws\n        is False\n    )\n    assert (\n        security.max_ws_fragment_parts\n        == 128\n    )\n    assert (\n        security.max_ws_fragment_bytes\n        == 8 * 1024 * 1024\n    )\n    assert (\n        security.max_concurrent_ws_handlers\n        == 64\n    )\n    assert (\n        security.resource_overflow_policy\n        == "drop"\n    )\n\n    assert assembly.transport.write_bridge is None\n\n    assert set(\n        channel.handlers\n    ) == {\n        Events.MESSAGE,\n        Events.CARD_ACTION,\n    }\n\n\n@pytest.mark.asyncio\nasync def test_connect_and_disconnect_are_explicit_and_acknowledged(\n    monkeypatch,\n    tmp_path,\n):\n    runtime = build_runtime(\n        monkeypatch,\n        tmp_path,\n    )\n\n    assembly = (\n        create_feishu_live_channel_assembly(\n            runtime=runtime,\n            acknowledgement=(\n                FEISHU_LIVE_ACKNOWLEDGEMENT\n            ),\n            environment=environment(),\n            channel_factory=(\n                FakeChannel\n            ),\n        )\n    )\n\n    with pytest.raises(\n        FeishuLiveChannelAcknowledgementError\n    ):\n        await assembly.connect(\n            acknowledgement="wrong"\n        )\n\n    assert (\n        assembly.channel\n        .connect_timeouts\n        == []\n    )\n\n    await assembly.connect(\n        acknowledgement=(\n            FEISHU_LIVE_ACKNOWLEDGEMENT\n        )\n    )\n\n    assert (\n        assembly.channel\n        .connect_timeouts\n        == [30.0]\n    )\n\n    await assembly.disconnect()\n\n    assert (\n        assembly.channel\n        .disconnect_calls\n        == 1\n    )\n\n\ndef test_live_runtime_source_has_no_direct_domain_write_authority():\n    from pathlib import Path\n\n    import services.agent_runtime.app.conversation.feishu_live_runtime as module\n\n    source = Path(\n        module.__file__\n    ).read_text(\n        encoding="utf-8"\n    )\n\n    required = [\n        "SecurityConfig(",\n        \'mode="audit"\',\n        \'group_policy="allowlist"\',\n        \'dm_policy="disabled"\',\n        "connect_until_ready",\n        "write_bridge=None",\n    ]\n\n    assert [\n        item\n        for item in required\n        if item not in source\n    ] == []\n\n    forbidden = [\n        "ApprovalService",\n        "ActionRuntime",\n        "VerificationCoordinator",\n        ".approve(",\n        ".reject(",\n        ".resume(",\n        "KubernetesProductionExecutor",\n    ]\n\n    assert [\n        item\n        for item in forbidden\n        if item in source\n    ] == []\n\n\ndef test_manual_runner_is_not_runtime_startup_wiring():\n    from pathlib import Path\n\n    source = Path(\n        "scripts/dev/run_feishu_live_channel_v1.py"\n    ).read_text(\n        encoding="utf-8"\n    )\n\n    assert (\n        \'if __name__ == "__main__":\'\n        in source\n    )\n    assert (\n        "FEISHU_LIVE_ACKNOWLEDGEMENT"\n        in source\n    )\n    assert (\n        "create_feishu_live_channel_assembly"\n        in source\n    )\n\n    forbidden = [\n        "app_secret=",\n        "cli_",\n        "ApprovalService",\n        "ActionRuntime",\n    ]\n\n    assert [\n        item\n        for item in forbidden\n        if item in source\n    ] == []\n', 'scripts/dev/run_feishu_live_channel_v1.py': 'from __future__ import annotations\n\nimport asyncio\n\nfrom services.agent_runtime.app.conversation.feishu_live_runtime import (\n    FEISHU_LIVE_ACKNOWLEDGEMENT,\n    FeishuLiveChannelSettings,\n    acknowledgement_from_environment,\n    create_feishu_live_channel_assembly,\n)\nfrom services.agent_runtime.app.runtime.runtime import (\n    AgentRuntime,\n)\n\n\nasync def main() -> None:\n    """\n    Explicit manual Feishu live runner.\n\n    Nothing imports or starts this runner automatically. The exact live-network\n    acknowledgement must exist before AgentRuntime is constructed or Feishu\n    credentials are resolved.\n    """\n\n    settings = (\n        FeishuLiveChannelSettings()\n    )\n\n    acknowledgement = (\n        acknowledgement_from_environment(\n            settings\n        )\n    )\n\n    if (\n        acknowledgement\n        != FEISHU_LIVE_ACKNOWLEDGEMENT\n    ):\n        raise RuntimeError(\n            "Refusing real Feishu connection: exact live acknowledgement is missing"\n        )\n\n    runtime = AgentRuntime()\n\n    assembly = (\n        create_feishu_live_channel_assembly(\n            runtime=runtime,\n            settings=settings,\n            acknowledgement=(\n                acknowledgement\n            ),\n        )\n    )\n\n    print("=" * 72)\n    print(\n        "FEISHU LIVE CHANNEL READ-ONLY V1"\n    )\n    print("=" * 72)\n    print()\n    print(\n        "Security mode: audit"\n    )\n    print(\n        "DM policy: disabled"\n    )\n    print(\n        "Group policy: allowlist"\n    )\n    print(\n        "Authenticated write bridge: disabled"\n    )\n    print()\n    print(\n        "Opening real Feishu Channel..."\n    )\n\n    await assembly.connect(\n        acknowledgement=(\n            acknowledgement\n        )\n    )\n\n    print()\n    print(\n        "Feishu Channel is ready."\n    )\n    print(\n        "Press Ctrl+C to stop."\n    )\n\n    try:\n        await asyncio.Event().wait()\n\n    finally:\n        print()\n        print(\n            "Disconnecting Feishu Channel..."\n        )\n\n        await assembly.disconnect()\n\n        print(\n            "Feishu Channel stopped."\n        )\n\n\nif __name__ == "__main__":\n    try:\n        asyncio.run(\n            main()\n        )\n\n    except KeyboardInterrupt:\n        pass\n'}


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def find_repo_root(
    start: Path,
) -> Path:
    for candidate in (
        start,
        *start.parents,
    ):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate

    raise RuntimeError(
        "Repository root not found. Run this installer inside ai-reliability-platform."
    )


def normalize(
    value: str,
) -> str:
    return (
        value
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def raw_sha256(
    path: Path,
) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def write_text(
    path: Path,
    value: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        value,
        encoding="utf-8",
        newline="\n",
    )


def section(
    report: list[str],
    title: str,
) -> None:
    report.extend(
        [
            "",
            "=" * 120,
            title,
            "=" * 120,
            "",
        ]
    )


def run_command(
    *,
    root: Path,
    name: str,
    command: list[str],
) -> CommandResult:
    process = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    return CommandResult(
        name=name,
        command=tuple(
            command
        ),
        returncode=(
            process.returncode
        ),
        stdout=normalize(
            process.stdout
        ),
        stderr=normalize(
            process.stderr
        ),
    )


def add_command(
    report: list[str],
    result: CommandResult,
) -> None:
    section(
        report,
        "COMMAND: "
        + result.name,
    )

    report.extend(
        [
            " ".join(
                result.command
            ),
            "",
            "ExitCode: "
            + str(
                result.returncode
            ),
            "",
            "STDOUT",
            "-" * 120,
            (
                result.stdout.rstrip()
                or "<EMPTY>"
            ),
            "",
            "STDERR",
            "-" * 120,
            (
                result.stderr.rstrip()
                or "<EMPTY>"
            ),
        ]
    )


def verify_hashes(
    root: Path,
    report: list[str],
) -> None:
    for relative, expected in (
        EXPECTED_RAW_HASHES.items()
    ):
        path = (
            root
            / relative
        )

        if not path.exists():
            raise RuntimeError(
                "Required current file is missing: "
                + relative
            )

        actual = raw_sha256(
            path
        )

        report.append(
            relative
            + "="
            + actual
        )

        if actual != expected:
            raise RuntimeError(
                relative
                + " changed after the reviewed Live Runner snapshot. "
                + "expected_raw_sha256="
                + expected
                + " actual_raw_sha256="
                + actual
                + ". Refusing stale installation; capture current code again."
            )


def remove_created(
    created: list[Path],
) -> None:
    for path in reversed(
        created
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after = (
        root
        / AFTER_NAME
    )
    error = (
        root
        / ERROR_NAME
    )

    for output in (
        after,
        error,
    ):
        try:
            output.unlink()
        except FileNotFoundError:
            pass

    targets = {
        (
            root
            / relative
        ): source
        for relative, source
        in SOURCES.items()
    }

    created: list[Path] = []

    report = [
        "Feishu Live Channel Runner + Runtime Assembly v1",
        (
            "GeneratedAt: "
            + datetime.now()
            .astimezone()
            .isoformat()
        ),
        "",
        "Reviewed baseline:",
        "- lark-channel-sdk==1.2.0 is installed",
        "- websockets==15.0.1 is the resolved SDK dependency",
        "- Feishu Core + Official Channel Transport are full-suite green",
        "- AgentRuntime has no automatic Feishu startup wiring",
        "",
        "Live v1 capability:",
        "- explicit environment-reference settings",
        "- exact live-network acknowledgement before secret resolution",
        "- Feishu App ID/App Secret resolved only during explicit assembly",
        "- SecurityConfig(mode='audit')",
        "- remote/local insecure WebSocket disabled",
        "- bounded WebSocket fragments and handler concurrency",
        "- DM disabled",
        "- group policy exact allowlist",
        "- require bot mention",
        "- official FeishuChannel constructed only by explicit live assembly",
        "- explicit connect_until_ready()/disconnect() lifecycle",
        "- manual scripts/dev runner",
        "",
        "Critical safety boundary:",
        "- v1 real-network rollout is READ-ONLY ChatOps",
        "- ChatOpsAuthenticatedWriteBridge is NOT attached",
        "- Approval / Action / Verification writes remain unavailable from live Feishu",
        "- main.py and AgentRuntime startup are NOT modified",
        "",
        "Installer safety:",
        "- does not read App ID/App Secret",
        "- does not read live acknowledgement",
        "- does not construct FeishuChannel",
        "- does not connect to Feishu",
        "- does not send a Feishu message",
        "- does not call LLM/Kubernetes/Prometheus",
        "- creates new files only",
        "- removes all created files on failure",
    ]

    try:
        section(
            report,
            "CURRENT RAW HASH PREFLIGHT",
        )

        verify_hashes(
            root,
            report,
        )

        section(
            report,
            "NEW TARGET PREFLIGHT",
        )

        for path in targets:
            relative = str(
                path.relative_to(
                    root
                )
            ).replace(
                "\\",
                "/",
            )

            if path.exists():
                raise RuntimeError(
                    "Live Runner v1 target already exists; refusing overwrite: "
                    + relative
                )

            report.append(
                "new_target="
                + relative
            )

        for path, source in (
            targets.items()
        ):
            write_text(
                path,
                source,
            )
            created.append(
                path
            )

        syntax = run_command(
            root=root,
            name="Feishu Live Runner Python syntax",
            command=[
                "uv",
                "run",
                "python",
                "-m",
                "py_compile",
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
                    for path in targets
                ],
            ],
        )
        add_command(
            report,
            syntax,
        )

        if syntax.returncode != 0:
            raise RuntimeError(
                "Feishu Live Runner syntax failed"
            )

        focused = run_command(
            root=root,
            name="Feishu Live Runtime focused tests",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_feishu_live_runtime.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_feishu_channel_transport.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_feishu_chatops_adapter.py"
                ),
                "-q",
            ],
        )
        add_command(
            report,
            focused,
        )

        if focused.returncode != 0:
            raise RuntimeError(
                "Feishu Live Runtime focused tests failed"
            )

        chatops_compat = run_command(
            root=root,
            name="ChatOps identity/write compatibility",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_chatops_authenticated_write_bridge.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_durable_conversation_chatops_contract.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_security_authentication_service.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_security_policy.py"
                ),
                "-q",
            ],
        )
        add_command(
            report,
            chatops_compat,
        )

        if chatops_compat.returncode != 0:
            raise RuntimeError(
                "Feishu Live ChatOps compatibility failed"
            )

        architecture = run_command(
            root=root,
            name="Live Runner architecture boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "p=Path(r'services/agent_runtime/app/conversation/"
                    "feishu_live_runtime.py').read_text(encoding='utf-8'); "
                    "r=Path(r'scripts/dev/run_feishu_live_channel_v1.py')"
                    ".read_text(encoding='utf-8'); "
                    "required=["
                    "'SecurityConfig(',"
                    "'mode=\\\"audit\\\"',"
                    "'group_policy=\\\"allowlist\\\"',"
                    "'dm_policy=\\\"disabled\\\"',"
                    "'connect_until_ready',"
                    "'write_bridge=None']; "
                    "forbidden=["
                    "'ApprovalService','ActionRuntime','VerificationCoordinator',"
                    "'.approve(','.reject(','.resume(',"
                    "'KubernetesProductionExecutor']; "
                    "missing=[x for x in required if x not in p]; "
                    "bad=[x for x in forbidden if x in p]; "
                    "runner_bad=[x for x in ['app_secret=','cli_','ApprovalService','ActionRuntime'] if x in r]; "
                    "print('missing='+str(missing)); "
                    "print('forbidden='+str(bad)); "
                    "print('runner_forbidden='+str(runner_bad)); "
                    "raise SystemExit(1 if missing or bad or runner_bad else 0)"
                ),
            ],
        )
        add_command(
            report,
            architecture,
        )

        if architecture.returncode != 0:
            raise RuntimeError(
                "Feishu Live Runner architecture boundary failed"
            )

        main_unchanged = (
            raw_sha256(
                root
                / "services/agent_runtime/app/main.py"
            )
            == EXPECTED_RAW_HASHES[
                "services/agent_runtime/app/main.py"
            ]
        )

        runtime_unchanged = (
            raw_sha256(
                root
                / "services/agent_runtime/app/runtime/runtime.py"
            )
            == EXPECTED_RAW_HASHES[
                "services/agent_runtime/app/runtime/runtime.py"
            ]
        )

        section(
            report,
            "NO AUTOMATIC STARTUP WIRING CHECK",
        )

        report.extend(
            [
                "main_py_unchanged="
                + str(
                    main_unchanged
                ),
                "runtime_py_unchanged="
                + str(
                    runtime_unchanged
                ),
            ]
        )

        if (
            not main_unchanged
            or not runtime_unchanged
        ):
            raise RuntimeError(
                "Live Runner unexpectedly modified automatic Runtime entrypoints"
            )

        lock_check = run_command(
            root=root,
            name="uv lock consistency",
            command=[
                "uv",
                "lock",
                "--check",
            ],
        )
        add_command(
            report,
            lock_check,
        )

        if lock_check.returncode != 0:
            raise RuntimeError(
                "uv lock consistency failed"
            )

        full_suite = run_command(
            root=root,
            name="Agent Runtime full test suite",
            command=[
                "uv",
                "run",
                "pytest",
                "services/agent_runtime/tests",
                "-q",
            ],
        )
        add_command(
            report,
            full_suite,
        )

        if full_suite.returncode != 0:
            raise RuntimeError(
                "Agent Runtime full test suite failed"
            )

        status = run_command(
            root=root,
            name="Git status for Live Runner v1 targets",
            command=[
                "git",
                "status",
                "--short",
                "--",
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
                    for path in targets
                ],
            ],
        )
        add_command(
            report,
            status,
        )

        section(
            report,
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "Feishu Live Channel Runner + Runtime Assembly v1 is installed.",
                "",
                "Installed live boundary:",
                "1. exact acknowledgement before secret resolution",
                "2. App ID/App Secret remain environment-only",
                "3. SecurityConfig audit mode with explicit resource bounds",
                "4. DM disabled",
                "5. group allowlist required",
                "6. bot mention required",
                "7. FeishuChannel lifecycle is explicit",
                "8. Transport registers existing message/card handlers",
                "9. real Feishu ChatOps is read-only in v1",
                "10. no automatic AgentRuntime startup wiring",
                "",
                "Still NOT enabled:",
                "- no live connection was opened by this installer",
                "- no authenticated Feishu Approval/Action write bridge",
                "- no automatic service startup",
                "- no strict security mode yet",
                "",
                "Next stage after review:",
                "- Feishu Live Read-Only Connectivity Preflight v1",
                "- then Authenticated Live Write Enablement only after real read path is proven",
                "",
                "Upload only: "
                + AFTER_NAME,
            ]
        )

        after.write_text(
            "\n".join(
                report
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print(
            "FEISHU LIVE CHANNEL RUNNER + RUNTIME ASSEMBLY V1 PASSED"
        )
        print("=" * 72)
        print()
        print(
            "Installer opened no Feishu connection."
        )
        print(
            "Installer read no App ID/App Secret."
        )
        print(
            "Live Feishu write authority remains disabled."
        )
        print()
        print(
            "Upload only:"
        )
        print(after)

        return 0

    except Exception as exc:
        remove_created(
            created
        )

        report.extend(
            [
                "",
                "=" * 120,
                "ROLLBACK",
                "=" * 120,
                "",
                "All newly created Live Runner v1 files were removed.",
                "No existing production source file was modified.",
            ]
        )

        error.write_text(
            "\n".join(
                [
                    (
                        "Feishu Live Channel Runner + "
                        "Runtime Assembly v1 FAILED"
                    ),
                    (
                        "GeneratedAt: "
                        + datetime.now()
                        .astimezone()
                        .isoformat()
                    ),
                    "",
                    (
                        type(exc).__name__
                        + ": "
                        + str(exc)
                    ),
                    "",
                    traceback.format_exc(),
                    "",
                    "PARTIAL REPORT",
                    "=" * 120,
                    *report,
                    "",
                    "Upload only: "
                    + ERROR_NAME,
                ]
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        print("=" * 72)
        print(
            "FEISHU LIVE CHANNEL RUNNER + RUNTIME ASSEMBLY V1 FAILED"
        )
        print("=" * 72)
        print()
        print(
            "New Live Runner files were rolled back."
        )
        print(
            "Existing production source files were not modified."
        )
        print()
        print(
            "Upload only:"
        )
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
