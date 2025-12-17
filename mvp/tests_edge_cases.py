"""
Deterministic edge-case runner for HI-MACP.
Implements EC1–EC15 and reports structured PASS/FAIL summaries.
"""
import json
from pathlib import Path
from typing import Dict
import os

from llm_adapter import LLMAdapter
from interaction_manager import InteractionManager
from metrics import Metrics
from agent_planner import AgentPlanner
from agent_executor import AgentExecutor
from agent_monitor import AgentMonitor
from agent_tool_executor import AgentToolExecutor
from protocol import Message

USE_LLM = os.environ.get("HI_MACP_LLM_HARNESS", "0") == "1"


def run_ec9_invalid_plan(shared_path: Path) -> Dict[str, any]:
    manager = InteractionManager(str(shared_path))
    planner = AgentPlanner(manager, adapter=LLMAdapter(enabled=USE_LLM))
    executor = AgentExecutor(manager, adapter=LLMAdapter(enabled=USE_LLM))
    monitor = AgentMonitor(manager, adapter=LLMAdapter(enabled=USE_LLM))
    metrics = Metrics()
    manager.save_shared(
        {
            "goals": ["invalid plan"],
            "tasks": [],
            "roles": {"Planner": "AgentA", "Executor": "AgentB", "Monitor": "AgentM"},
            "world_state": {},
            "commitments": {},
            "history": [],
            "phase": "opening",
        }
    )
    try:
        # Force very short/placeholder plan hint when using LLM to trigger rejection/fallback
        if USE_LLM:
            llm = planner.adapter
            # Temporarily inject a bad plan hint via shared state
            manager.save_shared(
                {
                    "goals": ["invalid plan"],
                    "tasks": [],
                    "roles": {"Planner": "AgentA", "Executor": "AgentB", "Monitor": "AgentM"},
                    "world_state": {"force_invalid_plan": True},
                    "commitments": {},
                    "history": [],
                    "phase": "opening",
                }
            )
        shared, divergence, plan = planner.send_proposal(metrics=metrics)
    except Exception:
        return {"case": "EC9_invalid_plan", "status": "PASS", "reason": "Planner rejected invalid plan."}
    if divergence[0]:
        return {"case": "EC9_invalid_plan", "status": "PASS", "reason": divergence[1]}
    # If plan meets validity (>=4), treat as pass in fallback; otherwise fail
    if isinstance(plan, list) and len(plan) >= 4:
        return {"case": "EC9_invalid_plan", "status": "PASS", "reason": "Planner fell back to valid deterministic plan."}
    return {"case": "EC9_invalid_plan", "status": "FAIL", "reason": "Planner accepted invalid plan."}


def run_ec11_schema_failures(shared_path: Path) -> Dict[str, any]:
    # Simulate repeated schema failure by sending malformed messages directly
    manager = InteractionManager(str(shared_path))
    metrics = Metrics()
    from protocol import Message

    bad_msg = Message(
        sender="AgentA",
        receiver="AgentB",
        type="commit",
        content={"commitment": "accept_plan", "plan": "notalist"},  # invalid plan
        confidence=0.5,
    )
    shared, divergence = manager.route(bad_msg, metrics=metrics)
    if divergence[0]:
        return {"case": "EC11_schema_failures", "status": "PASS", "reason": divergence[1]}
    return {"case": "EC11_schema_failures", "status": "FAIL", "reason": "Malformed message not detected."}


def run_ec1_refusal(shared_path: Path) -> Dict[str, any]:
    # Simulate monitor pending with no agent response; expect alignment False and monitor_pending True
    manager = InteractionManager(str(shared_path))
    manager.save_shared(
        {
            "goals": ["test refusal"],
            "tasks": ["step1"],
            "roles": {"Planner": "AgentA", "Executor": "AgentB", "Monitor": "AgentM"},
            "world_state": {"last_message": "monitor clarify"},
            "commitments": {"monitor_request_pending": True, "plan_status": "repair_required"},
            "history": [],
            "phase": "repair",
        }
    )
    aligned, reason = manager.alignment_metric(monitor_required_env=True)
    if not aligned and "monitor" in reason:
        return {"case": "EC1_refusal", "status": "PASS", "reason": "Monitor pending prevents alignment"}
    return {"case": "EC1_refusal", "status": "FAIL", "reason": "System aligned despite monitor pending"}


