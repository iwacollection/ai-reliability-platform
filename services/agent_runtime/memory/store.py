from .models import IncidentMemory, MemoryQuery


class IncidentMemoryStore:
    """Simple memory abstraction.

    Production implementations can replace this with vector DB,
    graph database or incident knowledge lake backends.
    """

    def __init__(self):
        self._records: list[IncidentMemory] = []

    def save(self, memory: IncidentMemory) -> None:
        self._records.append(memory)

    def search(self, query: MemoryQuery) -> list[IncidentMemory]:
        results = self._records

        if query.service:
            results = [r for r in results if r.service == query.service]

        if query.root_cause:
            results = [r for r in results if query.root_cause.lower() in r.root_cause.lower()]

        return results[: query.limit]
