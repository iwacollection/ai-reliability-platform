from abc import ABC, abstractmethod

from common.domain.event import StandardEvent
from common.domain.raw_event import RawEvent


class BaseParser(ABC):

    @abstractmethod
    def parse(
        self,
        raw_event: RawEvent,
    ) -> StandardEvent:
        ...