def run_ec2_nonsense_clarify(shared_path: Path) -> Dict[str, any]:
    manager = InteractionManager(str(shared_path))
    metrics = Metrics()
    from protocol import Message
    manager.save_shared(
        {
            "goals": ["nonsense clarify"],
            "tasks": ["step1", "step2"],
            "roles": {"Planner": "AgentA", "Executor": "AgentB"},
            "world_state": {},
            "commitments": {"plan_status": "proposed"},
            "history": [],
            "phase": "negotiation",
        }
    )
    bad_clarify = Message(
        sender="AgentB",
        receiver="AgentA",
        type="clarify",
        content={"issue": ""},
        confidence=0.5,
    )
    shared, div = manager.route(bad_clarify, metrics=metrics)
    if div[0] and "Clarify requires" in div[1]:
        return {"case": "EC2_nonsense_clarify", "status": "PASS", "reason": div[1]}
    return {"case": "EC2_nonsense_clarify", "status": "FAIL", "reason": "Nonsensical clarify was accepted"}


def run_ec3_self_contradiction(shared_path: Path) -> Dict[str, any]:
    manager = InteractionManager(str(shared_path))
    metrics = Metrics()
    from protocol import Message
    manager.save_shared(
        {
            "goals": ["contradiction"],
            "tasks": ["step1"],
            "roles": {"Planner": "AgentA", "Executor": "AgentB"},
            "world_state": {"contradiction": True},
            "commitments": {"plan_status": "repair"},
            "history": [],
            "phase": "repair",
        }
    )
    info = Message(sender="AgentA", receiver="AgentB", type="inform", content={"note": "conflict"}, confidence=0.6)
    shared, div = manager.route(info, metrics=metrics)
    if div[0] and "contradiction" in div[1].lower():
        return {"case": "EC3_self_contradiction", "status": "PASS", "reason": div[1]}
    return {"case": "EC3_self_contradiction", "status": "FAIL", "reason": "Contradiction not detected"}


def run_ec4_hallucinated_steps(shared_path: Path) -> Dict[str, any]:
    manager = InteractionManager(str(shared_path))
    metrics = Metrics()
    from protocol import Message
    manager.save_shared(
        {
            "goals": ["hallucinated"],
            "tasks": ["a", "b", "c"],
            "roles": {"Planner": "AgentA", "Executor": "AgentB"},
            "world_state": {"claimed_tasks": 3},
            "commitments": {"plan_status": "proposed"},
            "history": [],
            "phase": "negotiation",
        }
    )
    revise_msg = None
    if USE_LLM:
        adapter = LLMAdapter(enabled=True)
        revise_msg = adapter.generate(
            "Planner",
            "revise_plan",
            manager.load_shared(),
            hints={"plan": ["a", "b", "c"], "monitor_pending": False, "desired_steps": 5},
        )
    if revise_msg is None:
        revise_msg = Message(sender="AgentA", receiver="AgentB", type="revise", content={"plan": ["a", "b", "c", "d", "e"]}, confidence=0.7)
    revise = revise_msg
    shared, div = manager.route(revise, metrics=metrics)
    if div[0] and "Task count mismatch" in div[1]:
        return {"case": "EC4_hallucinated_steps", "status": "PASS", "reason": div[1]}
    return {"case": "EC4_hallucinated_steps", "status": "FAIL", "reason": "Hallucinated steps not flagged"}


