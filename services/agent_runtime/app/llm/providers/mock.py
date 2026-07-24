import json

from services.agent_runtime.app.llm.base import (
    BaseLLMProvider,
)

from services.agent_runtime.app.llm.models import (
    ChatRequest,
    ChatResponse,
)


class MockProvider(BaseLLMProvider):


    @property
    def name(
        self,
    ) -> str:

        return "mock"



    async def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:


        prompt = (
            request.user_prompt
            .lower()
        )


        #
        # Healing
        #
        # 注意：
        # 必须放在 RCA 前面
        # 避免 healing prompt
        # 包含 evidence/root cause 时被 RCA 捕获
        #
        if (
            "healing" in prompt
            or
            "remediation" in prompt
            or
            "repair" in prompt
        ):


            result = {


                "action":
                "increase_memory_limit",


                "target":
                "payment-api",


                "risk":
                "medium",


                "reason":
                (
                    "Pod terminated because "
                    "container memory limit exceeded."
                ),


                "approval_required":
                True,

            }



        #
        # RCA
        #
        elif (
            "root cause" in prompt
            or
            "evidence" in prompt
        ):


            #
            # Detect deployment change
            #

            has_change = (

                "deployment_revision_changed"
                in prompt

                or

                "configuration changed"
                in prompt

                or

                "revision abc123"
                in prompt

            )


            if has_change:


                result = {


                    "root_cause":
                    (
                        "Deployment configuration change "
                        "increased memory usage and caused "
                        "container OOMKilled."
                    ),


                    "confidence":
                    0.96,


                    "evidence":[

                        "pod_status=CrashLoopBackOff",

                        "restart_count=15",

                        "reason=OOMKilled",

                        "deployment revision changed",

                        "memory configuration changed",

                    ],
                }


            else:


                result = {


                    "root_cause":
                    (
                        "Container memory limit exceeded "
                        "causing OOMKilled."
                    ),


                    "confidence":
                    0.95,


                    "evidence":[

                        "status=CrashLoopBackOff",

                        "restart_count=15",

                        "last_reason=OOMKilled",

                    ],
                }



        #
        # Noise
        #
        else:


            result = {


                "noise":
                False,


                "confidence":
                0.95,


                "reason":
                "Critical production alert.",

            }



        return ChatResponse(


            content=json.dumps(
                result
            ),


            model="mock-model",


            prompt_tokens=50,


            completion_tokens=30,


            total_tokens=80,

        )