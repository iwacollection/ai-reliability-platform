from datetime import UTC, datetime
from enum import Enum
from re import fullmatch
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


_IDENTIFIER_PATTERN = (
    r"[A-Za-z0-9](?:[A-Za-z0-9_.:/-]{0,126}[A-Za-z0-9])?"
)
_SHA256_PATTERN = r"[0-9a-f]{64}"


class ProductionPilotBudgetStatus(str, Enum):
    """Durable lifecycle of the one-write production pilot budget."""

    RESERVED = "reserved"
    CONSUMED = "consumed"


def _aware_utc(
    value: Any,
    *,
    label: str,
) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                f"{label} must be an ISO-8601 datetime"
            ) from exc
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(
            f"{label} must be timezone-aware"
        )
    return value.astimezone(UTC)


class ProductionPilotBudgetRecord(BaseModel):
    """
    Immutable binding for the only production write attempt in one Pilot.

    RESERVED is persisted before the production executor is entered.
    CONSUMED is persisted immediately before the sole real Kubernetes PATCH.
    Neither state is automatically released or reset.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    pilot_id: str = Field(
        min_length=1,
        max_length=128,
    )
    execution_id: UUID
    approval_id: str = Field(
        min_length=1,
        max_length=128,
    )
    contract_id: UUID
    operator_id: str = Field(
        min_length=1,
        max_length=128,
    )
    patch_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    status: ProductionPilotBudgetStatus = (
        ProductionPilotBudgetStatus.RESERVED
    )
    reserved_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    consumed_at: datetime | None = None

    @field_validator(
        "pilot_id",
        "approval_id",
        "operator_id",
        mode="before",
    )
    @classmethod
    def validate_identifier(
        cls,
        value: Any,
    ) -> str:
        if (
            not isinstance(value, str)
            or value != value.strip()
            or fullmatch(
                _IDENTIFIER_PATTERN,
                value,
            )
            is None
        ):
            raise ValueError(
                "Production pilot budget identifier is invalid"
            )
        return value

    @field_validator(
        "patch_sha256",
        mode="before",
    )
    @classmethod
    def validate_digest(
        cls,
        value: Any,
    ) -> str:
        if (
            not isinstance(value, str)
            or fullmatch(
                _SHA256_PATTERN,
                value,
            )
            is None
        ):
            raise ValueError(
                "Production pilot budget digest is invalid"
            )
        return value

    @field_validator(
        "reserved_at",
        "updated_at",
        "consumed_at",
        mode="before",
    )
    @classmethod
    def validate_time(
        cls,
        value: Any,
        info,
    ) -> datetime | None:
        if value is None:
            return None
        return _aware_utc(
            value,
            label=info.field_name,
        )

    @model_validator(mode="after")
    def validate_lifecycle(
        self,
    ) -> "ProductionPilotBudgetRecord":
        if self.updated_at < self.reserved_at:
            raise ValueError(
                "Production pilot budget update time is invalid"
            )
        if (
            self.status
            == ProductionPilotBudgetStatus.RESERVED
        ):
            if self.consumed_at is not None:
                raise ValueError(
                    "Reserved production pilot budget cannot be consumed"
                )
            return self
        if self.consumed_at is None:
            raise ValueError(
                "Consumed production pilot budget requires consumed_at"
            )
        if self.consumed_at < self.reserved_at:
            raise ValueError(
                "Production pilot budget consumption time is invalid"
            )
        if self.updated_at != self.consumed_at:
            raise ValueError(
                "Consumed production pilot budget update time is invalid"
            )
        return self

    def consume(
        self,
        *,
        consumed_at: datetime,
    ) -> "ProductionPilotBudgetRecord":
        checked_at = _aware_utc(
            consumed_at,
            label="consumed_at",
        )
        if (
            self.status
            == ProductionPilotBudgetStatus.CONSUMED
        ):
            return self
        return ProductionPilotBudgetRecord.model_validate(
            {
                **self.model_dump(),
                "status": ProductionPilotBudgetStatus.CONSUMED,
                "updated_at": checked_at,
                "consumed_at": checked_at,
            }
        )


__all__ = [
    "ProductionPilotBudgetRecord",
    "ProductionPilotBudgetStatus",
]
