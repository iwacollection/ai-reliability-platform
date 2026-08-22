class ValidationGate:
    def check(self, evidence):
        for item in evidence:
            if item.get("status") != "PASS":
                return "BLOCK"
        return "PASS"
