"""
LLM adapter stub for HI-MACP.
Replace the `generate` method with a real LLM call that returns a protocol-compliant Message.
Uses environment variable OPENAI_API_KEY if enabled. Default is disabled to avoid accidental calls.
"""
import os
import json
import uuid
import re
import importlib.util
from pathlib import Path
from typing import Any, Dict, Optional

from protocol import Message, ProtocolError, validate_message

# Local import of provider module (avoid package shadowing by llm_adapter.py)
ProviderFactory = None
_base_dir = Path(__file__).resolve().parent
_pf_path = _base_dir / "llm_adapter" / "provider_factory.py"
if _pf_path.exists():
    spec = importlib.util.spec_from_file_location("provider_factory", _pf_path)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore
        ProviderFactory = getattr(module, "ProviderFactory", None)

try:
    from memory_manager import MemoryManager
except Exception:
    MemoryManager = None


class LLMAdapter:
    def __init__(self, enabled: bool = False, max_retries: int = 2, timeout: int = 20):
        self.enabled = enabled
        self.api_key = self._load_api_key()
        env_retries = os.environ.get("HI_MACP_LLM_RETRIES")
        env_timeout = os.environ.get("HI_MACP_LLM_TIMEOUT")
        self.max_retries = int(env_retries) if env_retries else max_retries
        self.timeout = int(env_timeout) if env_timeout else timeout
        self.provider = self._select_provider()
        self.memory = MemoryManager() if MemoryManager else None
        self.use_memory_delta = os.environ.get("HI_MACP_MEMORY_DELTA", "0") == "1"

    def _select_provider(self):
        if ProviderFactory:
            return ProviderFactory.from_env(self.api_key, self.max_retries, self.timeout)
        return None

    def generate(self, role: str, intent: str, shared: Dict[str, Any], hints: Dict[str, Any]) -> Optional[Message]:
        """
        role: agent role ("Planner", "Executor", "Monitor")
        intent: high-level action requested (e.g., "propose_plan", "evaluate_plan", "monitor_confirm")
        shared: the current shared memory snapshot
        hints: suggested content to guide generation (plan, issue, question, etc.)

        Return a Message or None to fall back to scripted behavior.
        """
        if not self.enabled:
            return None
        if not self.provider or not self.provider.available():
            return None

        # Track last message summary into memory manager if available
        if self.memory:
            lm = hints.get("last_message_summary") or shared.get("world_state", {}).get("last_message")
            if lm:
                update_last = getattr(self.memory, "update_last_message", None)
                update_from = getattr(self.memory, "update_from_message", None)
                if update_last:
                    update_last(lm)
                elif update_from:
                    try:
                        if isinstance(lm, dict):
                            update_from(json.dumps(lm), lm, shared)
                        else:
                            update_from(str(lm), {"type": "", "sender": ""}, shared)
                    except Exception:
                        pass
        prompt = self._build_prompt(role, intent, shared, hints)
        for attempt in range(1, self.max_retries + 1):
            try:
                content = self.provider.call(prompt, self._message_schema(intent))
                if not content:
                    raise ProtocolError("Empty LLM response")
                candidate_dict = self._coerce_message_dict(self._parse_json(content), role)
                candidate_dict = self._enforce_grounding(candidate_dict, role, intent, hints, shared)
                candidate = Message(**candidate_dict)
                validate_message(candidate)
                return candidate
            except Exception:
                if attempt >= self.max_retries:
                    return None
                # Tighten prompt for retry
                prompt = (
                    "Your previous response had invalid fields. "
                    "Restate the message as strict JSON only. "
                    + prompt
                )

    def _build_prompt(self, role: str, intent: str, shared: Dict[str, Any], hints: Dict[str, Any]) -> str:
        ws = shared.get("world_state", {}) or {}
        tool_actions = ws.get("tool_actions") or []
        tool_results = ws.get("tool_results") or []
        tool_failed = any(tr.get("status") not in {"success"} for tr in tool_results)
        prompt_overrides = ws.get("prompt_overrides", {})
        role_override = prompt_overrides.get(role.lower()) or prompt_overrides.get(role)
        if self.use_memory_delta and self.memory:
            delta = self.memory.delta(role, shared, hints)
            return (
                "Return STRICT JSON only (no markdown, no extra text). Keys: id, timestamp, sender, receiver, type, content, context, assumptions, confidence. "
                "Allowed types: inform, propose, commit, clarify, repair, revise, challenge, confirm, tool_action. "
                "Rules: propose/revise => content.plan list; include tool_actions if steps mention deploy/test/template/infra; "
                "commit => content.commitment + content.plan list; if monitor_pending or tool failures => clarify/challenge/repair (not commit); "
                "Do NOT invent/rename fields; be brief. If tool_actions exist, you may emit type=tool_action to request execution.\n"
                f"Role: {role}; Intent: {intent}; Role_goal: {hints.get('role_goal')}; Role_override: {role_override}; Plan_status: {delta.get('plan_status')}; Monitor_pending: {delta.get('monitor_pending')}; "
                f"Plan_preview: {delta.get('plan_preview')}; Tool_actions: {tool_actions}; Tool_failed: {tool_failed}; Last_message: {delta.get('last_message')}; Changes: {delta.get('changes')}; Hints: {hints}\n"
            )
        else:
            # Minimal delta-style prompt (fallback)
            shared_summary = {
                "goal": (shared.get("goals") or [""])[0] if isinstance(shared.get("goals"), list) else shared.get("goals"),
                "phase": shared.get("phase"),
                "plan_status": shared.get("commitments", {}).get("plan_status"),
                "monitor_pending": shared.get("commitments", {}).get("monitor_request_pending"),
            }
            plan = hints.get("plan") or shared.get("tasks") or []
            plan_preview = plan[:2] if isinstance(plan, list) else plan
            last_message = hints.get("last_message_summary") or shared.get("world_state", {}).get("last_message")

            return (
                "Return STRICT JSON only (no markdown, no extra text). Keys: id, timestamp, sender, receiver, type, content, context, assumptions, confidence. "
                "Allowed types: inform, propose, commit, clarify, repair, revise, challenge, confirm, tool_action. "
                "Rules: propose/revise => content.plan list; include tool_actions if steps mention deploy/test/template/infra; "
                "commit => content.commitment + content.plan list; if monitor_pending or tool failures => clarify/challenge/repair (not commit); "
                "Do NOT invent/rename fields; be brief. If tool_actions exist, you may emit type=tool_action to request execution.\n"
                f"Role: {role}; Intent: {intent}; Role_goal: {hints.get('role_goal')}; Role_override: {role_override}; Goal: {shared_summary.get('goal')}; Plan_status: {shared_summary.get('plan_status')}; Monitor_pending: {shared_summary.get('monitor_pending')}; "
                f"Plan_preview: {plan_preview}; Tool_actions: {tool_actions}; Tool_failed: {tool_failed}; Last_message: {last_message}; Hints: {hints}\n"
            )

    def _call_openai(self, prompt: str):
        """Use OpenAI structured output if available; fallback to basic JSON response format."""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an HI-MACP agent. Output ONLY strict JSON. "
                    "No markdown, no extra text. Keys must be exactly: "
                    "id, timestamp, sender, receiver, type, content, context, assumptions, confidence."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        response_format = "json"
        if self._supports_json_schema():
            response_format = {"type": "json_schema", "json_schema": self._message_schema()}
        return self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.1,
            max_tokens=400,
            timeout=self.timeout,
            response_format=response_format,
        )

    def _supports_json_schema(self) -> bool:
        return True if self.client else False

    def _message_schema(self, intent: str) -> Dict[str, Any]:
        # Minimal per-intent schema; system fills id/timestamp/sender/receiver/confidence if missing.
        base_properties = {
            "type": {
                "type": "string",
                "enum": ["inform", "propose", "commit", "clarify", "repair", "revise", "challenge", "confirm", "tool_action"],
            },
            "content": {"type": "object"},
            "context": {"type": "object"},
            "assumptions": {"type": "array", "items": {"type": "string"}},
        }
        required = ["type", "content"]
        if intent in {"propose", "revise"}:
            base_properties["content"] = {
                "type": "object",
                "properties": {"plan": {"type": "array"}},
                "required": ["plan"],
                "additionalProperties": True,
            }
        if intent == "commit":
            base_properties["content"] = {
                "type": "object",
                "properties": {"plan": {"type": "array"}, "commitment": {"type": "string"}},
                "required": ["plan", "commitment"],
                "additionalProperties": True,
            }
        return {
            "name": "Message",
            "schema": {
                "type": "object",
                "properties": base_properties,
                "required": required,
                "additionalProperties": False,
            },
            "strict": True,
        }

    def _parse_json(self, text: str) -> Dict[str, Any]:
        # Try direct load; if it fails, attempt to extract JSON substring.
        try:
            return json.loads(text)
        except Exception:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start : end + 1])
            raise

    def _coerce_message_dict(self, data: Dict[str, Any], role: str) -> Dict[str, Any]:
        # Ensure required fields exist; fill sane defaults to satisfy schema validation.
        content = data.get("content") or {}
        # Map common LLM fields to plan list
        if "plan" not in content:
            if isinstance(content.get("steps"), list):
                content["plan"] = content["steps"]
            elif isinstance(content.get("text"), str):
                content["plan"] = self._split_lines_to_list(content["text"])
            elif isinstance(content, list):
                content = {"plan": content}
        else:
            # normalize plan if single string
            if isinstance(content.get("plan"), str):
                content["plan"] = self._split_lines_to_list(content["plan"])
        # If commit missing commitment, try to set default for commit type
        if data.get("type") == "commit" and "commitment" not in content:
            content["commitment"] = "accept_plan"

        # System fills ids/timestamps/sender/receiver/confidence if missing
        filled = {
            "id": data.get("id") or f"msg-{uuid.uuid4().hex[:8]}",
            "timestamp": data.get("timestamp") or "",
            "sender": data.get("sender") or role,
            "receiver": data.get("receiver") or ("AgentB" if role == "Planner" else "AgentA"),
            "type": data.get("type") or "inform",
            "content": content,
            "context": data.get("context") or {},
            "assumptions": data.get("assumptions") or [],
            "confidence": data.get("confidence", 0.5),
        }
        # Overlay any provided keys (except we keep our generated ids/sender/receiver if missing)
        for k, v in data.items():
            if k in {"id", "sender", "receiver", "confidence", "timestamp"} and not v:
                continue
            filled[k] = v
        filled["content"] = content
        return filled

    def _enforce_grounding(self, candidate: Dict[str, Any], role: str, intent: str, hints: Dict[str, Any], shared: Dict[str, Any]) -> Dict[str, Any]:
        """Force clarify/challenge instead of premature commit when grounding is required."""
        needs_grounding = hints.get("force_grounding") or (shared.get("commitments", {}).get("plan_status") == "proposed")
        if needs_grounding and candidate.get("type") == "commit":
            issue = hints.get("grounding_issue") or "Need clarification before committing."
            candidate["type"] = "clarify"
            candidate["content"] = {"issue": issue}
            candidate["context"] = {"goal": hints.get("goal") or "create plan"}
        # Guard: ensure propose/revise have non-empty list plan or drop to fallback
        if candidate.get("type") in {"propose", "revise"}:
            plan = candidate.get("content", {}).get("plan")
            min_len = hints.get("min_plan_len", 2)
            if not isinstance(plan, list) or len(plan) < min_len:
                # Mark as invalid so caller can fallback (return None)
                raise ProtocolError("LLM produced propose/revise without valid plan.")
        return candidate

    def _split_lines_to_list(self, text: str) -> list:
        # Split numbered or bullet lists into items; fall back to single-item list.
        parts = re.split(r"\n|\r|\\n|\\r|[0-9]+\.\s+|- ", text)
        items = [p.strip() for p in parts if p.strip()]
        return items or [text.strip()]

    def _load_api_key(self) -> Optional[str]:
        env_key = os.environ.get("OPENAI_API_KEY")
        if env_key:
            return env_key
        # Fallback: try to read ../openai.json relative to this file.
        candidate = Path(__file__).resolve().parent.parent / "openai.json"
        if candidate.exists():
            try:
                with candidate.open() as f:
                    data = json.load(f)
                return data.get("api_key")
            except Exception:
                return None
        return None


def validate_or_raise(message: Message) -> Message:
    validate_message(message)
    return message
