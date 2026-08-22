"""
Self improvement loop for agent learning.

Updates memory confidence based on human feedback and evaluation results.
"""


class SelfImprovementLoop:
    def update_memory(self, memory, feedback: str):
        if feedback == "correct":
            memory.confidence = min(1.0, memory.confidence + 0.05)
        elif feedback == "incorrect":
            memory.confidence = max(0.0, memory.confidence - 0.1)

        return memory
