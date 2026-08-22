import json

class ValidationReportWriter:
    def write(self, path, report):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
