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


class AgentContext(BaseModel):

    model_config = ConfigDict(
        arbitrary_types_allowed=True
    )


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


    memory: BaseMemory | None = None


    tools: ToolManager | None = None