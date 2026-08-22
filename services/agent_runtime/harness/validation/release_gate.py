class ReleaseGate:

    def evaluate(self, validation_result):
        if validation_result.get("gate") == "PASS":
            return {"release_gate": "PASS"}

        return {"release_gate": "BLOCK"}
