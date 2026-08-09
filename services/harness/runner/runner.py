from services.harness.loader import (
    HarnessCaseLoader,
)

from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)

from services.agent_runtime.app.model.context import (
    AgentContext,
)

from services.agent_runtime.app.investigation.harness_evaluation import (
    build_investigation_harness_evaluation,
)


class HarnessRunner:
    """
    Execute agent runtime against harness cases.

    Flow:

    Case
      |
      v
    Loader
      |
      v
    StandardEvent
      |
      v
    AgentRuntime
      |
      +--> authoritative Pipeline results
      |
      +--> optional Investigation Shadow
      |
      v
    Result + Trace + Evaluation + bounded Shadow evaluation
    """

    def __init__(
        self,
        runtime: AgentRuntime | None = None,
    ) -> None:
        self.runtime = (
            runtime
            or AgentRuntime()
        )

        self.loader = (
            HarnessCaseLoader()
        )

    async def run(
        self,
        case_name: str,
    ) -> dict:
        """
        Execute one Harness case.

        The existing Harness result remains authoritative.

        investigation_evaluation is evaluation-only and cannot influence the
        Pipeline, RCA, Healing, Approval, Action or Verification.
        """

        # ----------------------------------------------------
        # Load Case
        # ----------------------------------------------------

        event = self.loader.load_event(
            case_name
        )

        # ----------------------------------------------------
        # Record existing traces
        # ----------------------------------------------------

        before_trace_ids = {
            trace.trace_id
            for trace
            in self.runtime.tracer.list()
        }

        # ----------------------------------------------------
        # Build Runtime Context
        # ----------------------------------------------------

        context = AgentContext(
            event=event,
            memory=self.runtime.memory,
            tools=self.runtime.tools,
            skills=self.runtime.skills,
            mcp=self.runtime.mcp,

            # Sandbox Runtime
            sandbox=self.runtime.sandbox,
            sandbox_policy=(
                self.runtime.sandbox_policy
            ),

            # Approval Workflow
            approval=self.runtime.approval,
        )

        # ----------------------------------------------------
        # Execute through AgentRuntime boundary
        # ----------------------------------------------------

        results = await self.runtime.execute(
            context
        )

        # ----------------------------------------------------
        # Collect current run traces only
        # ----------------------------------------------------

        all_traces = (
            self.runtime.tracer.list()
        )

        current_traces = [
            trace
            for trace
            in all_traces
            if trace.trace_id
            not in before_trace_ids
        ]

        # ----------------------------------------------------
        # Build bounded Investigation evaluation
        # ----------------------------------------------------

        comparison_snapshot = (
            context.metadata.get(
                "investigation_rca_comparison"
            )
        )

        investigation_evaluation = (
            build_investigation_harness_evaluation(
                comparison_snapshot
            )
        )

        # ----------------------------------------------------
        # Build Harness Result
        # ----------------------------------------------------

        return {
            "case": case_name,
            "success": True,

            "results": [
                result.model_dump()
                for result
                in results
            ],

            "executions": [
                execution.model_dump()
                for execution
                in context.executions
            ],

            "evaluations": [
                evaluation.model_dump()
                for evaluation
                in context.evaluations
            ],

            "traces": [
                trace.model_dump()
                for trace
                in current_traces
            ],

            "investigation_evaluation": (
                investigation_evaluation.model_dump(
                    mode="json"
                )
            ),
        }
