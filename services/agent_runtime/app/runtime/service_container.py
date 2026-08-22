class ServiceContainer:
    """
    Runtime dependency container.
    API should receive services through this object.
    """

    def __init__(
        self,
        investigation=None,
        action=None,
        approval=None,
    ):
        self.investigation = investigation
        self.action = action
        self.approval = approval