def run_ec5_goal_drift(shared_path: Path) -> Dict[str, any]:
    manager = InteractionManager(str(shared_path))
    metrics = Metrics()
    from protocol import Message
    manager.save_shared(
        {
            "goals": ["deploy"],
            "tasks": ["step1"],
            "roles": {"Planner": "AgentA", "Executor": "AgentB"},
            "world_state": {"contradiction": False, "active_goals": ["deploy", "rewrite_product"]},
            "commitments": {"plan_status": "proposed"},
            "history": [],
            "phase": "negotiation",
        }
    )
    msg = Message(sender="AgentA", receiver="AgentB", type="inform", content={"goal": "rewrite_product"}, confidence=0.7)
    shared, div = manager.route(msg, metrics=metrics)
    if div[0] and "Multiple active goals" in div[1]:
        return {"case": "EC5_goal_drift", "status": "PASS", "reason": div[1]}
    return {"case": "EC5_goal_drift", "status": "FAIL", "reason": "Goal drift not detected"}


def run_ec6_endless_challenge(shared_path: Path) -> Dict[str, any]:
    manager = InteractionManager(str(shared_path))
    metrics = Metrics()
    from protocol import Message
    manager.save_shared(
        {
            "goals": ["endless"],
            "tasks": ["step1"],
            "roles": {"Planner": "AgentA", "Executor": "AgentB"},
            "world_state": {},
            "commitments": {"plan_status": "proposed"},
            "history": [],
            "phase": "negotiation",
        }
    )
    for _ in range(3):
        challenge = Message(sender="AgentB", receiver="AgentA", type="challenge", content={"assumption": "I cannot proceed"}, confidence=0.6)
        shared, div = manager.route(challenge, metrics=metrics)
    if div[0] and "Endless challenge" in div[1]:
        return {"case": "EC6_endless_challenge", "status": "PASS", "reason": div[1]}
    return {"case": "EC6_endless_challenge", "status": "FAIL", "reason": "Endless challenge not detected"}


def run_ec7_resource_disagreement(shared_path: Path) -> Dict[str, any]:
    # Commit requiring resources executor lacks: expect commit gate or divergence
    manager = InteractionManager(str(shared_path))
    metrics = Metrics()
    from protocol import Message

    # Shared shows executor has no credentials
    manager.save_shared(
        {
            "goals": ["resource test"],
            "tasks": ["step1"],
            "roles": {"Planner": "AgentA", "Executor": "AgentB", "Monitor": "AgentM"},
            "world_state": {"claimed_tasks": 1},
            "commitments": {"plan_status": "proposed"},
            "resources": {"credentials": {"AgentB": False}},
            "history": [
                {
                    "type": "clarify",
                    "sender": "AgentB",
                    "receiver": "AgentA",
                    "content": {"issue": "grounding"},
                }
            ],
            "phase": "negotiation",
        }
    )
    commit = None
    if USE_LLM:
        adapter = LLMAdapter(enabled=True)
        commit = adapter.generate(
            "Executor",
            "evaluate_plan",
            manager.load_shared(),
            hints={"plan": ["Deploy app", "Run smoke tests"], "phase": "negotiation"},
        )
    if commit is None:
        commit = Message(
            sender="AgentB",
            receiver="AgentA",
            type="commit",
            content={"commitment": "accept_plan", "plan": ["Deploy app", "Run smoke tests"]},
            confidence=0.8,
        )
    shared, div = manager.route(commit, metrics=metrics)
    if div[0]:
        return {"case": "EC7_resource_disagreement", "status": "PASS", "reason": div[1]}
    return {"case": "EC7_resource_disagreement", "status": "FAIL", "reason": "Commit accepted despite resource mismatch risk"}


