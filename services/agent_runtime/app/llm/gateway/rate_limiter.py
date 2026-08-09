from time import monotonic


class RateLimitExceeded(
    Exception
):
    """
    Raised when rate limit exceeded.
    """



class RateLimiter:
    """
    Simple in-memory rate limiter.

    Algorithm:

    Sliding window.


    Example:

    limit:

    60 requests / minute


    Means:

    Within latest 60 seconds,
    maximum 60 requests allowed.

    """


    def __init__(
        self,
        enabled: bool = True,
        requests_per_minute: int = 60,
    ) -> None:


        self.enabled = enabled


        self.requests_per_minute = (
            requests_per_minute
        )


        self.window_seconds = 60


        self.requests: list[float] = []



    def allow(
        self,
    ) -> bool:
        """
        Check whether request can pass.
        """


        #
        # Disabled
        #

        if not self.enabled:

            return True



        now = monotonic()



        #
        # Remove expired requests
        #

        self.requests = [

            timestamp

            for timestamp in self.requests

            if (
                now - timestamp
            )
            <
            self.window_seconds

        ]



        #
        # Check limit
        #

        if len(
            self.requests
        ) >= self.requests_per_minute:


            return False



        #
        # Record request
        #

        self.requests.append(
            now
        )


        return True



    def check(
        self,
    ) -> None:
        """
        Raise exception when limited.
        """


        if not self.allow():

            raise RateLimitExceeded(

                "LLM request rate limit exceeded."

            )