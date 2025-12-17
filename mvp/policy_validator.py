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
    Policies are hard-coded defaults; can be extended to load from config.
    """

    def __init__(self) -> None:
        # Allow disabling via env flag
        self.enabled = os.environ.get("HI_MACP_POLICY_ENFORCE", "1") == "1"
        self.config = self._load_policy_config()
        self.rules_enabled = {
            "require_dry_run_before_execute": self.config.get("policies", {}).get("require_dry_run_before_execute", True),
            "require_rollback_plan": self.config.get("policies", {}).get("require_rollback_plan", True),
            "require_health_check": self.config.get("policies", {}).get("require_health_check", True),
            "require_smoke_test_for_deploy": self.config.get("policies", {}).get("require_smoke_test_for_deploy", True),
            "require_terraform_plan": self.config.get("policies", {}).get("require_terraform_plan", True),
        }
        self.policies = [
            self._policy_dry_run_before_execute,
            self._policy_require_rollback_for_deploy,
            self._policy_require_health_checks_for_deploy,
            self._policy_require_smoke_for_deploy,
            self._policy_require_terraform_plan,
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
            for act in tool_actions:
                if act.get("action") == "kubectl_apply" and act.get("mode") == "execute":
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
        return issues
