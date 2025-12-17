import json
import os
from pathlib import Path
from typing import Any, Dict, Tuple

from protocol import Message, ProtocolError, grounding_required, summarize_content, validate_message
from metrics import Metrics
from memory_manager import MemoryManager


class InteractionManager:
    def __init__(self, shared_path: str) -> None:
        self.shared_path = Path(shared_path)
        if not self.shared_path.exists():
            raise FileNotFoundError(f"Shared memory not found at {self.shared_path}")
        self.memory = MemoryManager()

    def load_shared(self) -> Dict[str, Any]:
        with self.shared_path.open() as f:
            return json.load(f)

    def save_shared(self, data: Dict[str, Any]) -> None:
        with self.shared_path.open("w") as f:
            json.dump(data, f, indent=2)

    def log_message(self, message: Message) -> None:
        shared = self.load_shared()
        shared.setdefault("history", []).append(message.to_dict())
        summary = summarize_content(message)
        shared["world_state"]["last_message"] = summary
        # Persistent counters for challenges and revisions/repairs
        if message.type == "challenge":
            shared["world_state"]["challenge_count"] = shared.get("world_state", {}).get("challenge_count", 0) + 1
        if message.type in {"revise", "repair"}:
            shared["world_state"]["revise_or_repair_count"] = shared.get("world_state", {}).get("revise_or_repair_count", 0) + 1
        try:
            self.memory.update_from_message(summary, message.to_dict(), shared)
        except Exception:
            pass
        self.save_shared(shared)

    def update_shared_models(self, message: Message) -> None:
        shared = self.load_shared()
        mtype = message.type

        if mtype == "propose":
            shared["tasks"] = message.content.get("plan", [])
            shared["commitments"]["plan_status"] = "proposed"
            shared["commitments"]["proposed_by"] = message.sender
            shared["phase"] = "negotiation"
            if "tool_actions" in message.content:
                shared.setdefault("world_state", {})["tool_actions"] = message.content.get("tool_actions") or []
        elif mtype == "commit":
            shared["commitments"]["plan_status"] = "committed"
            shared["commitments"]["committed_by"] = message.sender
            if "plan" in message.content:
                shared["tasks"] = message.content["plan"]
            shared["phase"] = message.content.get("commitment") == "accept_plan_reconciled" and "closing" or "execution"
        elif mtype == "clarify":
            shared.setdefault("world_state", {}).setdefault("clarifications", []).append(
                message.content.get("question") or message.content.get("issue") or "unspecified question"
            )
            shared["commitments"]["plan_status"] = "clarifying"
            shared["phase"] = "repair"
            if message.sender == "AgentM":
                shared["commitments"]["monitor_request_pending"] = True
                shared["commitments"]["plan_status"] = "repair_required"
        elif mtype == "revise":
            shared["tasks"] = message.content.get("plan", [])
            shared["commitments"]["plan_status"] = "revised"
            shared["commitments"]["revised_by"] = message.sender
            shared["phase"] = "negotiation"
            if "tool_actions" in message.content:
                shared.setdefault("world_state", {})["tool_actions"] = message.content.get("tool_actions") or []
        elif mtype == "repair":
            shared.setdefault("world_state", {}).setdefault("repairs", []).append(
                message.content.get("conflict", "unspecified conflict")
            )
            shared["commitments"]["plan_status"] = "repair"
            shared["phase"] = "repair"
            if message.sender == "AgentM":
                shared["commitments"]["monitor_request_pending"] = True
                shared["commitments"]["plan_status"] = "repair_required"
            if "tool_actions" in message.content:
                shared.setdefault("world_state", {})["tool_actions"] = message.content.get("tool_actions") or []
        elif mtype == "challenge":
            shared.setdefault("world_state", {}).setdefault("challenges", []).append(
                message.content.get("assumption", "unspecified assumption")
            )
            shared["commitments"]["plan_status"] = "repair"
            shared["phase"] = "repair"
        elif mtype == "inform":
            shared["world_state"].update(message.content)
        elif mtype == "confirm":
            shared["commitments"]["monitor_confirmed"] = True
            shared["phase"] = "closing"

        self.save_shared(shared)

    def route(self, message: Message, metrics: Metrics | None = None) -> Tuple[Dict[str, Any], Tuple[bool, str]]:
        try:
            validate_message(message)
            self._extra_validate(message)
        except ProtocolError as exc:
            shared = self._log_validation_failure(message, str(exc))
            if metrics:
                metrics.record_divergence()
            return shared, (True, f"Validation failure: {exc}")
        if metrics:
            metrics.record_message(message.type)

        shared_before = self.load_shared()
        # Role gate enforces which agent can perform which act (e.g., only Planner proposes).
        role_error = self._role_gate(shared_before, message)
        if role_error:
            shared = self._log_validation_failure(message, role_error)
            if metrics:
                metrics.record_divergence()
            return shared, (True, role_error)
        gate_error = self._commit_gate(shared_before, message)
        if gate_error:
            simulate_only = os.environ.get("HI_MACP_SIMULATE_ONLY", "0") == "1"
            if simulate_only and "DAG unresolved dependencies" in gate_error:
                gate_error = None
            else:
                shared = self._log_divergence_and_repair(message, gate_error, metrics)
                return shared, (True, gate_error)

        self.log_message(message)
        self.update_shared_models(message)
        shared = self.load_shared()
        divergence = self.check_divergence(shared, message)
        if divergence[0]:
            shared = self._log_divergence_and_repair(message, divergence[1], metrics)
        return shared, divergence

    def check_divergence(self, shared: Dict[str, Any], message: Message) -> Tuple[bool, str]:
        # Basic divergence detection for MVP: proposed plan without agreement, or conflicting statuses.
        plan_status = shared.get("commitments", {}).get("plan_status")
        tasks = shared.get("tasks", [])
        # corrupted tasks surface as immediate misalignment
        if tasks and not isinstance(tasks, list):
            return True, "Tasks structure corrupted."
        if plan_status == "committed" and not tasks:
            return True, "Commitment recorded but no tasks present."
        # Failure Mode 3: if executor rewrites plan with fewer steps than proposed.
        # Loosen during clarify/repair to allow negotiation convergence.
        history = shared.get("history", [])
        proposed = next((h for h in history if h.get("type") == "propose"), None)
        last_type = history[-1].get("type") if history else ""
        simulate_only = os.environ.get("HI_MACP_SIMULATE_ONLY", "0") == "1"
        if proposed and last_type not in {"clarify", "repair", "revise", "commit", "confirm"} and not simulate_only:
            proposed_steps = len(proposed.get("content", {}).get("plan", []))
            current_steps = len(tasks)
            if current_steps and current_steps < proposed_steps:
                return True, "Task mismatch: executor truncated the plan."
        claimed = shared.get("world_state", {}).get("claimed_tasks")
        if claimed and len(tasks) != claimed:
            return True, f"Task count mismatch: shared tasks={len(tasks)} but claimed_tasks={claimed}."
        # Contradiction flag injected by tests or monitor
        if shared.get("world_state", {}).get("contradiction"):
            return True, "World state contradiction detected."
        # Endless challenge loop detection
        history = shared.get("history", [])
        challenges = [h for h in history if h.get("type") == "challenge"]
        if len(challenges) >= 3 and plan_status != "committed":
            return True, "Endless challenge loop detected."
        # Monitor unable to resolve after repeated attempts
        monitor_attempts = shared.get("world_state", {}).get("monitor_attempts", 0)
        if shared.get("commitments", {}).get("monitor_request_pending") and monitor_attempts >= 2:
            return True, "Monitor unresolved after multiple attempts."
        # Multiple concurrent goals without isolation
        active_goals = shared.get("world_state", {}).get("active_goals")
        if active_goals and isinstance(active_goals, list) and len(active_goals) > 1:
            return True, "Multiple active goals detected without isolation."
        return False, ""

    def alignment_metric(self, monitor_required_env: bool | None = None) -> Tuple[bool, str]:
        """Returns whether the shared mental model is aligned and why."""
        shared = self.load_shared()
        failure_mode = shared.get("failure_mode")
        if failure_mode:
            return False, f"Failure mode set: {failure_mode}"
        plan_status = shared.get("commitments", {}).get("plan_status")
        tasks = shared.get("tasks", [])
        if tasks and not isinstance(tasks, list):
            return False, "Unaligned: tasks structure corrupted"
        if monitor_required_env is None:
            monitor_required = os.environ.get("HI_MACP_MONITOR", "1") == "1"
        else:
            monitor_required = monitor_required_env
        monitor_ok = shared.get("commitments", {}).get("monitor_confirmed", False)
        if not monitor_required:
            monitor_ok = True
        if plan_status == "committed" and tasks and monitor_ok:
            return True, "Shared mental model aligned on committed plan" + (" with monitor confirmation." if monitor_required else ".")
        return False, f"Unaligned: plan_status={plan_status}, tasks_count={len(tasks)}, monitor={monitor_ok}"

    # ---------------- Private helpers ----------------
    def _extra_validate(self, message: Message) -> None:
        """Stricter semantic checks for plan/commit content."""
        mtype = message.type
        content = message.content or {}
        if mtype in {"propose", "revise"}:
            plan = content.get("plan") or content.get("steps")
            if not isinstance(plan, list) or not plan:
                raise ProtocolError("Propose/Revise requires non-empty list plan.")
        if mtype == "commit":
            if "commitment" not in content:
                raise ProtocolError("Commit requires content.commitment.")
            plan = content.get("plan")
            if not isinstance(plan, list) or not plan:
                raise ProtocolError("Commit requires non-empty content.plan.")
        if mtype == "clarify":
            issue = content.get("question") or content.get("issue")
            if issue is None or issue == "":
                raise ProtocolError("Clarify requires a non-empty issue or question.")
        if mtype == "tool_action":
            action = content.get("action")
            mode = content.get("mode")
            if not action:
                raise ProtocolError("tool_action requires content.action.")
            if mode not in {"simulate", "dry-run", "execute"}:
                raise ProtocolError("tool_action mode must be one of simulate|dry-run|execute.")

    def _log_validation_failure(self, message: Message, error: str) -> Dict[str, Any]:
        shared = self.load_shared()
        shared.setdefault("history", []).append(
            {
                "type": "VALIDATION_FAILURE",
                "error": error,
                "raw_message": getattr(message, "__dict__", str(message)),
            }
        )
        shared["failure_mode"] = shared.get("failure_mode") or "schema_failure"
        self.save_shared(shared)
        return shared

    def _log_divergence_and_repair(self, message: Message, reason: str, metrics: Metrics | None) -> Dict[str, Any]:
        repair_content = {"conflict": reason}
        shared = self.load_shared()
        repair = Message(
            sender="InteractionManager",
            receiver="all",
            type="repair",
            content=repair_content,
            confidence=1.0,
            assumptions=[],
            context={"detected_from": message.id},
        )
        self.log_message(repair)
        self.update_shared_models(repair)
        if metrics:
            metrics.record_auto_repair()
        shared = self.load_shared()
        return shared

    def _role_gate(self, shared: Dict[str, Any], message: Message) -> str | None:
        """Ensure only the right agent performs certain acts."""
        roles = shared.get("roles", {})
        planner = roles.get("Planner")
        executor = roles.get("Executor")
        if message.type == "propose" and planner and message.sender != planner:
            return "Role violation: only Planner may propose."
        if message.type == "commit" and executor and message.sender not in {executor, "InteractionManager"}:
            return "Role violation: only Executor may commit."
        return None

    def _commit_gate(self, shared: Dict[str, Any], message: Message) -> str | None:
        """Enforce negotiation before commit under conflict/uncertainty."""
        if message.type != "commit":
            return None
        # --- EC16/18/19 FIX: early unresolved-challenge block ---
        history = shared.get("history", [])
        has_challenge = any(h.get("type") == "challenge" for h in history)
        has_response = any(h.get("type") in {"revise", "repair"} for h in history)
        if has_challenge and not has_response:
            return "Commit blocked: tradeoff challenge not answered by revise/repair."
        # --------------------------------------------------------
        plan_status = shared.get("commitments", {}).get("plan_status")
        # Block if DAG failures or unresolved nodes present
        dag_failures = shared.get("world_state", {}).get("dag_failures") or []
        if dag_failures:
            return "Commit blocked: DAG failures present."
        dag_unresolved = shared.get("world_state", {}).get("dag_unresolved") or []
        if dag_unresolved:
            return "Commit blocked: DAG unresolved dependencies present."
        # If monitor pending, block commits
        if shared.get("commitments", {}).get("monitor_request_pending"):
            return "Commit blocked: monitor requested repair; respond with revise/repair before committing."
        if plan_status == "repair_required":
            return "Commit blocked: monitor requested repair; revise/repair first."
        if plan_status == "proposed":
            history = shared.get("history", [])
            has_grounding = any(h.get("type") in {"clarify", "challenge"} for h in history)
            if not has_grounding:
                return "Commitment rejected: no clarification/challenge occurred after proposal."
        # Block duplicate reconciliation commits
        if message.content.get("commitment") == "accept_plan_reconciled":
            history = shared.get("history", [])
            if history and history[-1].get("type") == "commit" and history[-1].get("content", {}).get("commitment") == "accept_plan_reconciled":
                return "Commit blocked: duplicate reconciliation commit."
        # Block repeated commit attempts with same plan
        if message.type == "commit":
            last_commit = next((h for h in reversed(shared.get("history", [])) if h.get("type") == "commit"), None)
            if last_commit and last_commit.get("content", {}).get("plan") == message.content.get("plan"):
                return "Commit blocked: repeated commit with same plan."
        # Resource mismatch simple check
        resources = shared.get("resources", {}).get("credentials", {})
        if resources and message.sender in resources and resources.get(message.sender) is False:
            plan = message.content.get("plan") or []
            delegated = shared.get("world_state", {}).get("delegated_deploy_to")
            if any("deploy" in str(step).lower() or "root" in str(step).lower() for step in plan):
                if delegated and resources.get(delegated, False):
                    # delegation covers the capability
                    return None
                if os.environ.get("HI_MACP_ALLOW_DELEGATED_DEPLOY", "0") != "1":
                    return "Commit blocked: executor lacks required credentials for deployment tasks."
        # Tradeoff gate: require negotiation/justification when role goals exist
        role_goals = shared.get("world_state", {}).get("role_goals") or {}
        if role_goals and not self._tradeoff_negotiated(shared):
            return "Commit blocked: tradeoff (secure vs fast) not negotiated/justified."
        # Tool gate: block commit if any tool_result is non-success
        tool_results = shared.get("world_state", {}).get("tool_results", [])
        if any(tr.get("status") not in {"success"} for tr in tool_results):
            return "Commit blocked: tool actions failed or incomplete."
        return None

    def _tradeoff_negotiated(self, shared: Dict[str, Any]) -> bool:
        history = shared.get("history", [])
        keywords = ("secure", "fast", "tradeoff")
        negotiated = any(
            h.get("type") in {"clarify", "challenge", "revise", "repair"}
            and any(kw in json.dumps(h.get("content", {})).lower() for kw in keywords)
            for h in history
        )
        return negotiated
