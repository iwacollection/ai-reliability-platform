import json
from pathlib import Path

from services.agent_runtime.app.memory.base import (
    BaseMemory,
)


class MemoryStore(
    BaseMemory
):
    """
    Agent persistent memory store.
    """


    def __init__(
        self,
        file_path: str = "data/agent_memory.json",
    ):

        self.file_path = Path(
            file_path
        )


        self.file_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )


        if self.file_path.exists():

            with open(
                self.file_path,
                "r",
                encoding="utf-8",
            ) as f:

                self._store = json.load(f)


        else:

            self._store = {}



    async def save(
        self,
        key: str,
        value: dict,
    ) -> None:


        self._store[key] = value


        with open(
            self.file_path,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                self._store,
                f,
                ensure_ascii=False,
                indent=2,
            )



    async def get(
        self,
        key: str,
    ) -> dict | None:


        return self._store.get(
            key
        )