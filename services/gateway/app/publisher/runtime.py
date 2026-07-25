from pathlib import Path
import json
import traceback
import uuid

import httpx

from common.domain.event import StandardEvent

from services.gateway.app.publisher.base import (
    EventPublisher,
)


class RuntimePublisher(EventPublisher):
    """
    Publish event to Agent Runtime.
    """


    def __init__(
        self,
        runtime_url: str = "http://127.0.0.1:9000/runtime/execute",
    ) -> None:

        self.runtime_url = runtime_url



    async def publish(
        self,
        event: StandardEvent,
    ) -> dict:
        """
        Send event to Agent Runtime.
        """


        request_id = str(uuid.uuid4())


        payload = event.model_dump(
            mode="json"
        )


        print("=" * 80)

        print("RUNTIME PUBLISH START")

        print("REQUEST ID:")
        print(request_id)


        print("URL:")
        print(self.runtime_url)


        print("PAYLOAD:")
        print(payload)



        # 保存 Gateway 实际发送给 Runtime 的数据
        debug_file = Path(
            "data/gateway_runtime_payload.json"
        )


        debug_file.write_text(
            json.dumps(
                payload,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


        print()

        print("PAYLOAD SAVED:")
        print(debug_file)


        print("=" * 80)



        try:

            async with httpx.AsyncClient(
                timeout=60,
                trust_env=False,
            ) as client:


                response = await client.post(

                    self.runtime_url,

                    json=payload,

                    headers={
                        "X-Request-ID": request_id,
                    },

                )


                print("=" * 80)

                print("RUNTIME RESPONSE")


                print("STATUS:")
                print(response.status_code)


                print("HEADERS:")
                print(dict(response.headers))


                print("BODY:")
                print(response.text)


                print("=" * 80)



                response.raise_for_status()


                return response.json()



        except Exception as exc:


            print("=" * 80)

            print("RUNTIME PUBLISH ERROR")


            print(type(exc))


            print(str(exc))


            traceback.print_exc()


            print("=" * 80)


            raise