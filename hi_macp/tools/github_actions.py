import os
import zipfile
import io
import re
from typing import Dict, Any, List

import httpx


def _headers() -> Dict[str, str]:
    token = os.environ.get("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _ensure_api_response(resp: httpx.Response) -> Dict[str, Any] | None:
    """Guardrail: fail if GitHub returns HTML/redirect instead of JSON API."""
    ctype = resp.headers.get("content-type", "")
    text = resp.text or ""
    if "<html" in text.lower() or "<!doctype" in text.lower():
        return {"status": "transport_error", "details": "HTML detected instead of GitHub API JSON (check token/transport)"}
    if not any(token in ctype for token in ("json", "zip", "octet-stream")) and resp.status_code != 302:
        # Allow 302 for log redirects and zip/octet-stream for logs
        return {"status": "transport_error", "details": f"unexpected content-type '{ctype}' from GitHub API"}
    return None


def list_workflow_runs(owner: str, repo: str, workflow_id: str, branch: str) -> Dict[str, Any]:
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs"
    params = {"branch": branch, "per_page": 1}
    try:
        with httpx.Client(timeout=8) as client:
            resp = client.get(url, headers=_headers(), params=params)
        if resp.status_code == 401:
            return {"status": "unauthorized", "details": "GH_TOKEN missing or invalid"}
        guard = _ensure_api_response(resp)
        if guard:
            return guard
        resp.raise_for_status()
        data = resp.json()
        runs = data.get("workflow_runs") or []
        if not runs:
            return {"status": "not_found", "details": "no runs found"}
        run = runs[0]
        return {
            "status": "success",
            "run_id": run.get("id"),
            "status_text": run.get("status"),
            "conclusion": run.get("conclusion"),
            "html_url": run.get("html_url"),
        }
    except Exception as exc:
        return {"status": "error", "details": str(exc)}


def rerun_workflow_run(owner: str, repo: str, run_id: int) -> Dict[str, Any]:
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/rerun"
    try:
        with httpx.Client(timeout=8) as client:
            resp = client.post(url, headers=_headers())
        if resp.status_code == 401:
            return {"status": "unauthorized", "details": "GH_TOKEN missing or invalid"}
        guard = _ensure_api_response(resp)
        if guard:
            return guard
        if resp.status_code in {201, 202}:
            return {"status": "success", "rerun_triggered": True}
        return {"status": "error", "details": f"status {resp.status_code}: {resp.text}"}
    except Exception as exc:
        return {"status": "error", "details": str(exc)}


def create_pull_request(owner: str, repo: str, head: str, base: str, title: str, body: str | None = None) -> Dict[str, Any]:
    """Create a pull request from head -> base."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    payload = {"head": head, "base": base, "title": title}
    if body:
        payload["body"] = body
    try:
        with httpx.Client(timeout=8) as client:
            resp = client.post(url, headers=_headers(), json=payload)
        if resp.status_code == 401:
            return {"status": "unauthorized", "details": "GH_TOKEN missing or invalid"}
        if resp.status_code == 403 and "Resource not accessible by personal access token" in (resp.text or ""):
            scopes = resp.headers.get("x-oauth-scopes", "")
            return {
                "status": "unauthorized",
                "details": "GH_TOKEN lacks permission to create PRs (403). Create a token with repo scope or enable PR permissions.",
                "oauth_scopes": scopes,
            }
        guard = _ensure_api_response(resp)
        if guard:
            return guard
        if resp.status_code in {201, 202}:
            data = resp.json()
            return {"status": "success", "number": data.get("number"), "url": data.get("html_url")}
        return {"status": "error", "details": f"status {resp.status_code}: {resp.text}"}
    except Exception as exc:
        return {"status": "error", "details": str(exc)}

def merge_pull_request(owner: str, repo: str, number: int, merge_method: str = "squash") -> Dict[str, Any]:
    """Merge a pull request when CI is green."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}/merge"
    payload = {"merge_method": merge_method}
    try:
        with httpx.Client(timeout=8) as client:
            resp = client.put(url, headers=_headers(), json=payload)
        if resp.status_code == 401:
            return {"status": "unauthorized", "details": "GH_TOKEN missing or invalid"}
        guard = _ensure_api_response(resp)
        if guard:
            return guard
        if resp.status_code in {200, 201}:
            data = resp.json()
            return {"status": "success", "merged": data.get("merged"), "sha": data.get("sha")}
        return {"status": "error", "details": f"status {resp.status_code}: {resp.text}"}
    except Exception as exc:
        return {"status": "error", "details": str(exc)}


def get_workflow_logs(owner: str, repo: str, run_id: int) -> Dict[str, Any]:
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/logs"
    try:
        with httpx.Client(timeout=8) as client:
            resp = client.get(url, headers=_headers(), follow_redirects=True)
        if resp.status_code == 401:
            return {"status": "unauthorized", "details": "GH_TOKEN missing or invalid"}
        if resp.status_code == 404:
            return {"status": "not_found", "details": "logs not found"}
        if resp.status_code != 200:
            return {"status": "error", "details": f"status {resp.status_code}: {resp.text}"}
        guard = _ensure_api_response(resp)
        if guard:
            return guard
        content_len = len(resp.content or b"")
        summary: Dict[str, Any] = {"status": "success", "log_bytes": content_len, "run_id": run_id, "details": "logs retrieved (zip binary)"}
        # Attempt to unzip and summarize
        try:
            zfile = zipfile.ZipFile(io.BytesIO(resp.content))
            summary["files"] = zfile.namelist()[:20]
            snippets: List[Dict[str, Any]] = []
            error_lines: List[str] = []
            for name in zfile.namelist()[:5]:  # limit to first few files for safety
                with zfile.open(name) as f:
                    data = f.read().decode("utf-8", errors="ignore")
                lines = data.splitlines()
                head = "\n".join(lines[:20])
                tail = "\n".join(lines[-20:]) if len(lines) > 20 else "\n".join(lines)
                # extract last 200 lines too for CI tail
                tail200 = "\n".join(lines[-200:]) if len(lines) > 200 else "\n".join(lines)
                snippets.append({"file": name, "head": head, "tail": tail, "tail200": tail200})
                # basic error regex
                for line in lines:
                    if re.search(r"(ERROR|FAIL|Traceback)", line):
                        error_lines.append(line.strip())
                if len(error_lines) > 50:
                    break
            summary["snippets"] = snippets
            if error_lines:
                summary["errors"] = error_lines[:50]
        except Exception:
            summary["snippets"] = []
        return summary
    except Exception as exc:
        return {"status": "error", "details": str(exc)}
