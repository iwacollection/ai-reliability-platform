class IncidentContext:
    def __init__(self, incident_id=None, metadata=None):
        self.incident_id = incident_id
        self.metadata = metadata or {}
