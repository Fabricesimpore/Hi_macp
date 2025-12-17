import os
import shutil
import subprocess
from typing import Dict, Any


class CommandRunner:
    """Safe command runner with simulate/dry-run/execute modes for DevOps actions."""

    DEFAULT_TIMEOUT = 20
    MAX_OUTPUT = 4000

    # Map logical actions to concrete commands (dry-run where possible)
    ACTION_MAP = {
        "kubectl_apply": {"dry-run": ["kubectl", "apply", "-f", "{target}", "--dry-run=client"], "execute": ["kubectl", "apply", "-f", "{target}"]},
        "kubectl_status": {"execute": ["kubectl", "rollout", "status", "{target}"]},
        "helm_template": {"execute": ["helm", "template", "{target}"]},
        "terraform_plan": {"execute": ["terraform", "plan"]},
        "docker_build": {"execute": ["docker", "build", "{target or .}"]},
        "smoke_test": {"execute": ["sh", "-c", "{target}"]},  # target can be a test command
    }

    def __init__(self, allow_execute: bool = False, tool_paths: Dict[str, str] | None = None) -> None:
        self.allow_execute = allow_execute or os.environ.get("HI_MACP_ALLOW_EXECUTE", "0") == "1"
        self.tool_paths = tool_paths or {}

    def run_action(self, action: str, mode: str, target: str | None = None, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """Run or simulate an action and return structured result."""
        extra = extra or {}
        if mode not in {"simulate", "dry-run", "execute"}:
            return self._result(action, mode, target, status="error", details="invalid mode")

        # Simulation path
        if mode == "simulate":
            return self._result(action, mode, target, status="success", details="simulated result (no command run)")

        # Block execute unless allowed
        if mode == "execute" and not self.allow_execute:
            return self._result(action, mode, target, status="unauthorized", details="execution disabled by policy")

        # Resolve command
        template = self.ACTION_MAP.get(action, {}).get(mode) or self.ACTION_MAP.get(action, {}).get("execute")
        if not template:
            return self._result(action, mode, target, status="not_supported", details="unsupported action")

        cmd = [self._subst(arg, target) for arg in template]
        # Use custom tool paths if provided
        custom_path = self.tool_paths.get(cmd[0]) if isinstance(self.tool_paths, dict) else None
        if custom_path:
            cmd[0] = custom_path

        # Ensure binary exists
        if shutil.which(cmd[0]) is None:
            return self._result(action, mode, target, status="not_found", details=f"binary not found: {cmd[0]}")

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.DEFAULT_TIMEOUT)
            output = (proc.stdout or "") + (proc.stderr or "")
            output = output[: self.MAX_OUTPUT]
            status = "success" if proc.returncode == 0 else "failure"
            return self._result(action, mode, target, status=status, details=output.strip())
        except subprocess.TimeoutExpired:
            return self._result(action, mode, target, status="timeout", details="command timed out")
        except Exception as exc:  # pragma: no cover - safety net
            return self._result(action, mode, target, status="error", details=str(exc))

    def capability_profile(self) -> Dict[str, Any]:
        """Report available tools and whether execution is allowed."""
        tools = {}
        for binary in ["kubectl", "helm", "terraform", "docker", "sh"]:
            tools[binary] = bool(shutil.which(self.tool_paths.get(binary, binary)))
        return {"allow_execute": self.allow_execute, "tools": tools}

    def _subst(self, arg: str, target: str | None) -> str:
        if "{target}" in arg:
            return arg.replace("{target}", target or "")
        if "{target or .}" in arg:
            return arg.replace("{target or .}", target or ".")
        return arg

    def _result(self, action: str, mode: str, target: str | None, status: str, details: str) -> Dict[str, Any]:
        return {
            "action": action,
            "mode": mode,
            "target": target,
            "status": status,
            "details": details,
        }
