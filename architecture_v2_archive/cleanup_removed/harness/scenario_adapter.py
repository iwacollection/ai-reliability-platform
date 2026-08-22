
class ScenarioReplayAdapter:
    def run(self, scenario, agent):
        plan = agent.create_plan(scenario)
        return {
            "scenario": scenario,
            "plan": plan,
            "status": "replayed"
        }
