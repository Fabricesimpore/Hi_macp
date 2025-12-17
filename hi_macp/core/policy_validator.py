from typing import List, Dict, Any, Tuple
import os
import json
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


class PolicyValidator:
    """
    Simple policy engine for structured DevOps steps and tool actions.
    Policies configurable via env/file; rule toggles under policies.* keys.
    """

    def __init__(self) -> None:
        self.enabled = os.environ.get("HI_MACP_POLICY_ENFORCE", "1") == "1"
        self.config = self._load_policy_config()
        self.rules_enabled = {
            "require_dry_run_before_execute": self.config.get("policies", {}).get("require_dry_run_before_execute", True),
            "require_rollback_plan": self.config.get("policies", {}).get("require_rollback_plan", True),
            "require_health_check": self.config.get("policies", {}).get("require_health_check", True),
            "require_smoke_test_for_deploy": self.config.get("policies", {}).get("require_smoke_test_for_deploy", True),
            "require_terraform_plan": self.config.get("policies", {}).get("require_terraform_plan", True),
            "protect_system_namespaces": True,
        }
        self.policies = [
            self._policy_dry_run_before_execute,
            self._policy_require_rollback_for_deploy,
            self._policy_require_health_checks_for_deploy,
            self._policy_require_smoke_for_deploy,
            self._policy_require_terraform_plan,
            self._policy_protect_namespaces,
            self._policy_helm_diff_before_upgrade,
            self._policy_image_tags_explicit,
        ]

    def validate(self, steps: List[Dict[str, Any]], tool_actions: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        if not self.enabled:
            return True, []
        issues = []
        for policy in self.policies:
            issues.extend(policy(steps, tool_actions))
        return (len(issues) == 0), issues

    def _load_policy_config(self) -> Dict[str, Any]:
        path_str = os.environ.get("HI_MACP_POLICY_FILE")
        if not path_str:
            return {}
        path = Path(path_str)
        if not path.exists():
            return {}
        try:
            if yaml and path.suffix in {".yaml", ".yml"}:
                return yaml.safe_load(path.read_text()) or {}
            return json.loads(path.read_text())
        except Exception:
            return {}

    def _policy_dry_run_before_execute(self, steps: List[Dict[str, Any]], tool_actions: List[Dict[str, Any]]) -> List[str]:
        issues = []
        if self.rules_enabled.get("require_dry_run_before_execute", True):
            seen_dry = {a.get("action") for a in tool_actions if a.get("mode") == "dry-run"}
            for act in tool_actions:
                if act.get("action") == "kubectl_apply" and act.get("mode") == "execute" and act.get("action") not in seen_dry:
                    issues.append("kubectl apply should dry-run before execute")
        return issues

    def _policy_require_rollback_for_deploy(self, steps: List[Dict[str, Any]], tool_actions: List[Dict[str, Any]]) -> List[str]:
        issues = []
        if self.rules_enabled.get("require_rollback_plan", True):
            plan_str = " ".join(str(s) for s in steps).lower()
            if "deploy" in plan_str and "rollback" not in plan_str:
                issues.append("missing rollback for deploy")
        return issues

    def _policy_require_health_checks_for_deploy(self, steps: List[Dict[str, Any]], tool_actions: List[Dict[str, Any]]) -> List[str]:
        issues = []
        if self.rules_enabled.get("require_health_check", True):
            plan_str = " ".join(str(s) for s in steps).lower()
            if "deploy" in plan_str and ("health" not in plan_str and "probe" not in plan_str):
                issues.append("missing health checks for deploy")
        return issues

    def _policy_require_smoke_for_deploy(self, steps: List[Dict[str, Any]], tool_actions: List[Dict[str, Any]]) -> List[str]:
        issues = []
        if self.rules_enabled.get("require_smoke_test_for_deploy", True):
            plan_str = " ".join(str(s) for s in steps).lower()
            if "deploy" in plan_str and ("smoke" not in plan_str and "test" not in plan_str):
                issues.append("missing smoke tests for deploy")
        return issues

    def _policy_require_terraform_plan(self, steps: List[Dict[str, Any]], tool_actions: List[Dict[str, Any]]) -> List[str]:
        issues = []
        if self.rules_enabled.get("require_terraform_plan", True):
            plan_str = " ".join(str(s) for s in steps).lower()
            if "infra" in plan_str and "terraform_plan" not in " ".join(str(a) for a in tool_actions):
                issues.append("missing terraform plan for infra changes")
            for act in tool_actions:
                if act.get("action") == "terraform_apply" and not (act.get("extra") or {}).get("plan"):
                    issues.append("terraform apply missing plan file")
        return issues

    def _policy_protect_namespaces(self, steps: List[Dict[str, Any]], tool_actions: List[Dict[str, Any]]) -> List[str]:
        issues = []
        if not self.rules_enabled.get("protect_system_namespaces", True):
            return issues
        ns = os.environ.get("HI_MACP_KUBE_NAMESPACE")
        allow_system = os.environ.get("HI_MACP_KUBE_ALLOW_SYSTEM", "0") == "1"
        if ns and ns in {"default", "kube-system", "kube-public"} and not allow_system:
            issues.append(f"namespace '{ns}' is protected; set HI_MACP_KUBE_ALLOW_SYSTEM=1 to allow")
        return issues

    def _policy_helm_diff_before_upgrade(self, steps: List[Dict[str, Any]], tool_actions: List[Dict[str, Any]]) -> List[str]:
        issues = []
        has_upgrade = any(a.get("action") == "helm_upgrade" for a in tool_actions)
        if has_upgrade and not any(a.get("action") == "helm_diff" for a in tool_actions):
            issues.append("helm_upgrade requires helm_diff first")
        return issues

    def _policy_image_tags_explicit(self, steps: List[Dict[str, Any]], tool_actions: List[Dict[str, Any]]) -> List[str]:
        issues = []
        # Best-effort: look for image references in steps/tool actions content
        text = json.dumps(steps + tool_actions).lower()
        if ":latest" in text or ("image" in text and ":latest" in text):
            issues.append("images must not use :latest; pin explicit tags")
        return issues
