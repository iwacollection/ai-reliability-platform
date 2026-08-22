
class StrategyRegistry:
    def __init__(self):
        self.strategies = []

    def add(self, strategy):
        self.strategies.append(strategy)

    def get_for_incident(self, incident):
        return self.strategies
