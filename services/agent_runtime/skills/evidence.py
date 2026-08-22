class SkillEvidenceAdapter:

    def build(self, result):
        return {
            "skill": result.get("skill"),
            "status": result.get("status"),
            "evidence": result.get("evidence", [])
        }
