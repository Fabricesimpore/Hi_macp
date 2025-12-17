import json
from typing import List, Tuple

from interaction_manager import InteractionManager
from protocol import Message
from llm_adapter import LLMAdapter


class AgentPlanner:
    def __init__(self, manager: InteractionManager, adapter: LLMAdapter | None = None) -> None:
        self.manager = manager
        self.name = "AgentA"
        self.partner = "AgentB"
        self.adapter = adapter or LLMAdapter(enabled=False)
        self.monitor = "AgentM"

    def read_shared(self) -> dict:
        return self.manager.load_shared()

    def generate_plan(self) -> List[str]:
        # Trade-off encoded plan to force negotiation (secure vs fast variants)
        import os

        if os.environ.get("HI_MACP_STRESS", "0") == "1":
            return [
                "Build pipeline: secure=SBOM+signing | fast=skip-sign",
                "Secrets: secure=rotate keys | fast=reuse existing",
                "Deploy strategy: secure=blue/green+canary | fast=direct push",
                "Testing: secure=regression+smoke | fast=smoke-only",
                "Monitoring: secure=deep telemetry | fast=minimal checks",
                "Rollback: secure=2-step rollback | fast=single toggle",
                "Network: secure=policies+TLS | fast=baseline",
                "DNS: secure=validated change | fast=direct update",
                "Post-checks: secure=security audit | fast=skip audit",
                "Key rotation: secure=rotate+verify | fast=defer rotation",
                "Compliance: secure=approval gate | fast=auto-approve",
                "Cutover: secure=gradual | fast=immediate",
            ]
        # Minimal 4-step deploy plan for the MVP demo
        return [
            "Set up hosting",
            "Configure domain",
            "Deploy code",
            "Run smoke tests",
            "Add rollback triggers and playbook",
            "Health checks before traffic shift",
        ]

    def _tool_actions_for_plan(self, plan: List[str], shared: dict) -> List[dict]:
        """Derive tool_action requests from plan keywords, adjusting mode based on capabilities."""
        actions = []
        seen = set()
        caps = shared.get("world_state", {}).get("tool_capabilities", {})
        allow_execute = caps.get("allow_execute", False)
        tools = caps.get("tools", {}) if isinstance(caps, dict) else {}

        def choose_mode(pref_execute: str = "execute", pref_dry: str = "dry-run"):
            if allow_execute:
                return pref_execute
            if pref_dry in {"dry-run", "execute"} and pref_dry != "execute":
                return pref_dry
            return "simulate"

        for step in plan or []:
            s = step.lower() if isinstance(step, str) else json.dumps(step).lower()
            if "test" in s and "smoke_test" not in seen:
                seen.add("smoke_test")
                actions.append({"action": "smoke_test", "mode": choose_mode(pref_dry="simulate"), "target": "run smoke tests"})
            if "deploy" in s and "kubectl_apply" not in seen:
                seen.add("kubectl_apply")
                mode = "dry-run" if tools.get("kubectl") else "simulate"
                mode = choose_mode(pref_dry=mode)
                actions.append({"action": "kubectl_apply", "mode": mode, "target": "manifests/"})
            if "template" in s and "helm_template" not in seen:
                seen.add("helm_template")
                mode = "execute" if tools.get("helm") else "simulate"
                mode = choose_mode(pref_execute=mode, pref_dry="simulate")
                actions.append({"action": "helm_template", "mode": mode, "target": "chart/"})
            if "infra" in s and "terraform_plan" not in seen:
                seen.add("terraform_plan")
                mode = "execute" if tools.get("terraform") else "simulate"
                mode = choose_mode(pref_execute=mode, pref_dry="simulate")
                actions.append({"action": "terraform_plan", "mode": mode, "target": None})
        return actions

    def _structure_plan(self, plan: List[str], shared: dict) -> List[dict]:
        """Convert textual plan steps into structured DevOps actions."""
        structured = []
        tools = shared.get("world_state", {}).get("tool_capabilities", {}).get("tools", {}) if isinstance(shared.get("world_state", {}), dict) else {}

        def pick_mode(tool_key: str, default_execute: str = "execute") -> str:
            if tools and tools.get(tool_key):
                return default_execute
            return "simulate"

        for step in plan or []:
            if isinstance(step, dict) and step.get("type"):
                structured.append(step)
                continue
            text = step if isinstance(step, str) else str(step)
            lower = text.lower()
            item: dict = {"id": text.replace(" ", "_")[:30], "type": "task", "tool": None, "target": None, "mode": "simulate", "requires": [], "policy_tags": []}
            if "deploy" in lower:
                # Only add one deployment task; skip duplicates
                if any(s.get("tool") == "kubectl_apply" for s in structured if isinstance(s, dict)):
                    continue
                item.update({"type": "deployment", "tool": "kubectl_apply", "target": "manifests/", "mode": "dry-run"})
                item["policy_tags"] = ["must_succeed", "must_dry_run_first"]
                item["requires"] = ["helm_template"]
            if "helm" in lower or "template" in lower:
                item.update({"type": "templating", "tool": "helm_template", "target": "chart/", "mode": pick_mode("helm", "execute")})
            if "terraform" in lower or "infra" in lower:
                item.update({"type": "iac_plan", "tool": "terraform_plan", "target": None, "mode": pick_mode("terraform", "execute")})
            if "smoke" in lower or "test" in lower:
                item.update({"type": "test", "tool": "smoke_test", "target": "run smoke tests", "mode": "simulate"})
                item["policy_tags"] = ["must_succeed"]
            structured.append(item)
        return structured

    def send_proposal(self, metrics=None) -> Tuple[dict, Tuple[bool, str], List[str]]:
        # LLM hook
        shared = self.manager.load_shared()
        # Reject invalid existing tasks state
        if "tasks" in shared and not isinstance(shared["tasks"], list):
            raise ValueError("Invalid existing tasks in shared memory; cannot propose.")
        llm_msg = self.adapter.generate(
            "Planner",
            "propose_plan",
            shared,
            hints={
                "goal": "create plan",
                "monitor_pending": shared.get("commitments", {}).get("monitor_request_pending", False),
                "min_plan_len": 4,
                "role_goal": shared.get("world_state", {}).get("role_goals", {}).get("Planner"),
            },
        )
        if llm_msg and self._valid_plan(llm_msg.content.get("plan")):
            plan = self._align_plan(llm_msg.content.get("plan"), shared)
            plan = self._structure_plan(plan, shared)
            message = Message(
                sender=self.name,
                receiver=self.partner,
                type="propose",
                content={
                    "plan": plan,
                    "tradeoff": "secure option selected" if shared.get("world_state", {}).get("role_goals", {}).get("Planner") == "maximize_security" else "fast option selected",
                    "tool_actions": self._tool_actions_for_plan(plan, shared),
                },
                confidence=llm_msg.confidence,
                assumptions=llm_msg.assumptions or ["Executor agrees with plan structure"],
                context={"goal": "create plan"},
            )
        else:
            plan = self.generate_plan()
            if not self._valid_plan(plan):
                raise ValueError("Invalid plan generated.")
            plan = self._align_plan(plan, shared)
            plan = self._structure_plan(plan, shared)
            message = Message(
                sender=self.name,
                receiver=self.partner,
                type="propose",
                content={
                    "plan": plan,
                    "tradeoff": "secure option selected",
                    "tool_actions": self._tool_actions_for_plan(plan, shared),
                },
                confidence=0.79,
                assumptions=["Executor agrees with plan structure"],
                context={"goal": "create plan"},
            )
        shared, divergence = self.manager.route(message, metrics=metrics)
        return shared, divergence, message.content.get("plan", [])

    def respond_to_clarify(self, issue: str, previous_plan: List[str], metrics=None) -> Tuple[dict, Tuple[bool, str], List[str]]:
        shared = self.manager.load_shared()
        llm_msg = self.adapter.generate(
            "Planner",
            "revise_plan",
            shared,
            hints={"issue": issue, "previous_plan": previous_plan, "monitor_pending": shared.get("commitments", {}).get("monitor_request_pending", False)},
        )
        if llm_msg:
            message = llm_msg
            refined_plan = self._align_plan(llm_msg.content.get("plan", previous_plan), shared)
            refined_plan = self._structure_plan(refined_plan, shared)
        else:
            # Failure Mode 1: revise plan with explicit testing detail and rollback checklist.
            refined_plan = [
                "Set up hosting",
                "Configure domain",
                "Deploy code",
                "Run smoke tests with 3 scenarios",
                "Add rollback checklist",
            ]
            aligned_plan = self._align_plan(refined_plan, shared)
            aligned_plan = self._structure_plan(aligned_plan, shared)
            message = Message(
                sender=self.name,
                receiver=self.partner,
                type="revise",
                content={
                    "plan": aligned_plan,
                    "response_to": issue,
                    "tradeoff": "secure option retained" if shared.get("world_state", {}).get("role_goals", {}).get("Planner") == "maximize_security" else "fast option retained",
                    "tool_actions": self._tool_actions_for_plan(aligned_plan, shared),
                },
                confidence=0.73,
                assumptions=["Executor has deployment credentials"],
                context={"goal": "create plan"},
            )
        shared, divergence = self.manager.route(message, metrics=metrics)
        return shared, divergence, message.content.get("plan", previous_plan)

    def respond_to_challenge(self, assumption: str, plan: List[str], metrics=None) -> Tuple[dict, Tuple[bool, str]]:
        shared_state = self.manager.load_shared()
        role_goal = shared_state.get("world_state", {}).get("role_goals", {}).get("Planner")
        # Deterministic compromise for any challenge: always send a revise and mark plan_status revised
        history = shared_state.get("history", [])
        last_type = history[-1].get("type") if history else ""
        if last_type == "challenge":
            compromise = [
                "SBOM+signing",
                "reuse existing secrets",
                "blue/green+canary",
                "smoke-only",
                "baseline telemetry",
                "single toggle rollback",
                "policies+TLS",
                "validated change",
            ]
            aligned = self._align_plan(compromise, shared_state)
            aligned = self._structure_plan(aligned, shared_state)
            shared_state.setdefault("commitments", {})["plan_status"] = "revised"
            self.manager.save_shared(shared_state)
            message = Message(
                sender=self.name,
                receiver=self.partner,
                type="revise",
                content={
                    "plan": aligned,
                    "tradeoff": "compromise: keep SBOM/signing + TLS, relax tests/rollback for speed",
                    "tool_actions": self._tool_actions_for_plan(aligned, shared_state),
                },
                confidence=0.7,
                assumptions=["Executor accepts compromise"],
                context={"goal": "create plan"},
            )
            shared, divergence = self.manager.route(message, metrics=metrics)
            return shared, divergence
        llm_msg = self.adapter.generate(
            "Planner",
            "repair_assumption",
            shared_state,
            hints={"assumption": assumption, "plan": plan, "monitor_pending": shared_state.get("commitments", {}).get("monitor_request_pending", False)},
        )
        if llm_msg:
            message = llm_msg
        else:
            message = Message(
                sender=self.name,
                receiver=self.partner,
                type="repair",
                content={"conflict": f"Assumption rejected: {assumption}", "plan": plan},
                confidence=0.71,
                assumptions=["Executor will provide needed credentials"],
                context={"goal": "create plan"},
            )
        shared, divergence = self.manager.route(message, metrics=metrics)
        return shared, divergence

    def respond_to_monitor(self, issue: str, current_plan: List[str], metrics=None) -> Tuple[dict, Tuple[bool, str], List[str]]:
        """Mandatory response to monitor: send revise/repair addressing monitor issue."""
        shared = self.manager.load_shared()
        llm_msg = self.adapter.generate(
            "Planner",
            "monitor_response",
            shared,
            hints={
                "monitor_issue": issue,
                "plan": current_plan,
                "monitor_pending": True,
                "min_plan_len": 4,
                "role_goal": shared.get("world_state", {}).get("role_goals", {}).get("Planner"),
            },
        )
        if llm_msg and llm_msg.content.get("plan"):
            message = llm_msg
            refined_plan = self._align_plan(llm_msg.content.get("plan", current_plan), shared)
            refined_plan = self._structure_plan(refined_plan, shared)
        else:
            refined_plan = self._align_plan(self._expand_or_rewrite_plan(current_plan), shared)
            refined_plan = self._structure_plan(refined_plan, shared)
            message = Message(
                sender=self.name,
                receiver=self.monitor,
                type="repair",
                content={
                    "conflict": issue or "Monitor requested clarification of plan details.",
                    "plan": refined_plan,
                    "tradeoff": "secure option reiterated" if shared.get("world_state", {}).get("role_goals", {}).get("Planner") == "maximize_security" else "fast option reiterated",
                    "tool_actions": self._tool_actions_for_plan(refined_plan, shared),
                },
                confidence=0.7,
                assumptions=["Monitor concerns addressed"],
                context={"goal": "create plan", "phase": "repair"},
            )
        shared, divergence = self.manager.route(message, metrics=metrics)
        # Clear monitor pending if addressed
        shared["commitments"]["monitor_request_pending"] = False
        self.manager.save_shared(shared)
        return shared, divergence, refined_plan

    def _expand_or_rewrite_plan(self, plan: List[str]) -> List[str]:
        # Deterministically add missing details and DNS/security/resource steps.
        base = plan[:] if isinstance(plan, list) else []
        additions = [
            "Add DNS configuration and validation step",
            "Assign execution ownership and credentials for deployment",
            "Include security key rotation verification",
        ]
        for a in additions:
            if a not in base:
                base.append(a)
        if not base:
            base = [
                "Define architecture",
                "Blue/Green strategy",
                "Health checks",
                "Canary strategy",
                "Rollback conditions",
                "Key rotation",
                "Network policies",
                "Document protocol",
                "DNS configuration and validation",
                "Assign execution ownership/credentials",
            ]
        return base

    def _valid_plan(self, plan: List[str] | None) -> bool:
        if not isinstance(plan, list) or len(plan) < 2:
            return False
        placeholders = {"placeholder"}
        joined = " ".join(plan).lower()
        return not any(ph in joined for ph in placeholders)

    def _align_plan(self, plan: List[str], shared: dict) -> List[str]:
        aligned = list(plan) if isinstance(plan, list) else []
        claimed = shared.get("world_state", {}).get("claimed_tasks")
        role_goal = shared.get("world_state", {}).get("role_goals", {}).get("Planner")
        aligned = self._apply_preference(aligned, role_goal)
        # If capability mismatch, add delegation step
        resources = shared.get("resources", {}).get("credentials", {})
        def _as_text(item):
            if isinstance(item, dict):
                return " ".join(str(v) for v in item.values() if v)
            return str(item)
        if resources and resources.get("AgentB") is False and any("deploy" in _as_text(p).lower() for p in aligned):
            aligned.append("AgentA will perform deployment (delegated)")
            # record delegation in shared memory
            shared.setdefault("world_state", {})["delegated_deploy_to"] = "AgentA"
            self.manager.save_shared(shared)
        if claimed and isinstance(claimed, int):
            if claimed > len(aligned):
                # pad with explicit tasks to match claimed count
                for i in range(len(aligned), claimed):
                    aligned.append(f"Additional coordination task {i+1}")
            elif claimed < len(aligned):
                aligned = aligned[:claimed]
        return aligned

    def _apply_preference(self, plan: List[str], role_goal: str | None) -> List[str]:
        """Choose secure vs fast options encoded as 'secure=... | fast=...'."""
        if not plan:
            return plan
        preferred = []
        for step in plan:
            if not isinstance(step, str):
                preferred.append(step)
                continue
            if "secure=" in step and "fast=" in step:
                secure_part = step.split("secure=", 1)[1].split("|")[0].strip()
                fast_part = step.split("fast=", 1)[1].strip()
                choice = secure_part if role_goal == "maximize_security" else fast_part
                preferred.append(choice)
            else:
                preferred.append(step)
        return preferred
