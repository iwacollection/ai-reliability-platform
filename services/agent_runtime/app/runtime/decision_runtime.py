from services.agent_runtime.app.aggregator.simple import (
    SimpleAggregator,
)


class DecisionRuntime:
    """
    Handle incident decision aggregation.
    """


    def __init__(self):

        self.aggregator = SimpleAggregator()



    def evaluate(
        self,
        results,
    ):

        return self.aggregator.aggregate(
            results
        )