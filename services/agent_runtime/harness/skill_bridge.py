class HarnessSkillBridge:

    def __init__(self, skill_runtime):
        self.skill_runtime = skill_runtime

    def dispatch(self, skill_name, context):
        return self.skill_runtime.execute(
            skill_name,
            context
        )
