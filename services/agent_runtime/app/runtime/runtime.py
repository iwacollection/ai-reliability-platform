from services.agent_runtime.app.registry.factory import (
    create_agent_registry,
)

from services.agent_runtime.app.planner.agent_planner import (
    AgentPlanner,
)

from services.agent_runtime.app.pipeline.planner_pipeline import (
    PlannerPipeline,
)

from services.agent_runtime.app.memory.store import (
    MemoryStore,
)

from services.agent_runtime.app.tools.factory import (
    create_tool_manager,
)

from services.agent_runtime.app.skills.factory import (
    create_skill_registry,
)

from services.agent_runtime.app.observability.collector import (
    TraceCollector,
)

from services.agent_runtime.app.evaluation.factory import (
    create_evaluation_registry,
)



class AgentRuntime:
    """
    Runtime container.
    """


    def __init__(self):

        self.memory = MemoryStore()


        self.tools = create_tool_manager()


        self.skills = create_skill_registry()


        self.tracer = TraceCollector()


        self.evaluators = create_evaluation_registry()


        self.registry = create_agent_registry()


        self.planner = AgentPlanner()


        self.pipeline = PlannerPipeline(
            self.registry,
            self.planner,
            self.tracer,
            self.evaluators,
        )