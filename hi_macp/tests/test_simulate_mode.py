import json
import os
from pathlib import Path

from hi_macp.core.interaction_manager import InteractionManager
from hi_macp.core.protocol import Message


def make_manager(tmp_path: Path):
    shared_path = tmp_path / "shared.json"
    shared = {
        "commitments": {"plan_status": "revised", "monitor_confirmed": False, "monitor_request_pending": False},
        "tasks": [{"id": "Deploy", "type": "task"}],
        "world_state": {"tool_capabilities": {"allow_execute": False}},
        "history": [],
    }
    shared_path.write_text(json.dumps(shared))
    return InteractionManager(shared_path)


def test_simulate_mode_skips_task_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("HI_MACP_SIMULATE_ONLY", "1")
    m = make_manager(tmp_path)
    # propose has 2 steps, tasks only 1 -> would be mismatch in execute mode
    propose = Message(
        sender="AgentA",
        receiver="AgentB",
        type="propose",
        content={"plan": [{"id": "a"}, {"id": "b"}]},
        confidence=0.8,
        assumptions=[],
        context={},
    )
    m.route(propose)
    # simulate clarified plan is shorter
    m.save_shared({**m.load_shared(), "tasks": [{"id": "Deploy"}]})
    ok, reason = m.check_divergence(m.load_shared(), propose)
    assert not ok, f"simulate mode should not flag task mismatch: {reason}"


def test_simulate_mode_monitor_commits_without_tradeoff(monkeypatch, tmp_path):
    monkeypatch.setenv("HI_MACP_SIMULATE_ONLY", "1")
    from hi_macp.agents.monitor import AgentMonitor

    m = make_manager(tmp_path)
    monitor = AgentMonitor(m)
    shared, aligned = monitor.review_alignment()
    assert shared["commitments"]["plan_status"] == "committed"
    assert aligned == (False, "") or aligned == (True, "")
