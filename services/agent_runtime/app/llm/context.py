from contextvars import ContextVar



_current_llm_context: ContextVar[
    dict | None
] = ContextVar(
    "current_llm_context",
    default=None,
)



def set_llm_context(
    metadata: dict,
    trace=None,
) -> None:
    """
    Set current LLM observability context.

    Contains:
    - metadata
    - trace
    """


    _current_llm_context.set(

        {

            "metadata": metadata,

            "trace": trace,

        }

    )



def get_llm_context() -> dict | None:
    """
    Get current LLM observability context.
    """


    return _current_llm_context.get()



def clear_llm_context() -> None:
    """
    Clear current LLM observability context.
    """


    _current_llm_context.set(
        None
    )



#
# Backward compatibility
#
# Existing LLMClient still uses this.
#

def set_llm_metadata(
    metadata: dict,
) -> None:
    """
    Compatibility wrapper.
    """


    set_llm_context(
        metadata
    )



def get_llm_metadata() -> dict | None:
    """
    Compatibility wrapper.
    """


    context = get_llm_context()


    if context is None:

        return None


    return context.get(
        "metadata"
    )



def clear_llm_metadata() -> None:
    """
    Compatibility wrapper.
    """


    clear_llm_context()