# API migration target: business logic should move here.
class RuntimeFacade:
    def __init__(self, services=None):
        self.services = services or {}

    def investigate(self, request):
        service = self.services.get('investigation')
        if service:
            return service.execute(request)
        return {'status': 'accepted', 'mode': 'delegated'}

    def execute_action(self, request):
        service = self.services.get('action')
        if service:
            return service.execute(request)
        return {'status': 'accepted', 'mode': 'delegated'}
