from pydantic import BaseModel


class ChangeInfo(BaseModel):
    """
    Deployment change information.
    """

    detected: bool

    application: str

    change_type: str

    revision: str

    message: str

    risk: str