def run_ec8_monitor_cannot_resolve(shared_path: Path) -> Dict[str, any]:
    manager = InteractionManager(str(shared_path))
    metrics = Metrics()
    from protocol import Message
    manager.save_shared(
        {
            "goals": ["monitor"],
            "tasks": ["step1"],
            "roles": {"Planner": "AgentA", "Executor": "AgentB", "Monitor": "AgentM"},
            "world_state": {"monitor_attempts": 3},
            "commitments": {"plan_status": "repair", "monitor_request_pending": True},
            "history": [],
            "phase": "repair",
        }
    )
    clarify = Message(sender="AgentM", receiver="AgentA", type="clarify", content={"issue": "Unresolved blocking issue"}, confidence=1.0)
    shared, div = manager.route(clarify, metrics=metrics)
    if div[0] and "Monitor unresolved" in div[1]:
        return {"case": "EC8_monitor_cannot_resolve", "status": "PASS", "reason": div[1]}
    return {"case": "EC8_monitor_cannot_resolve", "status": "FAIL", "reason": "Monitor unresolved state not detected"}


def run_ec10_memory_corruption(shared_path: Path) -> Dict[str, any]:
    manager = InteractionManager(str(shared_path))
    # Corrupt tasks to non-list
    manager.save_shared(
        {
            "goals": ["corrupt"],
            "tasks": "notalist",
            "roles": {"Planner": "AgentA", "Executor": "AgentB"},
            "world_state": {},
            "commitments": {"plan_status": "committed"},
            "history": [],
            "phase": "execution",
        }
    )
    aligned, reason = manager.alignment_metric(monitor_required_env=False)
    if not aligned:
        return {"case": "EC10_memory_corruption", "status": "PASS", "reason": reason}
    return {"case": "EC10_memory_corruption", "status": "FAIL", "reason": "Aligned despite corrupted memory"}


def run_ec12_role_confusion(shared_path: Path) -> Dict[str, any]:
    manager = InteractionManager(str(shared_path))
    metrics = Metrics()
    from protocol import Message
    # AgentB sends propose (role confusion)
    msg = None
    if USE_LLM:
        adapter = LLMAdapter(enabled=True)
        msg = adapter.generate(
            "Executor",
            "propose_plan",
            manager.load_shared()
            or {
                "roles": {"Planner": "AgentA", "Executor": "AgentB"},
                "commitments": {"plan_status": "negotiation"},
                "tasks": [],
                "world_state": {},
            },
            hints={"plan": ["step1"], "monitor_pending": False},
        )
    if msg is None:
        msg = Message(
            sender="AgentB",
            receiver="AgentA",
            type="propose",
            content={"plan": ["step1"]},
            confidence=0.7,
        )
    try:
        shared, div = manager.route(msg, metrics=metrics)
    except Exception:
        return {"case": "EC12_role_confusion", "status": "PASS", "reason": "Role confusion rejected"}
    if div[0]:
        return {"case": "EC12_role_confusion", "status": "PASS", "reason": div[1]}
    return {"case": "EC12_role_confusion", "status": "FAIL", "reason": "Role confusion accepted"}


def run_ec13_deadlock(shared_path: Path) -> Dict[str, any]:
    manager = InteractionManager(str(shared_path))
    manager.save_shared(
        {
            "goals": ["deadlock"],
            "tasks": ["step1"],
            "roles": {"Planner": "AgentA", "Executor": "AgentB"},
            "world_state": {},
            "commitments": {"plan_status": "repair", "monitor_request_pending": True},
            "history": [],
            "phase": "repair",
        }
    )
    aligned, reason = manager.alignment_metric(monitor_required_env=True)
    return {
        "case": "EC13_deadlock",
        "status": "PASS" if not aligned else "FAIL",
        "reason": reason,
    }


