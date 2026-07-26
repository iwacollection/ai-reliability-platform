import json

from pathlib import Path


from common.domain.event import (
    StandardEvent,
)



class HarnessCaseLoader:
    """
    Load harness evaluation cases.

    Responsible for:

    json file
        |
        |
    StandardEvent

    """


    def __init__(
        self,
        base_path: str = "services/harness/cases",
    ) -> None:


        self.base_path = Path(
            base_path
        )



    def load(
        self,
        case_name: str,
    ) -> dict:
        """
        Load raw harness case.
        """


        path = (
            self.base_path
            /
            case_name
        )


        with open(
            path,
            "r",
            encoding="utf-8",
        ) as f:


            return json.load(
                f
            )



    def load_event(
        self,
        case_name: str,
    ) -> StandardEvent:
        """
        Load case input as StandardEvent.
        """


        case = self.load(
            case_name
        )


        return StandardEvent.model_validate(

            case["input"]

        )