from typing import Tuple, Dict, Any

from interaction_manager import InteractionManager
from protocol import Message
from tools.command_runner import CommandRunner


class AgentToolExecutor:
    """Executes or simulates DevOps commands and reports results as HI-MACP messages."""

    def __init__(self, manager: InteractionManager, runner: CommandRunner | None = None, tool_paths: Dict[str, str] | None = None, allow_execute: bool | None = None) -> None:
        self.manager = manager
        self.name = "ToolExecutor"
        self.runner = runner or CommandRunner(allow_execute=bool(allow_execute), tool_paths=tool_paths)

    def handle_action(self, action_msg: Message, metrics=None) -> Tuple[Dict[str, Any], Tuple[bool, str]]:
        """Process a tool_action message and return routed result."""
        content = action_msg.content or {}
        action = content.get("action")
        mode = content.get("mode", "simulate")
        target = content.get("target")
        extra = content.get("extra") or {}

        result = self.runner.run_action(action=action, mode=mode, target=target, extra=extra)
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