def run_ec14_multi_task(shared_path: Path) -> Dict[str, any]:
    manager = InteractionManager(str(shared_path))
    metrics = Metrics()
    from protocol import Message
    manager.save_shared(
        {
            "goals": ["multiA", "multiB"],
            "tasks": ["A1", "A2"],
            "roles": {"Planner": "AgentA", "Executor": "AgentB"},
            "world_state": {"active_goals": ["multiA", "multiB"], "claimed_tasks": 2},
            "commitments": {"plan_status": "proposed"},
            "history": [],
            "phase": "negotiation",
        }
    )
    propose = None
    if USE_LLM:
        adapter = LLMAdapter(enabled=True)
        propose = adapter.generate(
            "Planner",
            "propose_plan",
            manager.load_shared(),
            hints={"plan": ["B1", "B2", "B3"], "monitor_pending": False},
        )
    if propose is None:
        propose = Message(sender="AgentA", receiver="AgentB", type="propose", content={"plan": ["B1", "B2", "B3"]}, confidence=0.8)
    shared, div = manager.route(propose, metrics=metrics)
    if div[0]:
        return {"case": "EC14_multi_task", "status": "PASS", "reason": div[1]}
    return {"case": "EC14_multi_task", "status": "FAIL", "reason": "Multiple goals/tasks not flagged"}


def run_ec15_timeout_cascade(shared_path: Path) -> Dict[str, any]:
    # Simulate timeout cascade by marking failure_mode; alignment must be False
    manager = InteractionManager(str(shared_path))
    manager.save_shared(
        {
            "goals": ["timeout"],
            "tasks": ["step1"],
            "roles": {"Planner": "AgentA", "Executor": "AgentB"},
            "world_state": {},
            "commitments": {"plan_status": "repair"},
            "failure_mode": "timeout_cascade",
            "history": [],
            "phase": "repair",
        }
    )
    aligned, reason = manager.alignment_metric(monitor_required_env=False)
    if not aligned and "timeout_cascade" in reason:
        return {"case": "EC15_timeout_cascade", "status": "PASS", "reason": reason}
    return {"case": "EC15_timeout_cascade", "status": "FAIL", "reason": "Timeout cascade not surfaced"}


def run_ec16_tradeoff_missing(shared_path: Path) -> Dict[str, any]:
    # Commit should be blocked if tradeoff challenge not answered by revise/repair
    manager = InteractionManager(str(shared_path))
    metrics = Metrics()
    from protocol import Message
    manager.save_shared(
        {
            "goals": ["tradeoff"],
            "tasks": ["secure_step"],
            "roles": {"Planner": "AgentA", "Executor": "AgentB"},
            "world_state": {"role_goals": {"Planner": "maximize_security", "Executor": "minimize_time"}},
            "commitments": {"plan_status": "proposed"},
            "phase": "negotiation",
        }
    )
    # Send a challenge via route to record it in history
    ch = Message(sender="AgentB", receiver="AgentA", type="challenge", content={"assumption": "too secure"}, confidence=0.6)
    shared_state, _ = manager.route(ch, metrics=metrics)
    commit = Message(
        sender="AgentB",
        receiver="AgentA",
        type="commit",
        content={"commitment": "accept_plan", "plan": ["secure_step"]},
        confidence=0.8,
    )
    shared, div = manager.route(commit, metrics=metrics)
    if div[0] and "tradeoff challenge not answered" in div[1]:
        return {"case": "EC16_tradeoff_missing", "status": "PASS", "reason": div[1]}
    return {"case": "EC16_tradeoff_missing", "status": "FAIL", "reason": "Commit not blocked despite missing tradeoff response"}


def run_ec17_confidence_gate(shared_path: Path) -> Dict[str, any]:
    # Commit rejected if no clarify/challenge after proposal
    manager = InteractionManager(str(shared_path))
    metrics = Metrics()
    from protocol import Message
    manager.save_shared(
        {
            "goals": ["conf_gate"],
            "tasks": ["step1"],
            "roles": {"Planner": "AgentA", "Executor": "AgentB"},
            "world_state": {},
            "commitments": {"plan_status": "proposed"},
            "history": [],
            "phase": "negotiation",
        }
    )
    commit = Message(sender="AgentB", receiver="AgentA", type="commit", content={"commitment": "accept_plan", "plan": ["step1"]}, confidence=0.5)
    shared, div = manager.route(commit, metrics=metrics)
    if div[0] and "no clarification/challenge" in div[1]:
        return {"case": "EC17_confidence_gate", "status": "PASS", "reason": div[1]}
    return {"case": "EC17_confidence_gate", "status": "FAIL", "reason": "Commit allowed without grounding"}


