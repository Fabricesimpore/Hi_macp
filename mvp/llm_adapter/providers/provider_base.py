from typing import Any, Dict, Optional


class ProviderBase:
    def available(self) -> bool:
        raise NotImplementedError

    def capability(self) -> Dict[str, Any]:
        raise NotImplementedError

    def call(self, prompt: str, schema: Dict[str, Any]) -> Optional[str]:
        raise NotImplementedError
