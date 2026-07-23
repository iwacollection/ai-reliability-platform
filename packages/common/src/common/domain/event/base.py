"""
Base classes for domain models.

All domain objects in the AI Reliability Platform should inherit
from DomainModel to ensure consistent validation behavior.
"""

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """
    Base class for all immutable domain models.

    This class defines the common Pydantic configuration used
    across the entire platform.

    Notes
    -----
    - Immutable after creation.
    - Reject unknown fields.
    - Strict validation.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=False,
        populate_by_name=True,
        use_enum_values=True,
    )