import json
from typing import Any, Dict, Optional

from hi_macp.llm.providers.provider_base import ProviderBase


class LocalProvider(ProviderBase):
    """Deterministic local provider used for offline or test runs."""

    def __init__(self, api_key: Optional[str], max_retries: int = 2, timeout: int = 20):
        self.max_retries = max_retries
        self.timeout = timeout

    def available(self) -> bool:
        return True

    def capability(self) -> Dict[str, Any]:
        return {
            "supports_json_schema": False,
            "supports_tools": False,
            "max_tokens_hint": 0,
            "provider": "local",
        }

    def call(self, prompt: str, schema: Dict[str, Any]) -> Optional[str]:
        try:
            return json.dumps(schema.get("example", {}))
        except Exception:
            return None
