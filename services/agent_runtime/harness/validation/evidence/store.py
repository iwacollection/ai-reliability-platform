class EvidenceStore:
    def __init__(self):
        self.items = []

    def save(self, evidence):
        self.items.append(evidence)

    def query(self):
        return self.items
