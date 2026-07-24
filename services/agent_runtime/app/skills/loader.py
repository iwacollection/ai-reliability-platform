import importlib
import pkgutil

from services.agent_runtime.app.skills.base import (
    BaseSkill,
)

from services.agent_runtime.app.skills.registry.skill_registry import (
    SkillRegistry,
)


def load_skills(
    registry: SkillRegistry,
) -> SkillRegistry:
    """
    Auto discover and load skills.
    """


    package_name = (
        "services.agent_runtime.app.skills"
    )


    package = importlib.import_module(
        package_name
    )


    for module_info in pkgutil.iter_modules(
        package.__path__
    ):

        module_name = module_info.name


        #
        # skip internal modules
        #
        if module_name in [
            "base",
            "loader",
            "factory",
        ]:
            continue



        module = importlib.import_module(
            f"{package_name}.{module_name}"
        )



        for attribute_name in dir(module):


            attribute = getattr(
                module,
                attribute_name,
            )


            if (
                isinstance(
                    attribute,
                    type,
                )
                and issubclass(
                    attribute,
                    BaseSkill,
                )
                and attribute is not BaseSkill
            ):


                registry.register(
                    attribute()
                )



    return registry