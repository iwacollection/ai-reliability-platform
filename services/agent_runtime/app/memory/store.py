from services.agent_runtime.app.memory.base import (
    BaseMemory,
)


class MemoryStore(BaseMemory):
    """
    In-memory storage.

    Later can replace with:
    Redis
    PostgreSQL
    Vector DB
    """


    def __init__(self):

        self._data: dict[str, dict] = {}


    async def save(
        self,
        key: str,
        value: dict,
    ) -> None:

        self._data[key] = value


    async def get(
        self,
        key: str,
    ) -> dict | None:

        return self._data.get(
            key
        )