from dataclasses import dataclass


@dataclass
class SLOValidationResult:
    passed: bool
    signals: dict


class SLOValidator:
    def validate(self, metrics: dict) -> SLOValidationResult:
        return SLOValidationResult(
            passed=True,
            signals=metrics,
        )
