class InvestigationContext:
    def __init__(self, plan=None, evidence=None):
        self.plan = plan or {}
        self.evidence = evidence or []
