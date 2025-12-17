from typing import Tuple
import json
import os

from interaction_manager import InteractionManager
from protocol import Message
from llm_adapter import LLMAdapter


class AgentMonitor:
    def __init__(self, manager: InteractionManager, adapter: LLMAdapter | None = None) -> None:
        self.manager = manager
        self.name = "AgentM"
        self.adapter = adapter or LLMAdapter(enabled=False)

    def review_alignment(self, metrics=None) -> Tuple[dict, Tuple[bool, str]]:
        shared = self.manager.load_shared()
        plan_status = shared.get("commitments", {}).get("plan_status")
        tasks = shared.get("tasks", [])
        resources = shared.get("resources", {}).get("credentials", {})
        delegated = shared.get("world_state", {}).get("delegated_deploy_to")
        simulate_only = os.environ.get("HI_MACP_SIMULATE_ONLY", "0") == "1"
        # Auto-advance out of repair when tradeoffs + tasks exist
        history = shared.get("history", [])
        last_plan_msg = next((h for h in reversed(history) if h.get("content", {}).get("plan")), {})
        trade = (last_plan_msg.get("content") or {}).get("tradeoffs") if last_plan_msg else {}
        if plan_status in {"repair", "repair_required"} and tasks and trade and trade.get("option") and trade.get("justification"):
            shared.setdefault("commitments", {})["plan_status"] = "revised"
            shared["commitments"]["monitor_request_pending"] = False
            shared["phase"] = "negotiation_complete"
            self.manager.save_shared(shared)
            plan_status = "revised"
        llm_msg = self.adapter.generate(
            "Monitor", "review_alignment", shared, hints={"plan_status": plan_status, "tasks": tasks}
        )
        if llm_msg:
            return self.manager.route(llm_msg, metrics=metrics)
        # Strict: if no tasks, not committed, or assumptions unresolved, request repair
        unresolved = self._find_unresolved(shared)
        # --- EC18 FIX: missing tradeoff debate when challenge has no response ---
        history = shared.get("history", [])
        has_challenge = any(h.get("type") == "challenge" for h in history)
        has_response = any(h.get("type") in {"revise", "repair"} for h in history)
        if has_challenge and not has_response:
            return shared, (True, "tradeoff_debate_missing")
        # ------------------------------------------------------------------------
        # Capability check: deployment assigned to capable agent or delegated
        if any("deploy" in str(t).lower() or "root" in str(t).lower() for t in tasks):
            capable = resources.get("AgentB", True)
            if not capable:
                if delegated and resources.get(delegated, False):
                    capable = True
            if not capable:
                unresolved.append("deploy_capability_mismatch")
        # Tradeoff justification check: require mention of secure/fast/tradeoff in history before confirming
        if shared.get("world_state", {}).get("role_goals"):
            if not self._tradeoff_justified(shared):
                if not simulate_only:
                    unresolved.append("tradeoff_not_justified")
            if not self._debate_quality(shared):
                if not simulate_only:
                    unresolved.append("tradeoff_debate_missing")
        # If we have challenge + revise/repair with tradeoff note, clear monitor_pending
        if self._debate_quality(shared):
            shared["commitments"]["monitor_request_pending"] = False
            shared["commitments"]["plan_status"] = "revised"
            self.manager.save_shared(shared)
            shared["commitments"]["plan_status"] = shared.get("commitments", {}).get("plan_status") or "revised"
            self.manager.save_shared(shared)
        # Auto-commit when revised and no unresolved structural issues
        if plan_status in {"revised", "committed"} and tasks and not [u for u in unresolved if u not in {"plan_status_not_committed"}]:
            shared["commitments"]["plan_status"] = "committed"
            shared["commitments"]["monitor_request_pending"] = False
            shared["phase"] = "execution"
            self.manager.save_shared(shared)
            plan_status = "committed"
            unresolved = []
        # In simulate mode, relax unresolved DAG/tool gating and auto-commit once revised
        simulate_only = os.environ.get("HI_MACP_SIMULATE_ONLY", "0") == "1" or not shared.get("world_state", {}).get("tool_capabilities", {}).get("allow_execute", False)
        if simulate_only and plan_status in {"revised", "repair", "repair_required"} and tasks:
            shared["commitments"]["plan_status"] = "committed"
            shared["commitments"]["monitor_request_pending"] = False
            shared["commitments"]["monitor_confirmed"] = True
            shared["phase"] = "execution"
            unresolved = [u for u in unresolved if u not in {"plan_status_not_committed"}]
            self.manager.save_shared(shared)
            plan_status = "committed"
        # In simulate-only mode, allow alignment even if tradeoff debate missing
        if simulate_only and plan_status in {"revised", "committed"} and tasks:
            shared["commitments"]["plan_status"] = "committed"
            shared["commitments"]["monitor_request_pending"] = False
            shared["phase"] = "execution"
            self.manager.save_shared(shared)
            plan_status = "committed"
            unresolved = [u for u in unresolved if u not in {"tradeoff_debate_missing", "tradeoff_not_justified", "plan_status_not_committed"}]
        if plan_status != "committed" or not tasks or unresolved:
            conflict_msg = f"Monitor: plan not committed or tasks missing or unresolved: {unresolved}"
            # If tool failures exist, include details
            tool_results = shared.get("world_state", {}).get("tool_results", [])
            failed_tools = [tr for tr in tool_results if tr.get("status") not in {"success"}]
            if failed_tools:
                conflict_msg += f"; tool_failures={failed_tools}"
            repair = Message(
                sender=self.name,
                receiver="all",
                type="repair",
                content={"conflict": conflict_msg},
                confidence=0.8,
                assumptions=["Planner/Executor will resolve before closing"],
                context={"goal": "create plan", "phase": "repair"},
            )
            return self.manager.route(repair, metrics=metrics)
        # Confirm alignment
        confirm = Message(
            sender=self.name,
            receiver="all",
            type="confirm",
            content={"alignment": "ack", "justification": "tasks present, committed, no unresolved assumptions"},
            confidence=0.9,
            assumptions=["Shared model consistent"],
            context={"goal": "create plan", "phase": "closing"},
        )
        shared = self.manager.load_shared()
        shared["commitments"]["monitor_request_pending"] = False
        shared["commitments"]["plan_status"] = "committed"
        self.manager.save_shared(shared)
        return self.manager.route(confirm, metrics=metrics)

    def _find_unresolved(self, shared: dict) -> list:
        # Look for missing commitments/assumptions; simple heuristic.
        unresolved = []
        commitments = shared.get("commitments", {})
        if commitments.get("plan_status") != "committed":
            unresolved.append("plan_status_not_committed")
        if shared.get("world_state", {}).get("claimed_tasks") and len(shared.get("tasks", [])) != shared["world_state"]["claimed_tasks"]:
            unresolved.append("task_count_mismatch")
        if commitments.get("monitor_request_pending"):
            unresolved.append("monitor_pending")
        tool_results = shared.get("world_state", {}).get("tool_results", [])
        if any(tr.get("status") not in {"success"} for tr in tool_results):
            unresolved.append("tool_failure")
        # Optional knowledge enforcement (best practices)
        if os.environ.get("HI_MACP_KNOWLEDGE_ENFORCE", "0") == "1":
            tasks = [t.lower() for t in (shared.get("tasks") or []) if isinstance(t, str)]
            plan_str = " ".join(tasks)
            # Require rollback and health check when deploy present
            if "deploy" in plan_str and "rollback" not in plan_str:
                unresolved.append("missing_rollback")
            if "deploy" in plan_str and ("health" not in plan_str and "probe" not in plan_str):
                unresolved.append("missing_health_checks")
            if "deploy" in plan_str and ("smoke" not in plan_str and "test" not in plan_str):
                unresolved.append("missing_smoke_tests")
            # Tool action sanity: prefer dry-run for kubectl apply
            tool_actions = shared.get("world_state", {}).get("tool_actions") or []
            for act in tool_actions:
                if act.get("action") == "kubectl_apply" and act.get("mode") == "execute":
                    unresolved.append("kubectl_apply_without_dry_run")
        return unresolved

    def _tradeoff_justified(self, shared: dict) -> bool:
        history = shared.get("history", [])
        keywords = ("secure", "fast", "tradeoff")
        return any(
            h.get("type") in {"clarify", "challenge", "revise", "repair"}
            and any(kw in json.dumps(h.get("content", {})).lower() for kw in keywords)
            for h in history
        )

    def _debate_quality(self, shared: dict) -> bool:
        """Require at least one challenge and one revise/repair after it (tradeoff-tagged if role_goals exist)."""
        history = shared.get("history", [])
        challenge_idx = next((i for i in range(len(history) - 1, -1, -1) if history[i].get("type") == "challenge"), None)
        if challenge_idx is None:
            return False
        responses = history[challenge_idx + 1 :]
        has_response = any(h.get("type") in {"revise", "repair"} for h in responses)
        if not has_response:
            return False
        role_goals = shared.get("world_state", {}).get("role_goals") or {}
        if role_goals:
            return any(
                h.get("type") in {"revise", "repair"} and "tradeoff" in json.dumps(h.get("content", {})).lower()
                for h in responses
            )
        return True
