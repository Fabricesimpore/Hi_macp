from typing import Dict, Any, Tuple, List

from hi_macp.core.protocol import Message
from hi_macp.core.interaction_manager import InteractionManager


class AgentKnowledge:
    """Provides and enforces domain best practices via inform/challenge/repair."""

    def __init__(self, manager: InteractionManager) -> None:
        self.manager = manager
        self.name = "AgentK"
        self.partner_planner = "AgentA"
        self.partner_executor = "AgentB"

    def _tips(self) -> Dict[str, List[str]]:
        return {
            "kubernetes": [
                "Use --dry-run for kubectl apply before execute",
                "Prefer declarative manifests; pin image tags",
                "Check rollout status and health probes before traffic shift",
            ],
            "rollback": [
                "Define rollback triggers and playbooks",
                "Keep previous version artifacts available",
                "Monitor error budget during canary",
            ],
            "iac": [
                "Run terraform plan before apply",
                "Validate helm templates before deploy",
                "Store manifests in version control",
            ],
            "testing": [
                "Run smoke tests post-deploy",
                "Use synthetic + real traffic for canary",
            ],
        }

    def inject_best_practices(self, metrics=None) -> Tuple[Dict[str, Any], Tuple[bool, str]]:
        shared = self.manager.load_shared()
        tips = self._tips()
        msg = Message(
            sender=self.name,
            receiver="all",
            type="inform",
            content={"knowledge": tips},
            confidence=0.6,
            assumptions=["Planner/Executor will apply best practices where relevant"],
            context={"goal": shared.get("goals", ["create plan"])[0] if shared.get("goals") else "create plan"},
        )
        return self.manager.route(msg, metrics=metrics)

    def enforce_on_plan(self, plan: List[str] | List[dict], metrics=None) -> Tuple[Dict[str, Any], Tuple[bool, str]]:
        """If plan misses critical practices, issue challenges/repairs."""
        shared = self.manager.load_shared()
        needs = []
        if not plan:
            plan_str = ""
        else:
            normalized = []
            for step in plan:
                if isinstance(step, str):
                    normalized.append(step)
                else:
                    try:
                        import json

                        normalized.append(json.dumps(step))
                    except Exception:
                        normalized.append(str(step))
            plan_str = " ".join(normalized).lower()
        if "deploy" in plan_str and "rollback" not in plan_str:
            needs.append("Add rollback triggers and playbooks")
        if "deploy" in plan_str and "health" not in plan_str and "probe" not in plan_str:
            needs.append("Add health checks before traffic shift")
        if "deploy" in plan_str and "smoke" not in plan_str and "test" not in plan_str:
            needs.append("Run smoke tests post-deploy")
        def _lower(step: object) -> str:
            if isinstance(step, str):
                return step.lower()
            try:
                import json

                return json.dumps(step).lower()
            except Exception:
                return str(step).lower()

        if "terraform" not in plan_str and any("infra" in _lower(p) for p in (plan or [])):
            needs.append("Run terraform plan before apply")
        if "helm" in plan_str and "template" not in plan_str:
            needs.append("Validate helm templates before deploy")
        if not needs:
            return shared, (False, "")
        msg = Message(
            sender=self.name,
            receiver=self.partner_planner,
            type="challenge",
            content={"assumption": f"Best practices missing: {needs}"},
            confidence=0.65,
            assumptions=["Planner will incorporate required steps"],
            context={"goal": shared.get("goals", ["create plan"])[0] if shared.get("goals") else "create plan"},
        )
        return self.manager.route(msg, metrics=metrics)

    def enforce_on_actions(self, metrics=None) -> Tuple[Dict[str, Any], Tuple[bool, str]]:
        """If tool_actions missing dry-runs/templates/tests, issue repair."""
        shared = self.manager.load_shared()
        tool_actions = shared.get("world_state", {}).get("tool_actions") or []
        needs = []
        for act in tool_actions:
            action = act.get("action", "")
            mode = act.get("mode", "")
            if action == "kubectl_apply" and mode == "execute":
                needs.append("kubectl apply should dry-run before execute")
            if action == "terraform_plan" and mode != "execute":
                # plan is inherently a dry-run, so ok
                pass
            if action == "helm_template" and mode not in {"execute", "simulate"}:
                needs.append("helm template should be validated")
            if action == "smoke_test" and mode == "simulate":
                needs.append("smoke tests should eventually run real/CI command")
        if not needs:
            return shared, (False, "")
        msg = Message(
            sender=self.name,
            receiver="all",
            type="repair",
            content={"conflict": f"Tooling best practices missing: {needs}"},
            confidence=0.7,
            assumptions=["Planner/Executor will adjust tool_actions"],
            context={"goal": shared.get("goals", ["create plan"])[0] if shared.get("goals") else "create plan", "phase": "repair"},
        )
        return self.manager.route(msg, metrics=metrics)
