
class PodOOMAutonomousRunner:
    def __init__(self, planner, investigator, memory):
        self.planner = planner
        self.investigator = investigator
        self.memory = memory

    def run(self, incident):
        plan = self.planner.create_plan(incident)
        evidence = self.investigator.execute(plan)
        result = {
            "incident": incident,
            "plan": plan,
            "evidence": evidence
        }
        self.memory.update(result)
        return result
