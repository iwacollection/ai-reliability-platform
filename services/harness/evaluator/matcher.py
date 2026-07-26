from typing import Any



class HarnessEvaluator:
    """
    Evaluate harness execution result.

    Validate:

    - noise classification
    - diagnosis evidence
    - RCA confidence
    - healing action

    """



    def evaluate(
        self,
        expected: dict,
        result: dict,
    ) -> dict:


        checks = []


        checks.append(
            self._check_noise(
                expected.get(
                    "noise",
                    {}
                ),
                result,
            )
        )


        checks.append(
            self._check_diagnosis(
                expected.get(
                    "diagnosis",
                    {}
                ),
                result,
            )
        )


        checks.append(
            self._check_rca(
                expected.get(
                    "rca",
                    {}
                ),
                result,
            )
        )


        checks.append(
            self._check_healing(
                expected.get(
                    "healing",
                    {}
                ),
                result,
            )
        )



        return {

            "passed":
                all(
                    item["passed"]
                    for item
                    in checks
                ),

            "checks":
                checks,

        }



    def _get_agent_result(
        self,
        result: dict,
        agent: str,
    ) -> dict:


        for item in result.get(
            "results",
            []
        ):

            if item.get(
                "agent"
            ) == agent:

                return item


        return {}



    def _check_noise(
        self,
        expected: dict,
        result: dict,
    ) -> dict:


        actual = self._get_agent_result(
            result,
            "noise",
        )


        data = actual.get(
            "data",
            {},
        )


        expected_real = expected.get(
            "real_alert"
        )


        min_confidence = expected.get(
            "min_confidence",
            0,
        )


        real_alert = not data.get(
            "noise",
            False,
        )


        confidence = actual.get(
            "score",
            0,
        )



        passed = (

            real_alert
            ==
            expected_real

            and

            confidence
            >=
            min_confidence

        )



        return {

            "name":
                "noise",

            "passed":
                passed,

            "real_alert":
                real_alert,

            "confidence":
                confidence,

        }



    def _check_diagnosis(
        self,
        expected: dict,
        result: dict,
    ) -> dict:


        required_spans = expected.get(
            "required_spans",
            []
        )


        actual_spans = []



        for trace in result.get(
            "traces",
            []
        ):

            for span in trace.get(
                "spans",
                []
            ):

                actual_spans.append(
                    span.get(
                        "name"
                    )
                )



        missing = [

            item

            for item

            in required_spans

            if item not in actual_spans

        ]



        return {

            "name":
                "diagnosis",

            "passed":
                len(missing) == 0,

            "missing":
                missing,

            "spans":
                actual_spans,

        }



    def _check_rca(
        self,
        expected: dict,
        result: dict,
    ) -> dict:


        actual = self._get_agent_result(
            result,
            "rca",
        )


        data = actual.get(
            "data",
            {},
        )


        confidence = data.get(
            "confidence",
            actual.get(
                "score",
                0,
            ),
        )


        min_confidence = expected.get(
            "min_confidence",
            0,
        )


        return {

            "name":
                "rca",

            "passed":
                confidence >= min_confidence,

            "confidence":
                confidence,

        }



    def _check_healing(
        self,
        expected: dict,
        result: dict,
    ) -> dict:


        actual = self._get_agent_result(
            result,
            "healing",
        )


        data = actual.get(
            "data",
            {},
        )


        require_action = expected.get(
            "require_action",
            False,
        )


        action_exists = bool(
            data
        )



        return {

            "name":
                "healing",

            "passed":
                (
                    action_exists
                    if require_action
                    else True
                ),

            "action_exists":
                action_exists,

        }