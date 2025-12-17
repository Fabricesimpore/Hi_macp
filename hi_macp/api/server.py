import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Body, Depends, HTTPException, status, Request
import uvicorn
import time
from collections import defaultdict, deque

from hi_macp.runtime import run as hi_run

app = FastAPI(title="HI-MACP API", version="0.1.0")
API_TOKEN = os.environ.get("HI_MACP_API_TOKEN", "changeme-token")
METRICS = {"runs": 0, "runs_failed": 0}
RATE_WINDOW = 60  # seconds
RATE_LIMIT = 10   # requests per window per client
REQUEST_LOG: defaultdict[str, deque] = defaultdict(deque)


def _logs_dir() -> Path:
    pkg_logs = Path(__file__).resolve().parents[1] / "logs"
    if pkg_logs.exists():
        return pkg_logs
    return Path.cwd() / "logs"


def _latest_log() -> Optional[Path]:
    logs_dir = _logs_dir()
    if not logs_dir.exists():
        return None
    logs = sorted(logs_dir.glob("run_*.json"))
    return logs[-1] if logs else None


def _summarize_log(path: Path) -> dict:
    import json

    data = json.loads(path.read_text())
    failures = data.get("failures") or []
    results = data.get("results") or []
    return {
        "run_id": data.get("run_id"),
        "status": data.get("final_status"),
        "failures": failures,
        "results_count": len(results),
        "started_at": data.get("started_at"),
        "ended_at": data.get("ended_at"),
        "path": str(path),
    }


def _require_token(request: Request):
    if not API_TOKEN:
        return True
    token = request.headers.get("X-API-Token")
    if token != API_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    return True


def _rate_limit(request: Request):
    client = request.client.host if request.client else "unknown"
    now = time.time()
    window = REQUEST_LOG[client]
    while window and now - window[0] > RATE_WINDOW:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate limit exceeded")
    window.append(now)
    return True


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run")
def start_run(
    goal: str = Body(..., embed=True),
    env_name: Optional[str] = Body(None, embed=True),
    llm: bool = Body(False, embed=True),
    monitor: bool = Body(True, embed=True),
    stress: bool = Body(False, embed=True),
    reduced_stress: bool = Body(False, embed=True),
    auth=Depends(_require_token),
    rate=Depends(_rate_limit),
) -> dict:
    os.environ["HI_MACP_GOAL"] = goal
    if env_name:
        os.environ["HI_MACP_ENV_NAME"] = env_name
    os.environ["HI_MACP_LLM_ENABLED"] = "1" if llm else "0"
    os.environ["HI_MACP_MONITOR"] = "1" if monitor else "0"
    os.environ["HI_MACP_STRESS"] = "1" if stress else "0"
    os.environ["HI_MACP_REDUCED_STRESS"] = "1" if reduced_stress else "0"

    hi_run.main()
    METRICS["runs"] += 1

    latest = _latest_log()
    if latest:
        summary = _summarize_log(latest)
        if summary.get("status") and "fail" in str(summary.get("status")).lower():
            METRICS["runs_failed"] += 1
        return {"run": summary}
    METRICS["runs_failed"] += 1
    return {"run": None, "message": "No run log found"}


@app.get("/runs/latest")
def latest_run(auth=Depends(_require_token), rate=Depends(_rate_limit)):
    latest = _latest_log()
    if not latest:
        return {"run": None, "message": "No run log found"}
    return {"run": _summarize_log(latest)}


@app.get("/metrics")
def metrics(auth=Depends(_require_token)):
    lines = [
        "# HELP hi_macp_runs_total Total runs triggered",
        "# TYPE hi_macp_runs_total counter",
        f"hi_macp_runs_total {METRICS['runs']}",
        "# HELP hi_macp_runs_failed_total Total runs missing log or marked failed",
        "# TYPE hi_macp_runs_failed_total counter",
        f"hi_macp_runs_failed_total {METRICS['runs_failed']}",
    ]
    return "\n".join(lines)


def run():
    uvicorn.run("hi_macp.api.server:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
