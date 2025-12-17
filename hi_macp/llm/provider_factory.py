import os
from typing import Optional

from hi_macp.llm.providers.anthropic_provider import AnthropicProvider
from hi_macp.llm.providers.local_provider import LocalProvider
from hi_macp.llm.providers.openai_provider import OpenAIProvider


class ProviderFactory:
    @staticmethod
    def from_env(api_key: Optional[str], retries: int, timeout: int):
        name = os.environ.get("HI_MACP_PROVIDER", "openai").lower()
        if name == "openai":
            return OpenAIProvider(api_key=api_key, max_retries=retries, timeout=timeout)
        if name == "anthropic":
            return AnthropicProvider(api_key=api_key, max_retries=retries, timeout=timeout)
        if name == "local":
            return LocalProvider(api_key=api_key, max_retries=retries, timeout=timeout)
        return None
