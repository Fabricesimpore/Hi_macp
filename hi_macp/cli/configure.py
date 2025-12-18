import os
import subprocess
from pathlib import Path

import httpx


def check_ssh() -> dict:
    ssh_key = Path.home() / ".ssh" / "id_rsa"
    ssh_key_ed = Path.home() / ".ssh" / "id_ed25519"
    has_ssh = ssh_key.exists() or ssh_key_ed.exists()
    return {"has_ssh": has_ssh, "keys": [p for p in [ssh_key, ssh_key_ed] if p.exists()]}


def check_github_token() -> dict:
    token = os.environ.get("GH_TOKEN")
    if not token:
        return {"ok": False, "reason": "GH_TOKEN missing"}
    try:
        resp = httpx.get("https://api.github.com/user", headers={"Authorization": f"token {token}"}, timeout=6)
        if resp.status_code == 200:
            return {"ok": True, "login": resp.json().get("login")}
        return {"ok": False, "reason": f"GitHub token status {resp.status_code}"}
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "reason": str(exc)}


def configure(repo: str | None = None) -> None:
    """Validate SSH + GitHub token and optionally set git remote to SSH."""
    ssh = check_ssh()
    gh = check_github_token()
    if not ssh["has_ssh"]:
        print("WARN: No SSH key found (~/.ssh/id_rsa or id_ed25519). Run ssh-keygen and add to GitHub.")
    else:
        print(f"SSH keys detected: {', '.join(str(k) for k in ssh['keys'])}")
    if gh["ok"]:
        print(f"GitHub token OK (login={gh.get('login')})")
    else:
        print(f"GitHub token invalid: {gh.get('reason')}")
    if repo and ssh["has_ssh"]:
        try:
            subprocess.run(["git", "remote", "set-url", "origin", f"git@github.com:{repo}.git"], check=True)
            print(f"Set origin to SSH: git@github.com:{repo}.git")
        except Exception as exc:  # pragma: no cover
            print(f"WARN: failed to set remote to SSH: {exc}")
