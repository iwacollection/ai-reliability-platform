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

from services.agent_runtime.app.mcp.factory import (
    create_mcp_registry,
)

from services.agent_runtime.app.observability.collector import (
    TraceCollector,
)

from services.agent_runtime.app.evaluation.factory import (
    create_evaluation_registry,
)

from services.agent_runtime.app.policy.factory import (
    create_policy_engine,
)

from services.agent_runtime.app.approval.service import (
    ApprovalService,
)



class AgentRuntime:
    """
    Runtime container.
    """


    def __init__(self):

        #
        # Memory
        #
        self.memory = MemoryStore()


        #
        # Tool System
        #
        self.tools = create_tool_manager()


        #
        # Skill System
        #
        self.skills = create_skill_registry()


        #
        # MCP System
        #
        self.mcp = create_mcp_registry()


        #
        # Observability
        #
        self.tracer = TraceCollector()


        #
        # Evaluation
        #
        self.evaluators = create_evaluation_registry()


        #
        # Policy
        #
        self.policy = create_policy_engine()


        #
        # Approval
        #
        self.approval = ApprovalService()


        #
        # Agent System
        #
        self.registry = create_agent_registry()


        self.planner = AgentPlanner()


        #
        # Pipeline
        #
        self.pipeline = PlannerPipeline(

            self.registry,

            self.planner,

            self.tracer,

            self.evaluators,

        )