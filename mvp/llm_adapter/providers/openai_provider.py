import json
import os
import subprocess
import importlib.util
from pathlib import Path
from typing import Any, Dict, Optional

# Load ProviderBase without package import
_base_dir = Path(__file__).resolve().parent
_pb_path = _base_dir / "provider_base.py"
ProviderBase = None
if _pb_path.exists():
    spec = importlib.util.spec_from_file_location("provider_base", _pb_path)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore
        ProviderBase = getattr(module, "ProviderBase", None)


class OpenAIProvider(ProviderBase):
    """OpenAI provider using curl to avoid DNS/resolution issues in Python sandbox."""

    def __init__(self, api_key: Optional[str], max_retries: int = 2, timeout: int = 20):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        env_retries = os.environ.get("HI_MACP_LLM_RETRIES")
        env_timeout = os.environ.get("HI_MACP_LLM_TIMEOUT")
        self.max_retries = int(env_retries) if env_retries else max_retries
        self.timeout = int(env_timeout) if env_timeout else timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def capability(self) -> Dict[str, Any]:
        return {
            "supports_json_schema": True,
            "supports_tools": False,
            "max_tokens_hint": 400,
            "provider": "openai",
        }

    def _curl(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.api_key:
            return None
        resolve_ip = os.environ.get("HI_MACP_OPENAI_IP")
        try:
            cmd = [
                    "curl",
                    "-s",
                    "-X",
                    "POST",
                    "https://api.openai.com/v1/chat/completions",
                    "-H",
                    "Content-Type: application/json",
                    "-H",
                    f"Authorization: Bearer {self.api_key}",
                    "-d",
                    json.dumps(payload),
                ]
            if resolve_ip:
                cmd.extend(["--resolve", f"api.openai.com:443:{resolve_ip}"])
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout
            )
            if proc.returncode != 0 or not proc.stdout:
                return None
            return json.loads(proc.stdout)
        except Exception:
            return None

    def call(self, prompt: str, schema: Dict[str, Any]) -> Optional[str]:
        if not self.available():
            return None
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
        payload = {
            "model": os.environ.get("HI_MACP_MODEL", "gpt-4o-mini"),
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 400,
            "response_format": {"type": "json_schema", "json_schema": schema},
        }
        resp = self._curl(payload)
        if not resp:
            return None
        try:
            return resp.get("choices", [{}])[0].get("message", {}).get("content")
        except Exception:
            return None
