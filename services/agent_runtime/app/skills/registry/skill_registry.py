from services.agent_runtime.app.skills.base import (
    BaseSkill,
)

from typing import List



class SkillRegistry:
    """
    Registry for agent skills.
    """


    def __init__(self):

        self._skills: dict[str, BaseSkill] = {}



    def register(
        self,
        skill: BaseSkill,
    ) -> None:

        self._skills[
            skill.name
        ] = skill



    def get(
        self,
        name: str,
    ) -> BaseSkill:

        return self._skills[
            name
        ]



    def list(
        self,
    ) -> List[BaseSkill]:

        return list(
            self._skills.values()
        )



    def names(
        self,
    ) -> List[str]:

        return list(
            self._skills.keys()
        )