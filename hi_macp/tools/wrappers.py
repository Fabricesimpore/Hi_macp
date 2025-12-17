import os
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

from hi_macp.tools.command_runner import CommandRunner


class ToolWrappers:
    """Thin wrappers around common DevOps tools. Respects allow_execute via CommandRunner."""

    def __init__(self, allow_execute: bool = False, tool_paths: Optional[Dict[str, str]] = None) -> None:
        self.runner = CommandRunner(allow_execute=allow_execute, tool_paths=tool_paths)
        self.kube_context = os.environ.get("HI_MACP_KUBE_CONTEXT")
        self.kube_namespace = os.environ.get("HI_MACP_KUBE_NAMESPACE")
        self.kube_allow_system = os.environ.get("HI_MACP_KUBE_ALLOW_SYSTEM", "0") == "1"
        self.require_tf_plan = os.environ.get("HI_MACP_REQUIRE_TF_PLAN", "1") == "1"

    def _guard_kube_context(self) -> Dict[str, Any] | None:
        """Fail fast if kube context/namespace are unsafe or missing."""
        if not self.runner.allow_execute:
            return None
        ctx = self.kube_context
        ns = self.kube_namespace
        deny_namespaces = {"default", "kube-system", "kube-public"}
        try:
            if not ctx:
                proc = subprocess.run(["kubectl", "config", "current-context"], capture_output=True, text=True, timeout=5)
                ctx = (proc.stdout or "").strip()
        except Exception:
            ctx = None
        if not ctx:
            return {"status": "unauthorized", "details": "kube context not set; set HI_MACP_KUBE_CONTEXT or kubectl config use-context"}
        if ns in deny_namespaces and not self.kube_allow_system:
            return {"status": "unauthorized", "details": f"namespace '{ns}' is protected; override with HI_MACP_KUBE_ALLOW_SYSTEM=1"}
        if not ns:
            return {"status": "unauthorized", "details": "namespace not set; set HI_MACP_KUBE_NAMESPACE to proceed in execute mode"}
        return None

    # Kubectl
    def kubectl_apply(self, manifest: str, namespace: Optional[str] = None, dry_run: bool = True) -> Dict[str, Any]:
        guard = self._guard_kube_context()
        if guard:
            guard["action"] = "kubectl"
            guard["mode"] = "execute" if not dry_run else "dry-run"
            guard["target"] = manifest
            return guard
        args = ["apply", "-f", manifest]
        if namespace:
            args.extend(["-n", namespace])
        if dry_run:
            args.extend(["--dry-run=client"])
        result = self.runner.run("kubectl", args)
        # Follow-up rollout status for executes
        if result.get("status") == "success" and not dry_run and self.runner.allow_execute:
            rollout_args = ["rollout", "status", "-f", manifest, "--timeout=60s"]
            if namespace:
                rollout_args.extend(["-n", namespace])
            rollout = self.runner.run("kubectl", rollout_args)
            if rollout.get("status") != "success":
                result["status"] = rollout.get("status")
                result["details"] = f"{result.get('details')}\nrollout: {rollout.get('details')}"
        return result

    def kubectl_rollout_status(self, resource: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        guard = self._guard_kube_context()
        if guard:
            guard["action"] = "kubectl"
            guard["mode"] = "execute"
            guard["target"] = resource
            return guard
        args = ["rollout", "status", resource, "--timeout=60s"]
        if namespace:
            args.extend(["-n", namespace])
        return self.runner.run("kubectl", args)

    def kubectl_get(self, resource: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        args = ["get", resource]
        if namespace:
            args.extend(["-n", namespace])
        return self.runner.run("kubectl", args)

    # Helm
    def helm_template(self, chart: str, values: Optional[str] = None) -> Dict[str, Any]:
        args = ["template", chart]
        if values:
            args.extend(["-f", values])
        return self.runner.run("helm", args)

    def helm_diff(self, release: str, chart: str, namespace: Optional[str] = None, values: Optional[str] = None) -> Dict[str, Any]:
        args = ["upgrade", release, chart, "--dry-run", "--debug"]
        if namespace:
            args.extend(["-n", namespace])
        if values:
            args.extend(["-f", values])
        return self.runner.run("helm", args)

    def helm_upgrade(self, release: str, chart: str, namespace: Optional[str] = None, values: Optional[str] = None, install: bool = True) -> Dict[str, Any]:
        args = ["upgrade", release, chart]
        if install:
            args.append("--install")
        if namespace:
            args.extend(["-n", namespace])
        if values:
            args.extend(["-f", values])
        return self.runner.run("helm", args)

    # Git
    def git_status(self, repo: Optional[str] = None) -> Dict[str, Any]:
        args = ["status", "--short"]
        return self.runner.run("git", args, cwd=repo)

    def git_diff(self, repo: Optional[str] = None) -> Dict[str, Any]:
        args = ["diff", "--stat"]
        return self.runner.run("git", args, cwd=repo)

    def git_commit(self, message: str, repo: Optional[str] = None) -> Dict[str, Any]:
        args = ["commit", "-am", message]
        return self.runner.run("git", args, cwd=repo)

    def git_push(self, branch: str = "main", repo: Optional[str] = None) -> Dict[str, Any]:
        args = ["push", "origin", branch]
        return self.runner.run("git", args, cwd=repo)

    def git_checkout_new_branch(self, branch: str, repo: Optional[str] = None) -> Dict[str, Any]:
        args = ["checkout", "-B", branch]
        return self.runner.run("git", args, cwd=repo)

    # Filesystem
    def fs_read(self, path: str) -> Dict[str, Any]:
        try:
            content = Path(path).read_text()
            return {"status": "success", "details": content}
        except Exception as e:
            return {"status": "failure", "details": str(e)}

    def fs_write(self, path: str, content: str) -> Dict[str, Any]:
        target = Path(path)
        # Enforce allowed patch dirs if configured
        allowed = self.runner.allowed_patch_dirs
        if allowed and not any(str(target.resolve()).startswith(str(Path(a).resolve())) for a in allowed):
            return {"status": "unauthorized", "details": f"path not allowed: {path}"}
        if self.runner.fail_on_missing_path and not target.exists():
            return {"status": "failure", "details": f"path does not exist: {path}"}
        try:
            target.write_text(content)
            return {"status": "success", "details": f"wrote {len(content)} bytes"}
        except Exception as e:
            return {"status": "failure", "details": str(e)}

    # Docker
    def docker_build(self, context: str, tag: str) -> Dict[str, Any]:
        args = ["build", "-t", tag, context]
        return self.runner.run("docker", args)

    def docker_login(self, registry: str, username: Optional[str] = None, password: Optional[str] = None) -> Dict[str, Any]:
        user = username or os.environ.get("DOCKER_USERNAME")
        pw = password or os.environ.get("DOCKER_PASSWORD")
        if not user or not pw:
            return {"action": "docker", "mode": "execute", "target": "login", "status": "unauthorized", "details": "missing DOCKER_USERNAME/DOCKER_PASSWORD"}
        args = ["docker", "login", registry, "-u", user, "--password-stdin"]
        if not self.runner.allow_execute:
            return {"action": "docker", "mode": "simulate", "target": "login", "status": "simulated", "details": "execution disabled by policy"}
        try:
            proc = subprocess.run(args, input=pw, text=True, capture_output=True, timeout=self.runner.DEFAULT_TIMEOUT)
            output = (proc.stdout or "") + (proc.stderr or "")
            status = "success" if proc.returncode == 0 else "failure"
            return {"action": "docker", "mode": "execute", "target": "login", "status": status, "details": output.strip()}
        except Exception as exc:
            return {"action": "docker", "mode": "execute", "target": "login", "status": "error", "details": str(exc)}

    def docker_push(self, tag: str) -> Dict[str, Any]:
        args = ["push", tag]
        return self.runner.run("docker", args)

    def docker_run(self, image: str, cmd: Optional[str] = None) -> Dict[str, Any]:
        args = ["run", "--rm", image]
        if cmd:
            args.append(cmd)
        return self.runner.run("docker", args)

    def health_check(self, url: str, timeout: int = 5) -> Dict[str, Any]:
        cmd = ["curl", "-fk", "--max-time", str(timeout), url]
        return self.runner.run("curl", cmd)

    def smoke_custom(self, cmd: str) -> Dict[str, Any]:
        return self.runner.run("sh", ["-c", cmd])

    # Terraform
    def terraform_init(self, directory: str) -> Dict[str, Any]:
        return self.runner.run("terraform", ["init"], cwd=directory)

    def terraform_plan(self, directory: str, out: Optional[str] = None) -> Dict[str, Any]:
        args = ["plan"]
        if out:
            args.extend(["-out", out])
        return self.runner.run("terraform", args, cwd=directory)

    def terraform_apply(self, directory: str, plan: Optional[str] = None) -> Dict[str, Any]:
        if self.require_tf_plan and not plan:
            return {"action": "terraform", "mode": "execute", "target": "apply", "status": "unauthorized", "details": "plan file required (set HI_MACP_REQUIRE_TF_PLAN=0 to bypass)"}
        args = ["apply", "-auto-approve"]
        if plan:
            args.append(plan)
        return self.runner.run("terraform", args, cwd=directory)
