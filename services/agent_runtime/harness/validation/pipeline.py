class ValidationPipeline:

    def __init__(self, runner, collector, store, gate):
        self.runner = runner
        self.collector = collector
        self.store = store
        self.gate = gate

    def execute(self, scenarios):
        evidence = []

        for scenario in scenarios:
            result = self.runner.run(scenario)
            item = self.collector.collect(scenario, result)
            self.store.save(item)
            evidence.append(item)

        return {
            "evidence": evidence,
            "gate": self.gate.check(evidence)
        }