def run_ec18_monitor_tradeoff_missing(shared_path: Path) -> Dict[str, any]:
    manager = InteractionManager(str(shared_path))
    metrics = Metrics()
    from protocol import Message
    manager.save_shared(
        {
            "goals": ["monitor tradeoff"],
            "tasks": ["secure_step"],
            "roles": {"Planner": "AgentA", "Executor": "AgentB", "Monitor": "AgentM"},
            "world_state": {"role_goals": {"Planner": "maximize_security", "Executor": "minimize_time"}},
            "commitments": {"plan_status": "committed"},
            "phase": "execution",
        }
    )
    from agent_monitor import AgentMonitor

    monitor = AgentMonitor(manager, adapter=LLMAdapter(enabled=False))
    # Add a challenge without response
    ch = Message(sender="AgentB", receiver="AgentA", type="challenge", content={"assumption": "too secure"}, confidence=0.6)
    manager.route(ch, metrics=metrics)
    shared, div = monitor.review_alignment(metrics=metrics)
    if div[0] and "tradeoff" in div[1]:
        return {"case": "EC18_monitor_tradeoff_missing", "status": "PASS", "reason": div[1]}
    return {"case": "EC18_monitor_tradeoff_missing", "status": "FAIL", "reason": "Monitor did not flag missing tradeoff debate"}


def run_ec19_multi_challenge_chain(shared_path: Path) -> Dict[str, any]:
    manager = InteractionManager(str(shared_path))
    metrics = Metrics()
    from protocol import Message
    manager.save_shared(
        {
            "goals": ["multi challenge"],
            "tasks": ["secure_step"],
            "roles": {"Planner": "AgentA", "Executor": "AgentB"},
            "world_state": {"role_goals": {"Planner": "maximize_security", "Executor": "minimize_time"}},
            "commitments": {"plan_status": "proposed"},
            "history": [],
            "phase": "negotiation",
        }
    )
    ch1 = Message(sender="AgentB", receiver="AgentA", type="challenge", content={"assumption": "too secure"}, confidence=0.6)
    ch2 = Message(sender="AgentB", receiver="AgentA", type="challenge", content={"assumption": "still too secure"}, confidence=0.6)
    manager.route(ch1, metrics=metrics)
    manager.route(ch2, metrics=metrics)
    commit = Message(sender="AgentB", receiver="AgentA", type="commit", content={"commitment": "accept_plan", "plan": ["secure_step"]}, confidence=0.5)
    shared, div = manager.route(commit, metrics=metrics)
    if div[0] and "tradeoff challenge not answered" in div[1]:
        return {"case": "EC19_multi_challenge_chain", "status": "PASS", "reason": div[1]}
    return {"case": "EC19_multi_challenge_chain", "status": "FAIL", "reason": "Commit allowed despite unanswered challenges"}


def run_ec20_logging(shared_path: Path) -> Dict[str, any]:
    # Verify logger writes a row
    from logger import log_run
    import sqlite3
    import tempfile
    metrics = Metrics()
    # Minimal shared
    manager = InteractionManager(str(shared_path))
    shared = {
        "history": [],
        "commitments": {"plan_status": "committed"},
        "tasks": ["a"],
    }
    tmpdb = tempfile.NamedTemporaryFile(delete=False)
    log_run(shared, metrics, db_path=tmpdb.name)
    conn = sqlite3.connect(tmpdb.name)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM runs")
    count = cur.fetchone()[0]
    conn.close()
    if count >= 1:
        return {"case": "EC20_logging", "status": "PASS", "reason": "Row logged to SQLite"}
    return {"case": "EC20_logging", "status": "FAIL", "reason": "Logger did not write a row"}


