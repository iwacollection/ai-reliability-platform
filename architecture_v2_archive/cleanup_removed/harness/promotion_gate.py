
class PromotionGate:
    def check(self, evaluation):
        return {
            "promote": evaluation.get("score", 0) > 80
        }
