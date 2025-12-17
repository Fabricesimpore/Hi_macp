"""
LLM adapter for HI-MACP.
Uses provider factory to generate protocol-compliant Messages when enabled.
"""
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from hi_macp.core.memory_manager import MemoryManager
from hi_macp.core.protocol import Message, ProtocolError, validate_message
from hi_macp.llm.provider_factory import ProviderFactory


class LLMAdapter:
    def __init__(self, enabled: bool = False, max_retries: int = 2, timeout: int = 20):
        self.enabled = enabled
        self.api_key = self._load_api_key()
        env_retries = os.environ.get("HI_MACP_LLM_RETRIES")
        env_timeout = os.environ.get("HI_MACP_LLM_TIMEOUT")
        self.max_retries = int(env_retries) if env_retries else max_retries
        self.timeout = int(env_timeout) if env_timeout else timeout
        self.provider = ProviderFactory.from_env(self.api_key, self.max_retries, self.timeout)
        self.memory = MemoryManager()
        self.use_memory_delta = os.environ.get("HI_MACP_MEMORY_DELTA", "0") == "1"

    def _load_api_key(self) -> Optional[str]:
        if "OPENAI_API_KEY" in os.environ:
            return os.environ["OPENAI_API_KEY"]
        key_path = Path("openai.json")
        if key_path.exists():
            try:
                data = json.loads(key_path.read_text())
                return data.get("api_key")
            except Exception:
                return None
        return None

    def generate(self, role: str, intent: str, shared: Dict[str, Any], hints: Dict[str, Any]) -> Optional[Message]:
        if not self.enabled:
            return None
        if not self.provider or not self.provider.available():
            return None

        if self.memory and hints.get("last_message_summary"):
            self.memory.update_last_message(hints["last_message_summary"])

        prompt = self._build_prompt(role, intent, shared, hints)
        for attempt in range(1, self.max_retries + 1):
            try:
                content = self.provider.call(prompt, self._message_schema(intent, hints))
                if not content:
                    raise ProtocolError("Empty LLM response")
                candidate_dict = self._coerce_message_dict(self._parse_json(content), role)
                candidate = Message(**candidate_dict)
                validate_message(candidate)
                return candidate
            except Exception as exc:
                if attempt >= self.max_retries:
                    raise ProtocolError(f"LLM generation failed after {attempt} attempts: {exc}") from exc
        return None

    def _build_prompt(self, role: str, intent: str, shared: Dict[str, Any], hints: Dict[str, Any]) -> str:
        world = shared.get("world_state", {})
        role_goals = world.get("role_goals", {})
        prompt_overrides = world.get("prompt_overrides", {})
        plan_preview = hints.get("plan") or shared.get("tasks", [])
        delta = self.memory.delta(role, shared, hints) if self.use_memory_delta else {}
        return (
            f"Role: {role}. Intent: {intent}.\n"
            f"Goals: {role_goals}.\n"
            f"Plan preview: {plan_preview}.\n"
            f"Assumptions: {hints.get('assumptions') or []}.\n"
            f"Last message: {hints.get('last_message_summary') or world.get('last_message')}.\n"
            f"Delta: {delta}.\n"
            f"Prompt overrides: {prompt_overrides}.\n"
            "Return JSON only."
        )

    def _message_schema(self, intent: str, hints: Dict[str, Any] | None = None) -> Dict[str, Any]:
        hints = hints or {}
        return {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "timestamp": {"type": "string"},
                "sender": {"type": "string"},
                "receiver": {"type": "string"},
                "type": {"type": "string"},
                "content": {"type": "object"},
                "context": {"type": "object"},
                "assumptions": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number"},
            },
            "required": ["id", "timestamp", "sender", "receiver", "type", "content", "context", "assumptions", "confidence"],
            "example": {
                "id": "msg-123",
                "timestamp": "2025-01-01T00:00:00Z",
                "sender": "AgentA",
                "receiver": "AgentB",
                "type": "propose" if intent.startswith("propose") else "inform",
                "content": hints.get("content_example", {}),
                "context": {"goal": "demo"},
                "assumptions": [],
                "confidence": 0.7,
            },
        }

    def _parse_json(self, text: str) -> Dict[str, Any]:
        try:
            return json.loads(text)
        except Exception:
            cleaned = re.sub(r"```json|```", "", text).strip()
            return json.loads(cleaned)

    def _coerce_message_dict(self, data: Dict[str, Any], role: str) -> Dict[str, Any]:
        data.setdefault("id", f"msg-{uuid.uuid4().hex[:8]}")
        data.setdefault("timestamp", "2025-01-01T00:00:00Z")
        data.setdefault("sender", role)
        data.setdefault("receiver", "AgentB" if role == "AgentA" else "AgentA")
        data.setdefault("context", {})
        data.setdefault("assumptions", [])
        data.setdefault("confidence", 0.5)
        return data

    def _enforce_grounding(
        self, candidate_dict: Dict[str, Any], role: str, intent: str, hints: Dict[str, Any], shared: Dict[str, Any]
    ) -> Dict[str, Any]:
        return candidate_dict

    def update_last_message(self, summary: str) -> None:
        if self.memory:
            self.memory.update_last_message(summary)
