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


class LocalProvider(ProviderBase):
    def __init__(self, api_key: Optional[str] = None, max_retries: int = 2, timeout: int = 20):
        self.max_retries = max_retries
        self.timeout = timeout
        self.client = None

    def available(self) -> bool:
        return False  # placeholder

    def capability(self) -> Dict[str, Any]:
        return {
            "supports_json_schema": False,
            "supports_tools": False,
            "max_tokens_hint": None,
            "provider": "local",
        }

    def call(self, prompt: str, schema: Dict[str, Any]) -> Optional[str]:
        return None  # not implemented
