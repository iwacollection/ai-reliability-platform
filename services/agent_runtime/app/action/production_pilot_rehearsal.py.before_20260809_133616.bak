from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
)

from services.agent_runtime.app.action.production_pilot import (
    KubernetesProductionPilotControl,
    KubernetesProductionPilotReadinessSnapshot,
)
from services.agent_runtime.app.action.production_pilot_budget_service import (
    ProductionPilotBudgetService,
)


class ProductionPilotRehearsalResult(BaseModel):
    """Bounded proof that the enablement ceremony performed zero writes."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    passed: bool
    zero_write: Literal[True] = True
    pilot_id: str | None
    operator_authorized: bool
    kill_switch_engaged: bool
    budget_state: Literal[
        "unavailable",
        "available",
        "reserved",
        "consumed",
    ]
    durable_claim_created: Literal[False] = False
    external_call_count: Literal[0] = 0
    real_write_attempted: Literal[False] = False
    blockers: tuple[str, ...]
    readiness: KubernetesProductionPilotReadinessSnapshot


class ProductionPilotRehearsalService:
    """
    Run a side-effect-free enablement ceremony check.

    It never creates an Action Execution Claim, reserves a Pilot budget,
    contacts Kubernetes, starts Verification, or mutates workflow state.
    """

    def __init__(
        self,
        *,
        control: KubernetesProductionPilotControl,
        budget_service: ProductionPilotBudgetService | None,
        production_executor_configured: bool,
    ) -> None:
        if not isinstance(
            control,
            KubernetesProductionPilotControl,
        ):
            raise TypeError(
                "Production pilot rehearsal control is invalid"
            )
        if (
            budget_service is not None
            and not isinstance(
                budget_service,
                ProductionPilotBudgetService,
            )
        ):
            raise TypeError(
                "Production pilot rehearsal budget service is invalid"
            )
        self.control = control
        self.budget_service = budget_service
        self.production_executor_configured = bool(
            production_executor_configured
        )

    async def run(
        self,
        *,
        operator_id: str,
    ) -> ProductionPilotRehearsalResult:
        readiness = self.control.snapshot(
            production_executor_configured=(
                self.production_executor_configured
            )
        )
        pilot_id = self.control.config.pilot_id
        operator_authorized = (
            isinstance(operator_id, str)
            and operator_id
            in self.control.config.authorized_operator_ids
        )
        blockers = list(
            readiness.enablement_blockers
        )

        if not operator_authorized:
            blockers.append(
                "operator_not_authorized_for_pilot"
            )
        kill_switch_engaged = (
            readiness.kill_switch.state
            == "engaged"
        )
        if not kill_switch_engaged:
            blockers.append(
                "kill_switch_must_be_engaged_for_rehearsal"
            )

        budget_state: str
        if (
            self.budget_service is None
            or pilot_id is None
        ):
            budget_state = "unavailable"
            blockers.append(
                "pilot_budget_unavailable"
            )
        else:
            budget = await self.budget_service.get(
                pilot_id
            )
            if budget is None:
                budget_state = "available"
            else:
                budget_state = budget.status.value
                blockers.append(
                    f"pilot_budget_{budget_state}"
                )

        return ProductionPilotRehearsalResult(
            passed=not blockers,
            pilot_id=pilot_id,
            operator_authorized=(
                operator_authorized
            ),
            kill_switch_engaged=(
                kill_switch_engaged
            ),
            budget_state=budget_state,
            blockers=tuple(blockers),
            readiness=readiness,
        )


__all__ = [
    "ProductionPilotRehearsalResult",
    "ProductionPilotRehearsalService",
]
