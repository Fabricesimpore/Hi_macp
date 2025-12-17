import os
import importlib.util
from pathlib import Path
from typing import Optional

# Dynamically load providers to avoid package shadowing issues
_base_dir = Path(__file__).resolve().parent

def _load_provider(module_name: str):
    path = _base_dir / "providers" / f"{module_name}.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore
        # find any class ending with Provider
        for attr in dir(module):
            if attr.endswith("Provider"):
                return getattr(module, attr)
    return None

OpenAIProvider = _load_provider("openai_provider")
AnthropicProvider = _load_provider("anthropic_provider")
LocalProvider = _load_provider("local_provider")


class ProviderFactory:
    @staticmethod
    def from_env(api_key: Optional[str], retries: int, timeout: int):
        name = os.environ.get("HI_MACP_PROVIDER", "openai").lower()
        if name == "openai" and OpenAIProvider:
            return OpenAIProvider(api_key=api_key, max_retries=retries, timeout=timeout)
        if name == "anthropic" and AnthropicProvider:
            return AnthropicProvider(api_key=api_key, max_retries=retries, timeout=timeout)
        if name == "local" and LocalProvider:
            return LocalProvider(api_key=api_key, max_retries=retries, timeout=timeout)
        return None
