from typing import Dict, Any

from hi_macp.agents.ci_classifier import CIFailureClassifier
from hi_macp.core.interaction_manager import InteractionManager
from hi_macp.core.protocol import Message


class CICLassifierAgent:
    """Agent that tags CI failure reason/location based on log summaries."""

    def __init__(self, manager: InteractionManager) -> None:
        self.manager = manager
        self.name = "AgentC"
        self.classifier = CIFailureClassifier()

    def classify_and_broadcast(self, log_summary: Dict[str, Any], metrics=None) -> Dict[str, Any]:
        tags = self.classifier.classify(log_summary)
        msg = Message(
            sender=self.name,
            receiver="all",
            type="inform",
            content={"ci_tags": tags},
            confidence=0.7,
            assumptions=["Planner/Monitor will use ci_tags for repair planning"],
            context={"goal": "ci_repair"},
        )
        shared, divergence = self.manager.route(msg, metrics=metrics)
        return shared
