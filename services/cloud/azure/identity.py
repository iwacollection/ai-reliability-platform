from typing import Any


class AzureIdentityProvider:
    """Azure identity boundary.

    Production deployment can inject Azure Workload Identity credentials.
    Agent runtime never stores Azure secrets directly.
    """

    def __init__(self, credential: Any = None):
        self.credential = credential

    def get_token(self, scope: str = "https://management.azure.com/.default"):
        if self.credential is None:
            return {
                "status": "mock",
                "scope": scope,
            }

        token = self.credential.get_token(scope)
        return {
            "access_token": token.token,
            "expires_on": token.expires_on,
        }
