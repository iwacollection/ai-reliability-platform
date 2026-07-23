from pydantic import BaseModel


class NoiseResult(BaseModel):
    is_noise: bool
    score: float
    reason: str