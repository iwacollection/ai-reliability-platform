from datetime import UTC, datetime

from services.agent_runtime.app.agent.base import (
    BaseAgent,
)
from services.agent_runtime.app.model.context import (
    AgentContext,
)
from services.agent_runtime.app.model.result import (
    AgentResult,
)
from services.agent_runtime.app.observation.manager import (
    ObservationManager,
)
from services.agent_runtime.app.observation.models import (
    ObservationQuery,
)
from services.agent_runtime.app.observation.normalizer import (
    EvidenceNormalizer,
)
from services.agent_runtime.app.observability.models import (
    TraceSpan,
)


class DiagnosisAgent(BaseAgent):
    """
    Collect production evidence.
    """

    @property
    def agent_type(self):
        return "observation"

    @property
    def depends_on(self):
        return [
            "alert_classification"
        ]

    @property
    def provides(self):
        return [
            "kubernetes_evidence"
        ]

    def __init__(
        self,
        observation_manager: ObservationManager,
    ):
        self.observation_manager = (
            observation_manager
        )
        self.normalizer = EvidenceNormalizer()

    @property
    def name(self):
        return "diagnosis"

    async def run(
        self,
        context: AgentContext,
    ) -> AgentResult:
        evidence = []

        event_resource = (
            context.event.resources[0]
        )
        resource = event_resource.name
        namespace = (
            event_resource.namespace
            or "default"
        )
        cluster = event_resource.cluster

        resource_input = {
            "resource": resource,
            "namespace": namespace,
            "cluster": cluster,
        }

        #
        # Prefer Skill
        #
        if context.skills:
            skill = context.skills.get(
                "kubernetes_diagnosis"
            )
            skill_span = None

            #
            # Skill Trace
            #
            if context.trace:
                skill_span = TraceSpan(
                    type="skill",
                    name=skill.name,
                    start_time=datetime.now(
                        UTC
                    ),
                    input_data=dict(
                        resource_input
                    ),
                )
                context.trace.spans.append(
                    skill_span
                )

            try:
                skill_result = await skill.execute(
                    context,
                    dict(resource_input),
                )

                if skill_span:
                    skill_span.end_time = (
                        datetime.now(UTC)
                    )
                    skill_span.duration_ms = (
                        skill_span.end_time
                        - skill_span.start_time
                    ).total_seconds() * 1000
                    skill_span.success = True
                    skill_span.output_data = (
                        skill_result
                    )

                evidence.append(
                    skill_result
                )

            except Exception as exc:
                if skill_span:
                    skill_span.end_time = (
                        datetime.now(UTC)
                    )
                    skill_span.duration_ms = (
                        skill_span.end_time
                        - skill_span.start_time
                    ).total_seconds() * 1000
                    skill_span.success = False
                    skill_span.error = str(exc)

                raise

            #
            # Record current agent skill execution
            #
            skill_calls = (
                context.metadata.setdefault(
                    "current_skill_calls",
                    [],
                )
            )
            skill_calls.append(
                skill.name
            )

        #
        # Fallback old Observation system
        #
        else:
            result = await (
                self.observation_manager.query(
                    ObservationQuery(
                        source="kubernetes",
                        resource=resource,
                        parameters={
                            "namespace": namespace,
                            "cluster": cluster,
                        },
                    )
                )
            )

            normalized = self.normalizer.normalize(
                result.source,
                result.data,
            )
            evidence.append(
                normalized
            )

        context.variables[
            "evidence"
        ] = evidence

        return AgentResult(
            agent=self.name,
            success=True,
            score=1.0,
            message="Evidence collected",
            data={
                "evidence": evidence
            },
        )