from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionType,
)

from services.agent_runtime.app.action.executor import (
    BaseExecutor,
)


class MockExecutor(BaseExecutor):
    """
    Fake remediation executor.

    Simulate Kubernetes / ArgoCD operations.
    """


    async def execute(
        self,
        action: ActionPlan,
    ) -> dict:


        if action.type == ActionType.RESTART_POD:

            return {
                "success": True,
                "mode": "dry_run",
                "action": "restart_pod",
                "resource": "pod",
                "target": action.target,
                "message": (
                    "Pod restart simulated"
                ),
            }



        elif action.type == ActionType.INCREASE_MEMORY_LIMIT:

            return {
                "success": True,
                "mode": "dry_run",
                "action": (
                    "increase_memory_limit"
                ),
                "resource": "deployment",
                "target": action.target,
                "message": (
                    "Deployment resource "
                    "limit update simulated"
                ),
                "next_step": (
                    "ArgoCD sync required"
                ),
            }



        elif action.type == ActionType.ROLLBACK_APPLICATION:

            return {
                "success": True,
                "mode": "dry_run",
                "action": (
                    "rollback_application"
                ),
                "resource": "application",
                "target": action.target,
                "message": (
                    "ArgoCD rollback simulated"
                ),
            }



        elif action.type == ActionType.SCALE_WORKLOAD:

            return {
                "success": True,
                "mode": "dry_run",
                "action": (
                    "scale_workload"
                ),
                "resource": "deployment",
                "target": action.target,
                "message": (
                    "Scaling operation simulated"
                ),
            }



        else:

            return {
                "success": False,
                "message": (
                    "Unsupported action"
                ),
            }