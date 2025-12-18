import json
import os
from pathlib import Path

import pytest

from hi_macp.agents.monitor import AgentMonitor
from hi_macp.core.interaction_manager import InteractionManager
from hi_macp.tools import github_actions


def make_manager(tmp_path: Path):
    shared_path = tmp_path / "shared.json"
    shared = {
        "commitments": {"plan_status": "committed", "monitor_confirmed": False, "monitor_request_pending": False},
        "tasks": [{"id": "t1", "type": "task"}],
        "world_state": {
            "tool_capabilities": {"allow_execute": True},
            "push_policy": {"mode": "pr-first", "reason": "default"},
            "github": {"owner": "o", "repo": "r", "workflow_id": "ci.yml", "branch": "ci-fix/auto"},
            "tool_results": [
                {"action": "git", "status": "success", "mode": "execute", "target": "push origin ci-fix/auto"},
                {
                    "action": "github_create_pull_request",
                    "status": "unauthorized",
                    "mode": "execute",
                    "target": None,
                    "details": "GH_TOKEN lacks permission to create PRs (403).",
                },
            ],
        },
        "history": [
            {
                "type": "propose",
                "content": {"tradeoffs": {"option": "secure", "justification": "balance speed and safety"}},
            }
        ],
        "phase": "execution",
    }
    shared_path.write_text(json.dumps(shared))
    return InteractionManager(shared_path)


def test_alignment_true_with_authority_bound_blocker(monkeypatch, tmp_path):
    monkeypatch.setenv("HI_MACP_FORCE_PR_MODE", "1")
    monkeypatch.setenv("HI_MACP_ALLOW_EXECUTE", "1")

    def fake_list_runs(owner, repo, workflow_id, branch):
        return {"status": "success", "run_id": 1, "status_text": "completed", "conclusion": "success", "html_url": "x"}

    monkeypatch.setattr(github_actions, "list_workflow_runs", fake_list_runs)

    manager = make_manager(tmp_path)
    monitor = AgentMonitor(manager)
    shared, _div = monitor.review_alignment()
    shared = manager.load_shared()
    assert shared["commitments"]["plan_status"] == "committed"
    assert shared["commitments"]["monitor_confirmed"] is True
    blockers = (shared.get("world_state") or {}).get("blockers") or []
    assert blockers and blockers[0]["type"] == "authority_bound"
