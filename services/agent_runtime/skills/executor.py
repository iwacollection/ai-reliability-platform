from abc import ABC, abstractmethod


class SkillExecutor(ABC):

    @abstractmethod
    def execute(self, context):
        pass
