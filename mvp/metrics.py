from dataclasses import dataclass, field
from typing import Dict


@dataclass
class Metrics:
    clarify: int = 0
    challenge: int = 0
    repair: int = 0
    auto_repair: int = 0
    divergence: int = 0
    aligned: bool = False
    history_count: int = 0
    notes: Dict[str, str] = field(default_factory=dict)

    def record_message(self, mtype: str) -> None:
        if mtype == "clarify":
            self.clarify += 1
        elif mtype == "challenge":
            self.challenge += 1
        elif mtype == "repair":
            self.repair += 1

    def record_auto_repair(self) -> None:
        self.auto_repair += 1

    def record_divergence(self) -> None:
        self.divergence += 1
