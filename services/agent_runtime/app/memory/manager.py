from services.agent_runtime.app.memory.store import (
    MemoryStore,
)


class MemoryManager:
    """
    Memory business layer.
    """


    def __init__(
        self,
        store: MemoryStore,
    ):

        self.store = store



    async def remember(
        self,
        key: str,
        value: dict,
    ) -> None:

        await self.store.save(
            key,
            value,
        )



    async def recall(
        self,
        key: str,
    ) -> dict | None:

        return await self.store.get(
            key,
        )