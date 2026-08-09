from enum import Enum
from time import monotonic



class CircuitState(str, Enum):
    """
    Circuit breaker states.
    """


    CLOSED = "closed"


    OPEN = "open"


    HALF_OPEN = "half_open"




class CircuitBreakerOpen(
    Exception
):
    """
    Raised when circuit is open.
    """




class CircuitBreaker:
    """
    Simple circuit breaker.

    Protect LLM provider from continuous failures.


    States:

    CLOSED:

        Normal traffic.


    OPEN:

        Reject requests immediately.


    HALF_OPEN:

        Allow probe request.
    """



    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: int = 30,
    ) -> None:


        self.failure_threshold = (
            failure_threshold
        )


        self.recovery_timeout = (
            recovery_timeout
        )


        self.failure_count = 0


        self.state = (
            CircuitState.CLOSED
        )


        self.opened_at: float | None = None




    def allow_request(
        self,
    ) -> bool:
        """
        Check whether request is allowed.
        """


        #
        # Normal state
        #

        if self.state == CircuitState.CLOSED:

            return True



        #
        # Circuit opened
        #

        if self.state == CircuitState.OPEN:


            if self.opened_at is None:

                return False



            elapsed = (
                monotonic()
                -
                self.opened_at
            )


            #
            # Recovery time reached
            #

            if elapsed >= self.recovery_timeout:


                self.state = (
                    CircuitState.HALF_OPEN
                )


                return True



            return False



        #
        # Half open
        #

        return True





    def record_success(
        self,
    ) -> None:
        """
        Record successful request.
        """


        self.failure_count = 0


        self.state = (
            CircuitState.CLOSED
        )


        self.opened_at = None





    def record_failure(
        self,
    ) -> None:
        """
        Record failed request.
        """


        self.failure_count += 1



        if (
            self.failure_count
            >=
            self.failure_threshold
        ):


            self.state = (
                CircuitState.OPEN
            )


            self.opened_at = (
                monotonic()
            )





    def check(
        self,
    ) -> None:
        """
        Raise exception when circuit is open.
        """


        if not self.allow_request():


            raise CircuitBreakerOpen(

                "LLM provider circuit is open."

            )