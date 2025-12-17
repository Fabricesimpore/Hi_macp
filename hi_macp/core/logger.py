import json
import sqlite3
from pathlib import Path
from typing import Any, Dict


def log_run(shared: Dict[str, Any], metrics: Any, db_path: str | Path = "logs.db") -> None:
    """Persist run summary (aligned flag, reason, history, metrics) into SQLite."""
    db_path = Path(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            aligned INTEGER,
            reason TEXT,
            metrics TEXT,
            history TEXT
        )
        """
    )
    aligned, reason = False, ""
    if hasattr(metrics, "aligned"):
        aligned = bool(metrics.aligned)
    history = shared.get("history", [])
    cur.execute(
        "INSERT INTO runs (timestamp, aligned, reason, metrics, history) VALUES (?, ?, ?, ?, ?)",
        (
            history[-1].get("timestamp") if history else "",
            1 if aligned else 0,
            reason,
            json.dumps(
                {
                    "clarify": getattr(metrics, "clarify", 0),
                    "challenge": getattr(metrics, "challenge", 0),
                    "repair": getattr(metrics, "repair", 0),
                    "auto_repair": getattr(metrics, "auto_repair", 0),
                    "divergence": getattr(metrics, "divergence", 0),
                    "aligned": getattr(metrics, "aligned", False),
                    "history_count": getattr(metrics, "history_count", 0),
                }
            ),
            json.dumps(history),
        ),
    )
    conn.commit()
    conn.close()
