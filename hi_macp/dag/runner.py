from typing import Any, Dict, List, Tuple

from hi_macp.agents.tool_executor import AgentToolExecutor
from hi_macp.core.protocol import Message
from hi_macp.core.interaction_manager import InteractionManager
from hi_macp.dag.executor import DagExecutor


class DagRunner:
    """Runs structured plan steps respecting dependency batches. Uses ToolExecutor for tool-backed steps."""

    def __init__(self, manager: InteractionManager, tool_exec: AgentToolExecutor) -> None:
        self.manager = manager
        self.tool_exec = tool_exec

    def run(
        self,
        steps: List[Dict[str, Any]],
        metrics=None,
        rollback_on_failure: bool = True,
        retries: int = 0,
        parallel: bool = False,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Execute batches in order. Returns results and failures. If rollback_on_failure, abort remaining on first failure."""
        attempts = max(1, retries + 1)
        last_results: List[Dict[str, Any]] = []
        last_failures: List[str] = []
        batches, unresolved = DagExecutor(steps).resolve()
        for _attempt in range(attempts):
            results: List[Dict[str, Any]] = []
            failures: List[str] = []
            for batch in batches:
                for step in batch:
                    tool = step.get("tool")
                    mode = step.get("mode", "simulate")
                    target = step.get("target")
                    if tool:
                        tool_msg = Message(
                            sender="AgentB",
                            receiver="ToolExecutor",
                            type="tool_action",
                            content={"action": tool, "mode": mode, "target": target},
                            confidence=0.7,
                            assumptions=[f"mode={mode}"],
                            context={"goal": "execute dag step", "step_id": step.get("id")},
                        )
                        shared, div = self.manager.route(tool_msg, metrics=metrics)
                        if div[0]:
                            failures.append(div[1])
                            if rollback_on_failure:
                                results.append(
                                    {"action": "rollback", "status": "simulated", "details": "Rollback triggered due to failure"}
                                )
                                return results, unresolved + failures
                            continue
                        shared, div = self.tool_exec.handle_action(tool_msg, metrics=metrics)
                        if div[0]:
                            failures.append(div[1])
                            if rollback_on_failure:
                                results.append(
                                    {"action": "rollback", "status": "simulated", "details": "Rollback triggered due to failure"}
                                )
                                return results, unresolved + failures
                            continue
                        latest = self.manager.load_shared().get("history", [])[-1].get("content", {}).get("action_result")
                        if latest:
                            results.append(latest)
                            if latest.get("status") not in {"success"}:
                                failures.append(latest.get("details", latest.get("status")))
                                if rollback_on_failure:
                                    results.append(
                                        {"action": "rollback", "status": "simulated", "details": "Rollback triggered due to failure"}
                                    )
                                    return results, unresolved + failures
                    else:
                        results.append({"action": step.get("id"), "status": "success", "details": "simulated non-tool step"})
            if not failures:
                return results, unresolved
            last_results, last_failures = results, failures
        if rollback_on_failure and last_failures:
            last_results.append({"action": "rollback", "status": "simulated", "details": "Rollback triggered due to failure"})
        return last_results, unresolved + last_failures
