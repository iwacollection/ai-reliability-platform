class SkillRuntime:

    def __init__(self, registry, evidence_adapter):
        self.registry = registry
        self.evidence_adapter = evidence_adapter

    def execute(self, skill_name, context):
        skill = self.registry.get(skill_name)

        if not skill:
            return {
                "status": "FAILED",
                "reason": "skill_not_found"
            }

        result = skill.execute(context)

        return self.evidence_adapter.build(result)
