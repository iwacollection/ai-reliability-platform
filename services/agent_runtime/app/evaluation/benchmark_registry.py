from dataclasses import dataclass


@dataclass
class IncidentBenchmark:
    name: str
    category: str
    expected_rca: str


class BenchmarkRegistry:
    def __init__(self):
        self.items: list[IncidentBenchmark] = []

    def register(self, benchmark: IncidentBenchmark):
        self.items.append(benchmark)

    def list_all(self):
        return self.items


DEFAULT_BENCHMARKS = [
    IncidentBenchmark("pod_oom", "kubernetes", "memory_pressure"),
    IncidentBenchmark("db_deadlock", "database", "transaction_lock"),
    IncidentBenchmark("network_latency", "network", "dependency_latency"),
]
