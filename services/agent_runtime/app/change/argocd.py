from services.agent_runtime.app.agents.change.models import (
    ChangeInfo,
)


class MockArgoCDProvider:
    """
    Mock ArgoCD deployment history.

    Later replaced by real ArgoCD API client.
    """


    async def get_latest_change(
        self,
        application: str,
    ) -> ChangeInfo:


        return ChangeInfo(

            detected=True,

            application=application,

            change_type="deployment",

            revision="payment-api:v1.2.3",

            message=(
                "New version deployed "
                "10 minutes ago"
            ),

            risk="high",
        )