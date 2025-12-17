from typing import List, Tuple

from interaction_manager import InteractionManager
from protocol import Message
from llm_adapter import LLMAdapter


class AgentExecutor:
    CLARIFY_THRESHOLD = 0.7
    CHALLENGE_THRESHOLD = 0.65

    def __init__(self, manager: InteractionManager, adapter: LLMAdapter | None = None) -> None:
        self.manager = manager
        self.name = "AgentB"
        self.partner = "AgentA"
        self.adapter = adapter or LLMAdapter(enabled=False)
        self.monitor = "AgentM"

    def evaluate_plan(self, plan: List[str], metrics=None) -> Tuple[dict, Tuple[bool, str], str]:
        shared = self.manager.load_shared()
        resources = shared.get("resources", {}).get("credentials", {})
        executor_creds = resources.get("AgentB", True)
        delegation_target = shared.get("world_state", {}).get("delegated_deploy_to")
        role_goal = shared.get("world_state", {}).get("role_goals", {}).get("Executor")
        def _as_text(item):
            if isinstance(item, dict):
                return " ".join(str(v) for v in item.values() if v)
            return str(item)
        plan_texts = [_as_text(p).lower() for p in plan or []]
        plan = self._apply_preference(plan, role_goal)
        # Mandatory fast tradeoff challenge if plan is secure-heavy and executor prefers speed
        secure_signals = ("sbom", "sign", "rotate", "regression", "deep telemetry", "approval gate", "2-step rollback")
        if role_goal == "minimize_time" and any(any(sig in p for sig in secure_signals) for p in plan_texts):
            fast_proposal = [
                "skip-sign",
                "reuse existing secrets",
                "direct push",
                "smoke-only",
                "minimal checks",
                "single toggle rollback",
                "baseline network policies",
                "direct DNS update",
                "skip audit",
                "defer rotation",
                "auto-approve",
                "immediate cutover",
            ]
            challenge = Message(
                sender=self.name,
                receiver=self.partner,
                type="challenge",
                content={
                    "assumption": "Plan is too security-heavy for speed goal",
                    "proposed_delegation": "AgentA will deploy",
                    "proposed_fast_steps": fast_proposal[: len(plan)],
                },
                confidence=0.6,
                assumptions=["Planner will offer compromise"],
                context={"goal": "create plan"},
            )
            shared, divergence = self.manager.route(challenge, metrics=metrics)
            return shared, divergence, "challenge"
        # Proactive delegation proposal on first capability mismatch
        if (not executor_creds) and any("deploy" in p or "root" in p for p in plan_texts) and delegation_target is None:
            propose_delegate = Message(
                sender=self.name,
                receiver=self.partner,
                type="challenge",
                content={"assumption": "Executor lacks deployment credentials", "proposed_delegation": "AgentA will deploy"},
                confidence=0.6,
                assumptions=["Planner can adjust responsibilities"],
                context={"goal": "create plan"},
            )
            shared, divergence = self.manager.route(propose_delegate, metrics=metrics)
            return shared, divergence, "challenge"
        llm_msg = self.adapter.generate(
            "Executor",
            "evaluate_plan",
            shared,
            hints={
                "plan": plan,
                "phase": shared.get("phase"),
                "monitor_pending": shared.get("commitments", {}).get("monitor_request_pending", False),
                "role_goal": shared.get("world_state", {}).get("role_goals", {}).get("Executor"),
            },
        )
        if llm_msg:
            shared, divergence = self.manager.route(llm_msg, metrics=metrics)
            return shared, divergence, llm_msg.type

        last_type = ""
        if shared.get("history"):
            last_type = shared["history"][-1].get("type", "")
            # Do not commit immediately after issuing a challenge; wait for revise/repair
            if last_type == "challenge" and shared.get("commitments", {}).get("plan_status") != "revised":
                needs_clarify = True
                waiting_for_revise = True
            else:
                needs_clarify = False
                waiting_for_revise = False
        else:
            needs_clarify = False
            waiting_for_revise = False

        # Confidence heuristic: penalize if deploy/tests present
        plan_confidence = 0.85
        if any("test" in step for step in plan_texts):
            plan_confidence -= 0.15
        if any("deploy" in step for step in plan_texts):
            plan_confidence -= 0.1

        # Failure Mode 1: if any step mentions tests or deploy, ask for clarification unless we just received a revise/repair and confidence passes threshold.
        needs_clarify = any("test" in step or "deploy" in step for step in plan_texts)
        # force clarify if we just challenged
        needs_clarify = needs_clarify or (last_type == "challenge")
        if needs_clarify and last_type not in {"revise", "repair"} and plan_confidence < self.CLARIFY_THRESHOLD:
            clarify = Message(
                sender=self.name,
                receiver=self.partner,
                type="clarify",
                content={"issue": "Step is vague. What kind of tests or deploy details?"},
                confidence=0.64,
                assumptions=["Planner can extend plan"],
                context={"goal": "create plan"},
            )
            shared, divergence = self.manager.route(clarify, metrics=metrics)
            return shared, divergence, "clarify"

        # Failure Mode 2: challenge assumption if credentials are assumed (check last message assumptions or plan text) and confidence to proceed is below threshold
        last_assumptions = shared["history"][-1].get("assumptions", []) if shared.get("history") else []
        if ("Executor has deployment credentials" in " ".join(plan_texts) or "Executor has deployment credentials" in " ".join(last_assumptions) or any("deploy" in p for p in plan_texts)) and (not executor_creds) and delegation_target is None:
            challenge = Message(
                sender=self.name,
                receiver=self.partner,
                type="challenge",
                content={"assumption": "I do not have deployment credentials", "proposed_delegation": "AgentA will deploy"},
                confidence=0.6,
                assumptions=["Planner can adjust responsibilities"],
                context={"goal": "create plan"},
            )
            shared, divergence = self.manager.route(challenge, metrics=metrics)
            return shared, divergence, "challenge"

        # Failure Mode 4: Theory-of-Mind mismatch about DNS knowledge (initial detection)
        if last_type != "repair" and any("configure domain" in step for step in plan_texts):
            repair = Message(
                sender=self.name,
                receiver=self.partner,
                type="repair",
                content={"conflict": "Planner assumes I know DNS setup; I do not."},
                confidence=0.58,
                assumptions=["Planner will provide DNS steps or support"],
                context={"goal": "create plan"},
            )
            shared, divergence = self.manager.route(repair, metrics=metrics)
            return shared, divergence, "repair"
        # If we are already in repair loop, ensure DNS conflict has been flagged at least once
        repairs = shared.get("world_state", {}).get("repairs", [])
        if last_type == "repair" and "Planner assumes I know DNS setup; I do not." not in repairs:
            repair = Message(
                sender=self.name,
                receiver=self.partner,
                type="repair",
                content={"conflict": "Planner assumes I know DNS setup; I do not."},
                confidence=0.58,
                assumptions=["Planner will provide DNS steps or support"],
                context={"goal": "create plan"},
            )
            shared, divergence = self.manager.route(repair)
            return shared, divergence, "repair"

        # Failure Mode 3: intentionally truncate plan to induce divergence detection
        claimed = shared.get("world_state", {}).get("claimed_tasks")
        # Avoid truncation when claimed tasks present or delegation resolved
        if claimed or delegation_target:
            truncated_plan = plan
        else:
            truncated_plan = plan[:3] if len(plan) > 3 else plan
        # If last interaction was challenge and no planner revise yet, do not commit
        if waiting_for_revise:
            clarify = Message(
                sender=self.name,
                receiver=self.partner,
                type="clarify",
                content={"issue": "Waiting for planner compromise after tradeoff challenge."},
                confidence=0.6,
                assumptions=["Planner will offer compromise"],
                context={"goal": "create plan"},
            )
            shared, divergence = self.manager.route(clarify, metrics=metrics)
            return shared, divergence, "clarify"
        commit = Message(
            sender=self.name,
            receiver=self.partner,
            type="commit",
            content={"commitment": "accept_plan", "plan": truncated_plan},
            confidence=0.82,
            assumptions=["Plan is feasible as written"],
            context={"goal": "create plan"},
        )
        shared, divergence = self.manager.route(commit, metrics=metrics)
        return shared, divergence, "commit"

    def reconcile_commit(self, plan: List[str], metrics=None) -> Tuple[dict, Tuple[bool, str]]:
        # Final reconciliation: commit full plan without truncation or additional objections.
        shared = self.manager.load_shared()
        llm_msg = self.adapter.generate(
            "Executor",
            "reconcile_commit",
            shared,
            hints={"plan": plan, "phase": "reconcile"},
        )
        if llm_msg:
            return self.manager.route(llm_msg, metrics=metrics)
        commit = Message(
            sender=self.name,
            receiver=self.partner,
            type="commit",
            content={"commitment": "accept_plan_reconciled", "plan": plan},
            confidence=0.9,
            assumptions=["Divergences resolved and plan accepted"],
            context={"goal": "create plan", "phase": "reconcile"},
        )
        return self.manager.route(commit, metrics=metrics)

    def respond_to_monitor(self, issue: str, current_plan: List[str], metrics=None) -> Tuple[dict, Tuple[bool, str], str]:
        """Mandatory response to monitor: send revise/repair addressing monitor issue."""
        shared = self.manager.load_shared()
        role_goal = shared.get("world_state", {}).get("role_goals", {}).get("Executor")
        llm_msg = self.adapter.generate(
            "Executor",
            "monitor_response",
            shared,
            hints={"monitor_issue": issue, "plan": current_plan, "monitor_pending": True, "role_goal": role_goal},
        )
        if llm_msg and llm_msg.content:
            message = llm_msg
            action = llm_msg.type
        else:
            message = Message(
                sender=self.name,
                receiver=self.monitor,
                type="repair",
                content={
                    "conflict": issue or "Acknowledging monitor issue; executor provides clarification.",
                    "capability": "Executor lacks root credentials and needs reassigned responsibilities",
                    "plan": current_plan,
                    "tradeoff": "fast option preferred" if role_goal == "minimize_time" else "secure option accepted",
                },
                confidence=0.65,
                assumptions=["Monitor concerns addressed"],
                context={"goal": "create plan", "phase": "repair"},
            )
            action = "repair"
        shared, divergence = self.manager.route(message, metrics=metrics)
        shared["commitments"]["monitor_request_pending"] = False
        self.manager.save_shared(shared)
        return shared, divergence, action

    def _apply_preference(self, plan: List[str], role_goal: str | None) -> List[str]:
        """Choose secure vs fast options encoded as 'secure=... | fast=...'."""
        if not plan:
            return plan
        preferred = []
        prefer_fast = role_goal == "minimize_time"
        for step in plan:
            if "secure=" in step and "fast=" in step:
                secure_part = step.split("secure=", 1)[1].split("|")[0].strip()
                fast_part = step.split("fast=", 1)[1].strip()
                choice = fast_part if prefer_fast else secure_part
                preferred.append(choice)
            else:
                preferred.append(step)
        return preferred

    def _apply_preference(self, plan: List[str], role_goal: str | None) -> List[str]:
        """Choose secure vs fast options encoded as 'secure=... | fast=...'."""
        if not plan:
            return plan
        preferred = []
        prefer_fast = role_goal == "minimize_time"
        for step in plan:
            if "secure=" in step and "fast=" in step:
                secure_part = step.split("secure=", 1)[1].split("|")[0].strip()
                fast_part = step.split("fast=", 1)[1].strip()
                choice = fast_part if prefer_fast else secure_part
                preferred.append(choice)
            else:
                preferred.append(step)
        return preferred
