
class PodOOMCase:
    name = "pod_oom_killed"

    def load(self):
        return {
            "signal": "OOMKilled",
            "resource": "pod",
            "expected_evidence": [
                "memory_usage",
                "container_limit",
                "logs"
            ]
        }
