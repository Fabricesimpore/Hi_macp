from typing import Tuple, Dict, Any

from hi_macp.core.interaction_manager import InteractionManager
from hi_macp.core.protocol import Message
from hi_macp.tools.command_runner import CommandRunner
from hi_macp.tools.wrappers import ToolWrappers


class AgentToolExecutor:
    """Executes or simulates DevOps commands and reports results as HI-MACP messages."""

    def __init__(self, manager: InteractionManager, runner: CommandRunner | None = None, tool_paths: Dict[str, str] | None = None, allow_execute: bool | None = None) -> None:
        self.manager = manager
        self.name = "ToolExecutor"
        self.runner = runner or CommandRunner(allow_execute=bool(allow_execute), tool_paths=tool_paths)
        self.wrappers = ToolWrappers(allow_execute=bool(allow_execute), tool_paths=tool_paths)

    def handle_action(self, action_msg: Message, metrics=None) -> Tuple[Dict[str, Any], Tuple[bool, str]]:
        """Process a tool_action message and return routed result."""
        content = action_msg.content or {}
        action = content.get("action")
        mode = content.get("mode", "simulate")
        target = content.get("target")
        extra = content.get("extra") or {}

        # Prefer wrapper implementations for common actions; fallback to generic runner
        if action == "kubectl_apply":
            result = self.wrappers.kubectl_apply(target or "", namespace=extra.get("namespace"), dry_run=(mode != "execute"))
        elif action == "kubectl_rollout_status":
            result = self.wrappers.kubectl_rollout_status(extra.get("resource", ""), namespace=extra.get("namespace"))
        elif action == "helm_template":
            result = self.wrappers.helm_template(target or "")
        elif action == "helm_diff":
            result = self.wrappers.helm_diff(extra.get("release", "release"), target or "", namespace=extra.get("namespace"), values=extra.get("values"))
        elif action == "helm_upgrade":
            result = self.wrappers.helm_upgrade(extra.get("release", "release"), target or "", namespace=extra.get("namespace"), values=extra.get("values"))
        elif action == "smoke_test":
            # placeholder: docker run echo smoke-test
            result = self.wrappers.docker_run("alpine:latest", cmd="echo smoke-test")
        elif action == "health_check":
            result = self.wrappers.health_check(target or extra.get("url", "http://localhost/healthz"))
        elif action == "smoke_custom":
            result = self.wrappers.smoke_custom(target or extra.get("cmd", "echo smoke"))
        elif action == "docker_build":
            result = self.wrappers.docker_build(target or ".", tag=extra.get("tag", "hi-macp:latest"))
        elif action == "docker_push":
            result = self.wrappers.docker_push(target or extra.get("tag", "hi-macp:latest"))
        elif action == "docker_login":
            result = self.wrappers.docker_login(extra.get("registry", ""), username=extra.get("username"), password=extra.get("password"))
        elif action == "git_status":
            result = self.wrappers.git_status(target)
        elif action == "git_diff":
            result = self.wrappers.git_diff(target)
        elif action == "git_checkout_branch":
            result = self.wrappers.git_checkout_new_branch(extra.get("branch", "ci-fix/auto"), target)
        elif action == "git_add":
            result = self.wrappers.git_add_all(target)
        elif action == "git_commit":
            result = self.wrappers.git_commit(extra.get("message", "hi-macp commit"), target)
        elif action == "git_push":
            result = self.wrappers.git_push(extra.get("branch", "main"), target)
        elif action == "fs_read":
            result = self.wrappers.fs_read(target or "")
        elif action == "fs_write":
            result = self.wrappers.fs_write(target or "", content.get("content", ""))
        elif action == "terraform_init":
            result = self.wrappers.terraform_init(target or ".")
        elif action == "terraform_plan":
            result = self.wrappers.terraform_plan(target or ".", out=extra.get("out"))
        elif action == "terraform_apply":
            result = self.wrappers.terraform_apply(target or ".", plan=extra.get("plan"))
        elif action == "github_list_workflow_runs":
            from hi_macp.tools import github_actions

            result = github_actions.list_workflow_runs(
                extra.get("owner", ""), extra.get("repo", ""), extra.get("workflow_id", ""), extra.get("branch", "main")
            )
        elif action == "github_rerun_workflow_run":
            from hi_macp.tools import github_actions

            result = github_actions.rerun_workflow_run(extra.get("owner", ""), extra.get("repo", ""), int(extra.get("run_id", 0)))
            if result.get("status") == "success":
                shared = self.manager.load_shared()
                ws = shared.setdefault("world_state", {})
                ws["ci_reruns"] = ws.get("ci_reruns", 0) + 1
                self.manager.save_shared(shared)
        elif action == "github_get_workflow_logs":
            from hi_macp.tools import github_actions

            result = github_actions.get_workflow_logs(extra.get("owner", ""), extra.get("repo", ""), int(extra.get("run_id", 0)))
        elif action == "github_create_pull_request":
            from hi_macp.tools import github_actions

            result = github_actions.create_pull_request(
                extra.get("owner", ""), extra.get("repo", ""), extra.get("head", ""), extra.get("base", "main"), extra.get("title", "hi-macp auto-fix"), extra.get("body")
            )
            if result.get("status") == "success" and result.get("number"):
                shared = self.manager.load_shared()
                ws = shared.setdefault("world_state", {})
                ws["pr_number"] = result.get("number")
                ws["pr_url"] = result.get("url")
                self.manager.save_shared(shared)
        elif action == "github_merge_pull_request":
            from hi_macp.tools import github_actions

            result = github_actions.merge_pull_request(
                extra.get("owner", ""), extra.get("repo", ""), int(extra.get("number", 0)), extra.get("merge_method", "squash")
            )
        else:
            result = self.runner.run_action(action=action, mode=mode, target=target, extra=extra)
        # Normalize result schema for downstream gating
        if isinstance(result, dict):
            result.setdefault("action", action)
            result.setdefault("mode", mode)
            result.setdefault("target", target)
        status = result.get("status")
        if status == "success":
            response_type = "inform"
        else:
            response_type = "repair"
        response = Message(
            sender=self.name,
            receiver=action_msg.sender,
            type=response_type,
            content={"action_result": result},
            confidence=0.8,
            assumptions=[f"mode={mode}", f"allow_execute={self.runner.allow_execute}"],
            context={"goal": action_msg.context.get("goal", "execute action"), "phase": "execution"},
        )
        return self.manager.route(response, metrics=metrics)

    def capabilities(self) -> Dict[str, Any]:
        return self.runner.capability_profile()
