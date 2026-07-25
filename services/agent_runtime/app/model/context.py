from typing import Any

from pydantic import (
    BaseModel,
    Field,
    ConfigDict,
)

from common.domain.event import StandardEvent


from services.agent_runtime.app.memory.base import (
    BaseMemory,
)

from services.agent_runtime.app.incident.state import (
    IncidentState,
)

from services.agent_runtime.app.tools.manager import (
    ToolManager,
)

from services.agent_runtime.app.skills.registry.skill_registry import (
    SkillRegistry,
)

from services.agent_runtime.app.mcp.registry import (
    MCPRegistry,
)

from services.agent_runtime.app.observability.execution import (
    AgentExecutionRecord,
)

from services.agent_runtime.app.observability.models import (
    TraceEvent,
)

from services.agent_runtime.app.evaluation.models import (
    EvaluationResult,
)



class AgentContext(BaseModel):

    model_config = ConfigDict(
        arbitrary_types_allowed=True
    )


    # =========================
    # Request Trace Information
    # =========================

    request_id: str | None = None


    trace: TraceEvent | None = None


    event: StandardEvent



    incident: IncidentState = Field(
        default_factory=IncidentState
    )



    variables: dict[str, Any] = Field(
        default_factory=dict
    )



    results: dict[str, Any] = Field(
        default_factory=dict
    )



    metadata: dict[str, Any] = Field(
        default_factory=dict
    )



    # =========================
    # Runtime Components
    # =========================

    memory: BaseMemory | None = None



    tools: ToolManager | None = None



    skills: SkillRegistry | None = None



    mcp: MCPRegistry | None = None



    # =========================
    # Observability
    # =========================

    executions: list[AgentExecutionRecord] = Field(
        default_factory=list
    )



    evaluations: list[EvaluationResult] = Field(
        default_factory=list
    )