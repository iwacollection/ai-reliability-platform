
class EvidenceCollector:
    def __init__(self, adapter):
        self.adapter = adapter

    def collect(self, request):
        return self.adapter.collect(request)
