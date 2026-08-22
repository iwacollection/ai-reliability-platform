import json

class EvidenceReport:
    def generate(self, path, evidence):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(evidence, f, ensure_ascii=False, indent=2)
