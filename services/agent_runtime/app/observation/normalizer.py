from typing import Any


class EvidenceNormalizer:
    """
    Normalize observation results
    into standard evidence format.
    """


    def normalize(
        self,
        source: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:

        if source == "kubernetes":

            return self._normalize_kubernetes(
                data
            )


        return {

            "source": source,

            "facts": [
                data
            ],

        }



    def _normalize_kubernetes(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any]:


        facts = []


        status = data.get(
            "status"
        )


        if status:

            facts.append(
                f"pod_status={status}"
            )


        containers = data.get(
            "containers",
            []
        )


        for container in containers:

            restart_count = container.get(
                "restart_count"
            )

            reason = container.get(
                "reason"
            )


            if restart_count is not None:

                facts.append(
                    f"restart_count={restart_count}"
                )


            if reason:

                facts.append(
                    f"reason={reason}"
                )



        return {

            "source": "kubernetes",

            "resource": data.get(
                "pod"
            ),

            "namespace": data.get(
                "namespace"
            ),

            "facts": facts,

        }