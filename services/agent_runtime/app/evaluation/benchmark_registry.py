from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class IncidentBenchmark:
    name: str
    category: str
    expected_rca: str
    expected_evidence: List[str] = field(default_factory=list)


class BenchmarkRegistry:
    def __init__(self):
        self.items: Dict[str, IncidentBenchmark] = {}

    def register(self, benchmark: IncidentBenchmark):
        self.items[benchmark.name] = benchmark

    def get(self, name: str):
        return self.items.get(name)

    def list_all(self):
        return list(self.items.values())


registry = BenchmarkRegistry()

DEFAULT_BENCHMARKS = [
    IncidentBenchmark(
        "pod_oom",
        "kubernetes",
        "memory_pressure",
        ["oom_killed", "memory_growth", "restart_spike"],
    ),
    IncidentBenchmark(
        "db_deadlock",
        "database",
        "transaction_lock",
        ["lock_wait", "slow_query", "transaction_block"],
    ),
    IncidentBenchmark(
        "network_latency",
        "network",
        "dependency_latency",
        ["p99_increase", "timeout", "packet_loss"],
    ),
]

for benchmark in DEFAULT_BENCHMARKS:
    registry.register(benchmark)
