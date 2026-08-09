from collections.abc import Mapping
from enum import Enum
from typing import Any

from services.agent_runtime.app.evaluation.assertion.models import (
    AssertionResult,
)


from services.agent_runtime.app.evaluation.scenario.models import (
    ScenarioDefinition,
)



class AssertionEngine:
    """
    Validate scenario expected results.

    Used for:
    - Harness engineering
    - Agent regression testing
    """



    def assert_scenario(
        self,
        scenario: ScenarioDefinition,
        context,
    ) -> AssertionResult:


        checks = []

        passed = True


        expected = scenario.expected



        #
        # Noise assertion
        #

        if "noise" in expected:


            noise_result = context.results.get(
                "noise",
                {},
            )


            noise_data = noise_result.get(
                "data",
                {},
            )


            actual_noise = noise_data.get(
                "noise"
            )


            check_passed = (
                actual_noise
                ==
                expected["noise"]
            )


            checks.append(

                {

                    "name":
                    "noise_check",


                    "expected":
                    expected["noise"],


                    "actual":
                    actual_noise,


                    "passed":
                    check_passed,

                }

            )


            if not check_passed:

                passed = False



        #
        # RCA assertion
        #

        if "root_cause_contains" in expected:


            rca_result = context.results.get(
                "rca",
                {},
            )


            rca_data = rca_result.get(
                "data",
                {},
            )


            root_cause = rca_data.get(
                "root_cause",
                "",
            )


            check_passed = (

                expected[
                    "root_cause_contains"
                ]
                in
                root_cause

            )


            checks.append(

                {

                    "name":
                    "root_cause_check",


                    "expected":
                    expected[
                        "root_cause_contains"
                    ],


                    "actual":
                    root_cause,


                    "passed":
                    check_passed,

                }

            )


            if not check_passed:

                passed = False



        #
        # Healing assertion
        #

        if "healing_action" in expected:


            healing_result = context.results.get(
                "healing",
                {},
            )


            healing_data = healing_result.get(
                "data",
                {},
            )


            action = self._normalize_action(
                healing_data.get(
                    "action"
                )
            )


            check_passed = (

                action
                ==
                expected[
                    "healing_action"
                ]

            )


            checks.append(

                {

                    "name":
                    "healing_action_check",


                    "expected":
                    expected[
                        "healing_action"
                    ],


                    "actual":
                    action,


                    "passed":
                    check_passed,

                }

            )


            if not check_passed:

                passed = False



        return AssertionResult(

            scenario=scenario.name,

            passed=passed,

            checks=checks,

        )


    @classmethod
    def _normalize_action(
        cls,
        action: Any,
    ) -> str | None:
        """
        Convert supported action representations to one comparable value.

        Supported inputs:
        - legacy action strings
        - ActionType or another string Enum
        - ActionPlan-like objects exposing .type
        - nested mappings containing type or action

        Unknown structures remain invalid and therefore fail the assertion.
        """

        if isinstance(
            action,
            Mapping,
        ):
            action = action.get(
                "type",
                action.get(
                    "action"
                ),
            )

        elif hasattr(
            action,
            "type",
        ):
            action = getattr(
                action,
                "type"
            )

        if isinstance(
            action,
            Mapping,
        ):
            return cls._normalize_action(
                action
            )

        if isinstance(
            action,
            Enum,
        ):
            value = action.value

            return (
                value
                if isinstance(
                    value,
                    str,
                )
                else None
            )

        if isinstance(
            action,
            str,
        ):
            return action

        value = getattr(
            action,
            "value",
            None,
        )

        if isinstance(
            value,
            str,
        ):
            return value

        return None
