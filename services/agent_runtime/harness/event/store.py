class EventStore:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event)

    def replay(self, session_id):
        return [
            e for e in self.events
            if e.session_id == session_id
        ]