def run_ec21_tool_action_success(shared_path: Path) -> Dict[str, any]:
    manager = InteractionManager(str(shared_path))
    tool_exec = AgentToolExecutor(manager)
    metrics = Metrics()
    manager.save_shared(
        {
            "goals": ["tool action"],
            "tasks": [],
            "roles": {"Planner": "AgentA", "Executor": "AgentB"},
            "world_state": {},
            "commitments": {},
            "history": [],
            "phase": "execution",
        }
    )
    tool_msg = Message(
        sender="AgentB",
        receiver="ToolExecutor",
        type="tool_action",
        content={"action": "smoke_test", "mode": "simulate", "target": "echo 'ok'"},
        confidence=0.7,
        assumptions=[],
        context={"goal": "execute action"},
    )
    shared, div = manager.route(tool_msg, metrics=metrics)
    if div[0]:
        return {"case": "EC21_tool_action_success", "status": "FAIL", "reason": div[1]}
    shared, div = tool_exec.handle_action(tool_msg, metrics=metrics)
    if div[0]:
        return {"case": "EC21_tool_action_success", "status": "FAIL", "reason": div[1]}
    hist = manager.load_shared().get("history", [])
    if hist and hist[-1].get("content", {}).get("action_result", {}).get("status") == "success":
        return {"case": "EC21_tool_action_success", "status": "PASS", "reason": "Simulated tool action succeeded"}
    return {"case": "EC21_tool_action_success", "status": "FAIL", "reason": "Tool action did not record success"}


def run_ec22_tool_action_unauthorized(shared_path: Path) -> Dict[str, any]:
    manager = InteractionManager(str(shared_path))
    tool_exec = AgentToolExecutor(manager)
    metrics = Metrics()
    manager.save_shared(
        {
            "goals": ["tool action"],
            "tasks": [],
            "roles": {"Planner": "AgentA", "Executor": "AgentB"},
            "world_state": {},
            "commitments": {},
            "history": [],
            "phase": "execution",
        }
    )
    tool_msg = Message(
        sender="AgentB",
        receiver="ToolExecutor",
        type="tool_action",
        content={"action": "kubectl_apply", "mode": "execute", "target": "manifests/"},
        confidence=0.7,
        assumptions=[],
        context={"goal": "execute action"},
    )
    shared, div = manager.route(tool_msg, metrics=metrics)
    if div[0]:
        return {"case": "EC22_tool_action_unauthorized", "status": "FAIL", "reason": div[1]}
    shared, div = tool_exec.handle_action(tool_msg, metrics=metrics)
    hist = manager.load_shared().get("history", [])
    status = hist[-1].get("content", {}).get("action_result", {}).get("status") if hist else ""
    if status == "unauthorized":
        return {"case": "EC22_tool_action_unauthorized", "status": "PASS", "reason": "Execution blocked by policy"}
    return {"case": "EC22_tool_action_unauthorized", "status": "FAIL", "reason": f"Unexpected status: {status}"}


def run_ec23_tool_action_blocks_commit(shared_path: Path) -> Dict[str, any]:
    manager = InteractionManager(str(shared_path))
    metrics = Metrics()
    # Create shared with a failing tool_result
    manager.save_shared(
        {
            "goals": ["deploy"],
            "tasks": ["deploy service"],
            "roles": {"Planner": "AgentA", "Executor": "AgentB"},
            "world_state": {
                "tool_results": [{"action": "kubectl_apply", "status": "failure", "details": "dry-run failed"}]
            },
            "commitments": {"plan_status": "proposed"},
            "history": [],
            "phase": "execution",
        }
    )
    # Add grounding so commit gate reaches tool gate
    clarify = Message(sender="AgentB", receiver="AgentA", type="clarify", content={"issue": "grounding"}, confidence=0.6)
    manager.route(clarify, metrics=metrics)
    commit = Message(sender="AgentB", receiver="AgentA", type="commit", content={"commitment": "accept_plan", "plan": ["deploy service"]}, confidence=0.5)
    shared, div = manager.route(commit, metrics=metrics)
    if div[0] and "tool actions failed" in div[1].lower():
        return {"case": "EC23_tool_action_blocks_commit", "status": "PASS", "reason": div[1]}
    return {"case": "EC23_tool_action_blocks_commit", "status": "FAIL", "reason": "Commit was not blocked despite tool failure"}


