from services.agent_runtime.app.skills.registry.skill_registry import (
    SkillRegistry,
)

from services.agent_runtime.app.skills.loader import (
    load_skills,
)



def create_skill_registry() -> SkillRegistry:
    """
    Create skill registry.
    """


    registry = SkillRegistry()


    registry = load_skills(
        registry
    )


    return registry