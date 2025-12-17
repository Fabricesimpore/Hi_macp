import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import datetime


class ExecutionLog:
    """Structured execution logger (JSON per run)."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = None
        self.run_path: Optional[Path] = None
        self.data: Dict[str, Any] = {}

    def start_run(self, plan: List[Any], dag_batches: List[Any], history: List[Any] | None = None) -> str:
        self.run_id = datetime.datetime.utcnow().strftime("run_%Y%m%dT%H%M%S")
        self.run_path = self.base_dir / f"{self.run_id}.json"
        self.data = {
            "run_id": self.run_id,
            "started_at": datetime.datetime.utcnow().isoformat() + "Z",
            "plan": plan,
            "dag_batches": dag_batches,
            "results": [],
            "failures": [],
            "rollback": [],
            "final_status": "in_progress",
            "history": history or [],
        }
        self._flush()
        return self.run_id

    def record_results(self, results: List[Dict[str, Any]], failures: List[str]) -> None:
        if not self.run_path:
            return
        self.data["results"] = results
        self.data["failures"] = failures
        self._flush()

    def record_rollback(self, rollback_events: List[str]) -> None:
        if not self.run_path:
            return
        self.data["rollback"] = rollback_events
        self._flush()

    def finalize(self, status: str, extra: Dict[str, Any] | None = None) -> None:
        if not self.run_path:
            return
        self.data["final_status"] = status
        self.data["ended_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        if extra:
            self.data.update(extra)
        self._flush()

    def _flush(self) -> None:
        if not self.run_path:
            return
        with self.run_path.open("w") as f:
            json.dump(self.data, f, indent=2)