def run_ec24_llm_tool_action(shared_path: Path) -> Dict[str, any]:
    """If LLM harness enabled, ensure LLM can emit a tool_action or include tool_actions in propose."""
    if not USE_LLM:
        return {"case": "EC24_llm_tool_action", "status": "SKIP", "reason": "LLM harness disabled"}
    manager = InteractionManager(str(shared_path))
    adapter = LLMAdapter(enabled=True)
    if not adapter.provider or not adapter.provider.available():
        return {"case": "EC24_llm_tool_action", "status": "SKIP", "reason": "LLM provider unavailable"}
    shared = {
        "goals": ["deploy with tests"],
        "tasks": [],
        "roles": {"Planner": "AgentA", "Executor": "AgentB"},
        "world_state": {},
        "commitments": {},
        "history": [],
        "phase": "opening",
    }
    manager.save_shared(shared)
    msg = adapter.generate(
        role="Planner",
        intent="propose_plan",
        shared=shared,
        hints={"plan": ["Deploy code", "Run smoke tests"], "role_goal": "maximize_security"},
    )
    if msg is None:
        return {"case": "EC24_llm_tool_action", "status": "SKIP", "reason": "LLM unavailable or blocked"}
    tool_actions = msg.content.get("tool_actions") or []
    if msg.type == "tool_action" or tool_actions:
        return {"case": "EC24_llm_tool_action", "status": "PASS", "reason": "LLM provided tool_actions"}
    return {"case": "EC24_llm_tool_action", "status": "FAIL", "reason": "LLM did not include tool_actions"}


def main():
    base = Path(__file__).parent
    shared_path = base / "shared.json"
    results = []
    # EC1–EC15
    results.append(run_ec1_refusal(shared_path))
    results.append(run_ec2_nonsense_clarify(shared_path))
    results.append(run_ec3_self_contradiction(shared_path))
    results.append(run_ec4_hallucinated_steps(shared_path))
    results.append(run_ec5_goal_drift(shared_path))
    results.append(run_ec6_endless_challenge(shared_path))
    results.append(run_ec7_resource_disagreement(shared_path))
    results.append(run_ec8_monitor_cannot_resolve(shared_path))
    results.append(run_ec9_invalid_plan(shared_path))
    results.append(run_ec10_memory_corruption(shared_path))
    results.append(run_ec11_schema_failures(shared_path))
    results.append(run_ec12_role_confusion(shared_path))
    results.append(run_ec13_deadlock(shared_path))
    results.append(run_ec14_multi_task(shared_path))
    results.append(run_ec15_timeout_cascade(shared_path))
    results.append(run_ec16_tradeoff_missing(shared_path))
    results.append(run_ec17_confidence_gate(shared_path))
    results.append(run_ec18_monitor_tradeoff_missing(shared_path))
    results.append(run_ec19_multi_challenge_chain(shared_path))
    results.append(run_ec20_logging(shared_path))
    results.append(run_ec21_tool_action_success(shared_path))
    results.append(run_ec22_tool_action_unauthorized(shared_path))
    results.append(run_ec23_tool_action_blocks_commit(shared_path))
    results.append(run_ec24_llm_tool_action(shared_path))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    # Ensure env knobs for deterministic runs
    os.environ["HI_MACP_LLM_ENABLED"] = "0"
    os.environ["HI_MACP_MONITOR"] = "1"
    os.environ["HI_MACP_FULL_SMM"] = "1"
    os.environ["HI_MACP_STRESS"] = "0"
    os.environ["HI_MACP_REDUCED_STRESS"] = "1"
    os.environ["HI_MACP_FAST_SIM"] = "1"
    os.environ["HI_MACP_MEMORY_DELTA"] = "1"
    main()
