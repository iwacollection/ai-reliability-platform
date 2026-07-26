from abc import (
    ABC,
    abstractmethod,
)

from typing import Any



class SandboxExecutionResult:
    """
    Sandbox execution result.

    Represents:
    - simulated action result
    - dry-run result
    - validation result
    """


    def __init__(
        self,
        success: bool,
        action: str,
        message: str,
        output: dict[str, Any] | None = None,
    ) -> None:


        self.success = success

        self.action = action

        self.message = message

        self.output = (
            output
            or {}
        )



    def model_dump(
        self,
    ) -> dict[str, Any]:

        return {

            "success": self.success,

            "action": self.action,

            "message": self.message,

            "output": self.output,

        }



class BaseSandboxExecutor(
    ABC
):
    """
    Sandbox executor abstraction.


    Flow:

    HealingAgent

        |

        v

    SandboxExecutor

        |

        v

    ExecutionResult


    """


    @abstractmethod
    async def execute(
        self,
        action: dict[str, Any],
    ) -> SandboxExecutionResult:
        """
        Execute action inside sandbox.
        """

        raise NotImplementedError