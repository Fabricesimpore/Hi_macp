from typing import Tuple
import json
import os

from hi_macp.core.interaction_manager import InteractionManager
from hi_macp.core.protocol import Message
from hi_macp.llm.adapter import LLMAdapter
from hi_macp.tools import github_actions


class AgentMonitor:
    def __init__(self, manager: InteractionManager, adapter: LLMAdapter | None = None) -> None:
        self.manager = manager
        self.name = "AgentM"
        self.adapter = adapter or LLMAdapter(enabled=False)

    def _ci_config(self, shared: dict) -> dict:
        gh = (shared.get("world_state") or {}).get("github") or {}
        env_override = {
            "owner": os.environ.get("HI_MACP_GH_OWNER"),
            "repo": os.environ.get("HI_MACP_GH_REPO"),
            "workflow_id": os.environ.get("HI_MACP_GH_WORKFLOW"),
            "branch": os.environ.get("HI_MACP_GH_BRANCH"),
        }
        for k, v in env_override.items():
            if v:
                gh[k] = v
        return gh

    def _check_ci(self, shared: dict) -> tuple[list[str], dict]:
        """Return unresolved issues and ci_state dict."""
        gh = self._ci_config(shared)
        if not gh or not gh.get("owner") or not gh.get("repo") or not gh.get("workflow_id"):
            return [], {}
        branch = gh.get("branch") or "main"
        res = github_actions.list_workflow_runs(gh["owner"], gh["repo"], gh["workflow_id"], branch)
        ci_state = {"status": res.get("status")}
        if res.get("status") == "unauthorized":
            return ["ci_missing_token"], ci_state
        if res.get("status") in {"error"}:
            return [f"ci_error: {res.get('details')}"], ci_state
        if res.get("status") == "not_found":
            return ["ci_run_not_found"], ci_state
        status_text = res.get("status_text")
        conclusion = res.get("conclusion")
        ci_state.update({"status_text": status_text, "conclusion": conclusion, "run_id": res.get("run_id"), "html_url": res.get("html_url")})
        if status_text in {"queued", "in_progress"}:
            return ["ci_running"], ci_state
        if status_text == "completed" and conclusion != "success":
            # Track consecutive failures to flag cache issues
            shared = self.manager.load_shared()
            ws = shared.setdefault("world_state", {})
            ws["ci_failures"] = ws.get("ci_failures", 0) + 1
            self.manager.save_shared(shared)
            return [f"ci_failed:{conclusion}"], ci_state
        return [], ci_state

    def review_alignment(self, metrics=None) -> Tuple[dict, Tuple[bool, str]]:
        shared = self.manager.load_shared()
        plan_status = shared.get("commitments", {}).get("plan_status")
        tasks = shared.get("tasks", [])
        resources = shared.get("resources", {}).get("credentials", {})
        delegated = shared.get("world_state", {}).get("delegated_deploy_to")
        simulate_only = os.environ.get("HI_MACP_SIMULATE_ONLY", "0") == "1" or not shared.get("world_state", {}).get("tool_capabilities", {}).get("allow_execute", False)
        # If plan is stuck in repair but tradeoffs + tasks exist, move to revised
        if plan_status in {"repair", "repair_required"} and tasks:
            last_with_plan = next((h for h in reversed(shared.get("history", [])) if h.get("content", {}).get("plan")), {})
            trade = (last_with_plan.get("content") or {}).get("tradeoffs") if last_with_plan else {}
            if trade and trade.get("option") and trade.get("justification"):
                shared.setdefault("commitments", {})["plan_status"] = "revised"
                shared["commitments"]["monitor_request_pending"] = False
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
        # Require tradeoff presence when role goals exist
        if shared.get("world_state", {}).get("role_goals"):
            trade = (history[-1].get("content") or {}).get("tradeoffs") if history else {}
            if not trade or not trade.get("option") or not trade.get("justification"):
                unresolved.append("missing_tradeoff")
        # Tradeoff justification check: require mention of secure/fast/tradeoff in history before confirming
        if shared.get("world_state", {}).get("role_goals"):
            if not self._tradeoff_justified(shared) and not simulate_only:
                unresolved.append("tradeoff_not_justified")
            if not self._debate_quality(shared) and not simulate_only:
                unresolved.append("tradeoff_debate_missing")
        # If we have challenge + revise/repair with tradeoff note, clear monitor_pending
        if self._debate_quality(shared):
            shared["commitments"]["monitor_request_pending"] = False
            shared["commitments"]["plan_status"] = "revised"
            self.manager.save_shared(shared)
            shared["commitments"]["plan_status"] = shared.get("commitments", {}).get("plan_status") or "revised"
            self.manager.save_shared(shared)
        # If a revise/propose with tradeoffs exists after repair, mark plan ready
        if plan_status in {"repair", "repair_required"}:
            last_rev = next((h for h in reversed(history) if h.get("type") in {"revise", "propose"}), {})
            trade = (last_rev.get("content") or {}).get("tradeoffs") if last_rev else {}
            if trade and trade.get("option") and trade.get("justification") and tasks:
                shared["commitments"]["monitor_request_pending"] = False
                shared["commitments"]["plan_status"] = "revised"
                shared["phase"] = "negotiation_complete"
                self.manager.save_shared(shared)
                plan_status = "revised"
            else:
                # Only emit missing_tradeoff if tradeoffs truly absent
                if not trade or not trade.get("option") or not trade.get("justification"):
                    unresolved.append("missing_tradeoff")
                else:
                    # Tradeoffs exist but plan_status not advanced; avoid infinite repair loop
                    shared["commitments"]["monitor_request_pending"] = False
                    shared["commitments"]["plan_status"] = "revised"
                    shared["phase"] = "negotiation_complete"
                    self.manager.save_shared(shared)
                    plan_status = "revised"
        # Auto-advance to commit-ready when revised and no structural issues
        if plan_status == "revised" and tasks and not [u for u in unresolved if u != "plan_status_not_committed"]:
            shared["commitments"]["plan_status"] = "committed"
            shared["commitments"]["monitor_confirmed"] = True
            shared["commitments"]["monitor_request_pending"] = False
            shared["phase"] = "execution"
            self.manager.save_shared(shared)
            plan_status = "committed"
            unresolved = []
        # In simulate-only mode, relax tradeoff gating and mark aligned when tasks exist
        if simulate_only and tasks:
            shared["commitments"]["plan_status"] = "committed"
            shared["commitments"]["monitor_confirmed"] = True
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
            if not tool_results and shared.get("world_state", {}).get("tool_capabilities", {}).get("allow_execute", False):
                conflict_msg += "; tool_results missing for execute mode"
            # CI check
        ci_unresolved, ci_state = self._check_ci(shared)
        if ci_state:
            shared.setdefault("world_state", {})["ci_state"] = ci_state
            self.manager.save_shared(shared)
        if ci_unresolved:
            conflict_msg += f"; ci={ci_unresolved}"
            # Attempt to fetch CI logs and classify failure when CI failed
            if ci_state.get("run_id") and ci_state.get("status_text") == "completed" and ci_state.get("conclusion") and ci_state.get("conclusion") != "success":
                owner = shared.get("world_state", {}).get("github", {}).get("owner", "") or os.environ.get("HI_MACP_GH_OWNER", "")
                repo = shared.get("world_state", {}).get("github", {}).get("repo", "") or os.environ.get("HI_MACP_GH_REPO", "")
                logs = github_actions.get_workflow_logs(
                    owner,
                    repo,
                    ci_state.get("run_id"),
                )
                if logs.get("status") == "success":
                    shared.setdefault("world_state", {})["ci_logs_summary"] = logs
                    from hi_macp.agents.ci_classifier import CIFailureClassifier

                    tags = CIFailureClassifier().classify(logs)
                    # If repeated failures, flag cache suspect
                    if shared.get("world_state", {}).get("ci_failures", 0) >= 2:
                        tags["ci_cache_suspect"] = True
                    shared["world_state"]["ci_tags"] = tags
                    self.manager.save_shared(shared)
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
        # Only send confirm when phase allows; otherwise just set flags
        if shared.get("phase") not in {"repair", "clarifying"}:
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
        shared = self.manager.load_shared()
        shared["commitments"]["monitor_request_pending"] = False
        shared["commitments"]["plan_status"] = "committed"
        shared["commitments"]["monitor_confirmed"] = True
        self.manager.save_shared(shared)
        return shared, (False, "")

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
        role_goals = shared.get("world_state", {}).get("role_goals") or {}
        # If no challenge, accept a tradeoff-tagged revise/repair as sufficient debate
        if challenge_idx is None:
            return any(
                h.get("type") in {"revise", "repair"}
                and "tradeoff" in json.dumps(h.get("content", {})).lower()
                for h in history
            )
        responses = history[challenge_idx + 1 :]
        has_response = any(h.get("type") in {"revise", "repair"} for h in responses)
        if not has_response:
            return False
        if role_goals:
            return any(
                h.get("type") in {"revise", "repair"} and "tradeoff" in json.dumps(h.get("content", {})).lower()
                for h in responses
            )
        return True
