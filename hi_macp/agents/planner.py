import json
import os
import re
from pathlib import Path
from typing import List, Tuple

from hi_macp.core.interaction_manager import InteractionManager
from hi_macp.core.protocol import Message
from hi_macp.llm.adapter import LLMAdapter


class AgentPlanner:
    def __init__(self, manager: InteractionManager, adapter: LLMAdapter | None = None) -> None:
        self.manager = manager
        self.name = "AgentA"
        self.partner = "AgentB"
        self.adapter = adapter or LLMAdapter(enabled=False)
        self.monitor = "AgentM"

    def _apply_ci_autofix_files(self, shared: dict, ci_tags: dict) -> List[str]:
        """Attempt lightweight, deterministic fixes based on CI tags/logs."""
        if os.environ.get("HI_MACP_AUTO_APPLY_CI_FIXES", "1") != "1":
            return []
        applied: List[str] = []
        logs = shared.get("world_state", {}).get("ci_logs_summary") or {}
        errors: List[str] = logs.get("errors") or []
        # Flatten tail200 snippets to scan for missing module names
        for snip in logs.get("snippets") or []:
            if isinstance(snip, dict):
                for key in ("tail200", "tail", "head"):
                    val = snip.get(key)
                    if val:
                        errors.extend(val.splitlines())
        reason = ci_tags.get("ci_failure_reason")
        if reason == "import_error":
            module = None
            for line in errors:
                m = re.search(r"ModuleNotFoundError: No module named ['\"]([^'\"\\s]+)['\"]", line)
                if m:
                    module = m.group(1)
                    break
            req_path = Path("requirements.txt")
            if module:
                try:
                    existing = req_path.read_text().splitlines() if req_path.exists() else []
                    if module not in existing:
                        updated = "\n".join(existing + [module]) + "\n"
                        req_path.write_text(updated)
                        applied.append(f"added missing dependency '{module}' to requirements.txt")
                except Exception as exc:  # pragma: no cover - safety net
                    applied.append(f"failed to update requirements.txt: {exc}")
            else:
                # If module not found, drop a hint file for humans/LLM
                Path("CI_AUTO_IMPORT_HINT.txt").write_text("Detected import error but module name not parsed.\n")
                applied.append("wrote CI_AUTO_IMPORT_HINT.txt for unresolved import error")
        if applied:
            shared.setdefault("world_state", {})["ci_autofix_applied"] = applied
            self.manager.save_shared(shared)
        return applied

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

        def add_action(action: dict) -> None:
            key = (action.get("action"), action.get("mode"), action.get("target"))
            if key in seen:
                return
            seen.add(key)
            actions.append(action)

        ci_tags = shared.get("world_state", {}).get("ci_tags") or {}
        ci_state = shared.get("world_state", {}).get("ci_state") or {}
        github_cfg = shared.get("world_state", {}).get("github") or {}
        pr_mode = bool(os.environ.get("HI_MACP_PR_MODE", "0") == "1" or github_cfg.get("pr_mode"))
        pr_branch = os.environ.get("HI_MACP_PR_BRANCH") or github_cfg.get("pr_branch") or "ci-fix/auto"
        suffix = os.environ.get("HI_MACP_CI_CONCURRENCY_SUFFIX") or github_cfg.get("concurrency_suffix")
        if suffix:
            pr_branch = f"{pr_branch}-{suffix}"
        pr_number = shared.get("world_state", {}).get("pr_number")
        max_commits = int(os.environ.get("HI_MACP_MAX_COMMITS", "3"))
        max_reruns = int(os.environ.get("HI_MACP_MAX_CI_RERUNS", "2"))
        ci_reruns = shared.get("world_state", {}).get("ci_reruns", 0)
        repair_edits = []
        if ci_tags:
            reason = ci_tags.get("ci_failure_reason")
            if reason == "import_error":
                repair_edits.append("Add missing dependency to requirements.txt")
            if reason == "lint_error":
                repair_edits.append("Run formatter/lint fixes on codebase")
            if reason == "test_failure":
                loc = ci_tags.get("ci_failure_location")
                repair_edits.append(f"Investigate failing test at {loc or 'tests/'} and add fix")
            if ci_tags.get("ci_cache_suspect"):
                repair_edits.append("CI cache suspect: consider clearing caches or rerunning without cache")
            if not repair_edits:
                repair_edits.append("General CI fix patch based on logs")
            # Apply lightweight autofix before planning actions (e.g., add missing dependency)
            self._apply_ci_autofix_files(shared, ci_tags)

        for step in plan or []:
            s = step.lower() if isinstance(step, str) else json.dumps(step).lower()
            if "test" in s and "smoke_test" not in seen:
                add_action({"action": "smoke_test", "mode": choose_mode(pref_dry="simulate"), "target": "run smoke tests"})
            if "health" in s and "health_check" not in seen:
                add_action({"action": "health_check", "mode": choose_mode(pref_dry="simulate"), "target": "http://localhost/healthz"})
            if "deploy" in s and "kubectl_apply" not in seen:
                # Always dry-run before execute; execute only if allowed
                dry_mode = choose_mode(pref_dry="dry-run")
                add_action({"action": "kubectl_apply", "mode": dry_mode, "target": "manifests/"})
                if allow_execute:
                    add_action({"action": "kubectl_apply", "mode": "execute", "target": "manifests/"})
                    add_action({"action": "kubectl_rollout_status", "mode": "execute", "target": None, "extra": {"resource": "deployment/sample-service"}})
            if "template" in s and "helm_template" not in seen:
                mode = "execute" if tools.get("helm") else "simulate"
                mode = choose_mode(pref_execute=mode, pref_dry="simulate")
                add_action({"action": "helm_template", "mode": mode, "target": "chart/"})
                if allow_execute:
                    add_action({"action": "helm_diff", "mode": "execute", "target": "chart/", "extra": {"release": "hi-macp"}})
                    add_action({"action": "helm_upgrade", "mode": "execute", "target": "chart/", "extra": {"release": "hi-macp"}})
            if "infra" in s and "terraform_plan" not in seen:
                mode = "execute" if tools.get("terraform") else "simulate"
                mode = choose_mode(pref_execute=mode, pref_dry="simulate")
                add_action({"action": "terraform_plan", "mode": mode, "target": None, "extra": {"out": "tfplan.out"}})
                if allow_execute:
                    add_action({"action": "terraform_apply", "mode": "execute", "target": None, "extra": {"plan": "tfplan.out"}})
        # CI repair loop: add commit/push and CI rerun when CI tags exist
        if ci_tags:
            commit_msg = f"ci-fix: {ci_tags.get('ci_failure_reason', 'unknown')}"
            branch = pr_branch if pr_mode else (github_cfg.get("branch") or "main")
            if pr_mode:
                add_action({"action": "git_checkout_branch", "mode": "execute" if allow_execute else "simulate", "target": None, "extra": {"branch": branch}})
            if max_commits > 0:
                add_action({"action": "git_commit", "mode": "execute" if allow_execute else "simulate", "target": None, "extra": {"message": commit_msg}})
                max_commits -= 1
            add_action({"action": "git_push", "mode": "execute" if allow_execute else "simulate", "target": None, "extra": {"branch": branch}})
            if ci_state.get("run_id") and ci_reruns < max_reruns:
                add_action(
                    {
                        "action": "github_rerun_workflow_run",
                        "mode": "execute" if allow_execute else "simulate",
                        "target": None,
                        "extra": {"owner": github_cfg.get("owner", ""), "repo": github_cfg.get("repo", ""), "run_id": ci_state.get("run_id")},
                    }
                )
            if pr_mode:
                add_action(
                    {
                        "action": "github_create_pull_request",
                        "mode": "execute" if allow_execute else "simulate",
                        "target": None,
                        "extra": {
                            "owner": github_cfg.get("owner", ""),
                            "repo": github_cfg.get("repo", ""),
                            "head": branch,
                            "base": github_cfg.get("branch", "main"),
                            "title": commit_msg,
                            "body": "Automated CI fix proposal",
                        },
                    }
                )
            if pr_mode and pr_number and ci_state.get("conclusion") == "success":
                add_action(
                    {
                        "action": "github_merge_pull_request",
                        "mode": "execute" if allow_execute else "simulate",
                        "target": None,
                        "extra": {
                            "owner": github_cfg.get("owner", ""),
                            "repo": github_cfg.get("repo", ""),
                            "number": pr_number,
                            "merge_method": "squash",
                        },
                    }
                )
            if repair_edits:
                add_action({"action": "fs_write", "mode": "simulate", "target": "CI_FIX_NOTES.md", "extra": {}, "content": "\n".join(repair_edits)})
        return actions

    def _structure_plan(self, plan: List[str], shared: dict) -> List[dict]:
        """Convert textual plan steps into structured DevOps actions."""
        structured = []
        tools = shared.get("world_state", {}).get("tool_capabilities", {}).get("tools", {}) if isinstance(shared.get("world_state", {}), dict) else {}
        ci_tags = shared.get("world_state", {}).get("ci_tags") or {}

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
        # If CI failure tags exist, add a remediation task
        if ci_tags:
            structured.append(
                {
                    "id": "CI_fix",
                    "type": "task",
                    "tool": None,
                    "target": f"Apply fix for CI failure: {ci_tags.get('ci_failure_reason', 'unknown')}",
                    "mode": "simulate",
                    "requires": [],
                    "policy_tags": [],
                }
            )
        # Ensure templating step exists if required
        step_ids = {s.get("id") for s in structured if isinstance(s, dict)}
        needs_helm = any("helm_template" in (s.get("requires") or []) for s in structured)
        if needs_helm and "helm_template" not in step_ids:
            structured.insert(
                0,
                {
                    "id": "helm_template",
                    "type": "templating",
                    "tool": "helm_template",
                    "target": "chart/",
                    "mode": pick_mode("helm", "execute"),
                    "requires": [],
                    "policy_tags": [],
                },
            )
        # Add default health check and rollback tasks if any deploy present
        deploy_present = any(s.get("tool") == "kubectl_apply" for s in structured if isinstance(s, dict))
        if deploy_present and "Health_checks_before_shift" not in step_ids:
            structured.append(
                {
                    "id": "Health_checks_before_shift",
                    "type": "task",
                    "tool": None,
                    "target": "verify health probes before traffic",
                    "mode": "simulate",
                    "requires": [],
                    "policy_tags": [],
                }
            )
        step_ids = {s.get("id") for s in structured if isinstance(s, dict)}
        if deploy_present and "Rollback_triggers_and_playbook" not in step_ids:
            structured.append(
                {
                    "id": "Rollback_triggers_and_playbook",
                    "type": "task",
                    "tool": None,
                    "target": "define rollback triggers and playbook",
                    "mode": "simulate",
                    "requires": [],
                    "policy_tags": [],
                }
            )
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
        def build_tradeoffs(selected: str) -> dict:
            return {
                "option": selected,
                "justification": "balance speed and safety",
                "impact_on_plan": ["ordering", "tool modes"],
                "risks": ["may slow deployment"] if selected == "secure" else ["may reduce safety"],
            }
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
                    "tradeoffs": build_tradeoffs("secure" if shared.get("world_state", {}).get("role_goals", {}).get("Planner") == "maximize_security" else "fast"),
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
                    "tradeoffs": build_tradeoffs("secure"),
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
        ci_tags = shared.get("world_state", {}).get("ci_tags") or {}
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
            message.content["tradeoffs"] = message.content.get("tradeoffs") or {
                "option": "balanced",
                "justification": "addresses clarify issues",
                "impact_on_plan": ["ordering", "added checks"],
                "risks": ["slower cycle"],
            }
            message.content["revise_plan"] = {
                "changes": refined_plan,
                "justification": "addresses clarify issues",
                "tradeoffs": message.content.get("tradeoffs"),
                "ci_error_context": shared.get("world_state", {}).get("ci_state", {}),
                "ci_tags": ci_tags,
            }
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
                    "tradeoffs": {
                        "option": "secure",
                        "justification": "adds tests and rollback to address clarify",
                        "impact_on_plan": ["added smoke tests", "rollback checklist"],
                        "risks": ["longer deploy cycle"],
                    },
                    "revise_plan": {
                        "changes": aligned_plan,
                        "justification": "address clarify",
                        "tradeoffs": {
                            "option": "secure",
                            "justification": "adds tests and rollback",
                            "impact_on_plan": ["smoke tests", "rollback checklist"],
                            "risks": ["longer cycle"],
                        },
                        "ci_error_context": shared.get("world_state", {}).get("ci_state", {}),
                        "ci_tags": ci_tags,
                    },
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
                    "tradeoffs": {
                        "option": "balanced",
                        "justification": "compromise after challenge",
                        "impact_on_plan": ["kept signing/TLS", "reduced tests"],
                        "risks": ["reduced safety"],
                    },
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
            message.content["tradeoffs"] = message.content.get("tradeoffs") or {
                "option": "balanced",
                "justification": "addresses monitor issue",
                "impact_on_plan": ["added details"],
                "risks": ["longer cycle"],
            }
        else:
            refined_plan = self._align_plan(self._expand_or_rewrite_plan(current_plan), shared)
            refined_plan = self._structure_plan(refined_plan, shared)
            message = Message(
                sender=self.name,
                receiver=self.monitor,
                type="revise",
                content={
                    "conflict": issue or "Monitor requested clarification of plan details.",
                    "plan": refined_plan,
                    "tradeoff": "secure option reiterated" if shared.get("world_state", {}).get("role_goals", {}).get("Planner") == "maximize_security" else "fast option reiterated",
                    "tradeoffs": {
                        "option": "secure" if shared.get("world_state", {}).get("role_goals", {}).get("Planner") == "maximize_security" else "fast",
                        "justification": "adds detail to satisfy monitor",
                        "impact_on_plan": ["added rollback/checklist"],
                        "risks": ["longer cycle"] if shared.get("world_state", {}).get("role_goals", {}).get("Planner") == "maximize_security" else ["reduced safety"],
                    },
                    "ci_error_context": shared.get("world_state", {}).get("ci_state", {}),
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

    def _valid_plan(self, plan: List[str] | List[dict] | None) -> bool:
        if not isinstance(plan, list) or len(plan) < 2:
            return False
        placeholders = {"placeholder"}
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
        joined = " ".join(normalized).lower()
        return not any(ph in joined for ph in placeholders)

    def _align_plan(self, plan: List[str] | List[dict], shared: dict) -> List[str]:
        aligned = list(plan) if isinstance(plan, list) else []
        claimed = shared.get("world_state", {}).get("claimed_tasks")
        role_goal = shared.get("world_state", {}).get("role_goals", {}).get("Planner")
        aligned = self._apply_preference(aligned, role_goal)
        # If capability mismatch, add delegation step
        resources = shared.get("resources", {}).get("credentials", {})
        def _lower(step: object) -> str:
            if isinstance(step, str):
                return step.lower()
            try:
                import json

                return json.dumps(step).lower()
            except Exception:
                return str(step).lower()

        if resources and resources.get("AgentB") is False and any("deploy" in _lower(p) for p in aligned):
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

    def _apply_preference(self, plan: List[str] | List[dict], role_goal: str | None) -> List[str | dict]:
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
