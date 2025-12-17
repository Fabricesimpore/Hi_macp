import os
from typing import Any, Dict, Optional

from hi_macp.llm.providers.provider_base import ProviderBase

try:
    import anthropic
except Exception:
    anthropic = None


class AnthropicProvider(ProviderBase):
    def __init__(self, api_key: Optional[str], max_retries: int = 2, timeout: int = 20):
        self.api_key = api_key
        self.client = anthropic.Anthropic(api_key=api_key) if (api_key and anthropic) else None
        env_retries = os.environ.get("HI_MACP_LLM_RETRIES")
        env_timeout = os.environ.get("HI_MACP_LLM_TIMEOUT")
        self.max_retries = int(env_retries) if env_retries else max_retries
        self.timeout = int(env_timeout) if env_timeout else timeout

    def available(self) -> bool:
        return self.client is not None

    def capability(self) -> Dict[str, Any]:
        return {
            "supports_json_schema": False,
            "supports_tools": True,
            "max_tokens_hint": 400,
            "provider": "anthropic",
        }

    def call(self, prompt: str, schema: Dict[str, Any]) -> Optional[str]:
        if not self.available():
            return None
        try:
            msg = self.client.messages.create(
                model=os.environ.get("HI_MACP_MODEL", "claude-3-haiku-20240307"),
                max_tokens=400,
                temperature=0.1,
                system="You are an HI-MACP agent. Output ONLY strict JSON.",
                messages=[{"role": "user", "content": prompt}],
                timeout=self.timeout,
            )
            return msg.content[0].text if msg and msg.content else None
        except Exception:
            return None
