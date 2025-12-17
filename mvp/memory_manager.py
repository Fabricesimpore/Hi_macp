from typing import Any, Dict, List


class MemoryManager:
    """Minimal memory manager that maintains compact deltas for prompts and state tracking."""

    def __init__(self):
        self.last_message: str | None = None
        self.flags: Dict[str, Any] = {}
        self.snapshot: Dict[str, Any] = {}

    def update_from_message(self, summary: str, message_dict: Dict[str, Any], shared: Dict[str, Any]) -> None:
        self.last_message = summary
        self.snapshot = {
            "plan_status": shared.get("commitments", {}).get("plan_status"),
            "monitor_pending": shared.get("commitments", {}).get("monitor_request_pending"),
            "monitor_confirmed": shared.get("commitments", {}).get("monitor_confirmed"),
            "tasks_len": len(shared.get("tasks", [])),
            "claimed_tasks": shared.get("world_state", {}).get("claimed_tasks"),
            "last_type": message_dict.get("type"),
            "last_sender": message_dict.get("sender"),
        }

    def plan_preview(self, plan, max_items: int = 2):
        if isinstance(plan, list):
            return plan[:max_items]
        return plan

    def delta(self, agent: str, shared: Dict[str, Any], hints: Dict[str, Any]) -> Dict[str, Any]:
        commitments = shared.get("commitments", {})
        world_state = shared.get("world_state", {})
        tasks = shared.get("tasks", [])
        claimed = world_state.get("claimed_tasks")
        changes = {}
        if claimed is not None and isinstance(tasks, list):
            changes["task_count_mismatch"] = len(tasks) != claimed
        return {
            "last_message": hints.get("last_message_summary") or world_state.get("last_message") or self.last_message,
            "plan_preview": self.plan_preview(hints.get("plan") or tasks),
            "plan_status": commitments.get("plan_status"),
            "monitor_pending": commitments.get("monitor_request_pending"),
            "monitor_confirmed": commitments.get("monitor_confirmed"),
            "claimed_tasks": claimed,
            "changes": changes,
            "flags": self.flags,
        }
