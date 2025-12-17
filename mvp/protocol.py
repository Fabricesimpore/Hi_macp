import datetime
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List

# Minimal HI-MACP message schema and helpers for the MVP
MESSAGE_TYPES = {
    "inform",
    "propose",
    "commit",
    "clarify",
    "repair",
    "revise",
    "challenge",
    "confirm",
    "tool_action",  # request to run or simulate an external tool/command
}


def _now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


@dataclass
class Message:
    sender: str
    receiver: str
    type: str
    content: Dict[str, Any]
    confidence: float
    assumptions: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"msg-{uuid.uuid4().hex[:8]}")
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "sender": self.sender,
            "receiver": self.receiver,
            "type": self.type,
            "content": self.content,
            "context": self.context,
            "assumptions": self.assumptions,
            "confidence": round(float(self.confidence), 2),
        }


class ProtocolError(Exception):
    pass


def validate_message(message: Message) -> None:
    if message.type not in MESSAGE_TYPES:
        raise ProtocolError(f"Unsupported message type: {message.type}")
    if not 0.0 <= float(message.confidence) <= 1.0:
        raise ProtocolError(f"Confidence must be between 0 and 1: {message.confidence}")
    if not isinstance(message.content, dict):
        raise ProtocolError("Content must be a dict")
    if not message.sender or not message.receiver:
        raise ProtocolError("Sender and receiver are required")


def grounding_required(message: Message) -> bool:
    """Determine if grounding should be forced for this message."""
    if message.type in {"clarify", "repair"}:
        return True
    if message.type == "propose":
        return bool(message.content.get("plan"))
    return False


def summarize_content(message: Message) -> str:
    """Short human-readable summary for console logging."""
    if message.type == "propose":
        plan = message.content.get("plan", [])
        return f"proposal with {len(plan)} steps"
    if message.type == "revise":
        plan = message.content.get("plan", [])
        return f"revise with {len(plan)} steps"
    if message.type == "commit":
        return f"commitment: {message.content.get('commitment', 'unspecified')}"
    if message.type == "confirm":
        return f"confirm: {message.content}"
    if message.type == "clarify":
        if "issue" in message.content:
            return f"clarify: {message.content.get('issue', '')}"
        return f"clarify: {message.content.get('question', '')}"
    if message.type == "repair":
        return f"repair: {message.content.get('conflict', '')}"
    if message.type == "challenge":
        return f"challenge: {message.content.get('assumption', '')}"
    if message.type == "inform":
        return f"inform: {message.content}"
    if message.type == "tool_action":
        action = message.content.get("action", "action")
        mode = message.content.get("mode", "simulate")
        target = message.content.get("target", "")
        return f"tool_action: {action} ({mode}) {target}"
    return "message"
