from __future__ import annotations

import asyncio

from services.agent_runtime.app.conversation.feishu_live_runtime import (
    FEISHU_LIVE_ACKNOWLEDGEMENT,
    FeishuLiveChannelSettings,
    acknowledgement_from_environment,
    create_feishu_live_channel_assembly,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)


async def main() -> None:
    """
    Explicit manual Feishu live runner.

    Nothing imports or starts this runner automatically. The exact live-network
    acknowledgement must exist before AgentRuntime is constructed or Feishu
    credentials are resolved.
    """

    settings = (
        FeishuLiveChannelSettings()
    )

    acknowledgement = (
        acknowledgement_from_environment(
            settings
        )
    )

    if (
        acknowledgement
        != FEISHU_LIVE_ACKNOWLEDGEMENT
    ):
        raise RuntimeError(
            "Refusing real Feishu connection: exact live acknowledgement is missing"
        )

    runtime = AgentRuntime()

    assembly = (
        create_feishu_live_channel_assembly(
            runtime=runtime,
            settings=settings,
            acknowledgement=(
                acknowledgement
            ),
        )
    )

    print("=" * 72)
    print(
        "FEISHU LIVE CHANNEL READ-ONLY V1"
    )
    print("=" * 72)
    print()
    print(
        "Security mode: audit"
    )
    print(
        "DM policy: disabled"
    )
    print(
        "Group policy: allowlist"
    )
    print(
        "Authenticated write bridge: disabled"
    )
    print()
    print(
        "Opening real Feishu Channel..."
    )

    await assembly.connect(
        acknowledgement=(
            acknowledgement
        )
    )

    print()
    print(
        "Feishu Channel is ready."
    )
    print(
        "Press Ctrl+C to stop."
    )

    try:
        await asyncio.Event().wait()

    finally:
        print()
        print(
            "Disconnecting Feishu Channel..."
        )

        await assembly.disconnect()

        print(
            "Feishu Channel stopped."
        )


if __name__ == "__main__":
    try:
        asyncio.run(
            main()
        )

    except KeyboardInterrupt:
        pass
