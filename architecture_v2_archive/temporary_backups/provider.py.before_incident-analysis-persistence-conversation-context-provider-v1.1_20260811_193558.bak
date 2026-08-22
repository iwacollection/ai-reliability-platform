from __future__ import annotations

from abc import ABC, abstractmethod

from services.agent_runtime.app.conversation.models import (
    ConversationIncidentContext,
)


class BaseConversationIncidentContextProvider(ABC):
    """
    Read-only bridge into existing Runtime persistence.

    Mutation methods are deliberately absent.
    """

    @abstractmethod
    async def get(
        self,
        incident_id: str,
    ) -> ConversationIncidentContext | None:
        raise NotImplementedError


class DictConversationIncidentContextProvider(
    BaseConversationIncidentContextProvider
):
    def __init__(
        self,
        items: dict[
            str,
            ConversationIncidentContext,
        ] | None = None,
    ) -> None:
        self._items = dict(items or {})

    async def get(
        self,
        incident_id: str,
    ) -> ConversationIncidentContext | None:
        return self._items.get(incident_id)
