import json
import os
from pathlib import Path

from agent_planner import AgentPlanner
from agent_executor import AgentExecutor
from interaction_manager import InteractionManager
from agent_monitor import AgentMonitor
from agent_knowledge import AgentKnowledge
from agent_tool_executor import AgentToolExecutor
from llm_adapter import LLMAdapter
from metrics import Metrics
from logger import log_run
from dag_executor import DagExecutor
from policy_validator import PolicyValidator
from dag_runner import DagRunner
from execution_log import ExecutionLog
from protocol import Message
import importlib


def _load_yaml_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except Exception:
        yaml = None
    try:
        if yaml and path.suffix in {".yaml", ".yml"}:
            return yaml.safe_load(path.read_text()) or {}
        return json.loads(path.read_text())
    except Exception:
        return {}


def load_project_config() -> dict:
    """Load hi.yaml + env config if present. Respects HI_MACP_CONFIG and HI_MACP_ENV_NAME."""
    cfg_path = os.environ.get("HI_MACP_CONFIG") or "hi.yaml"
    cfg_data = _load_yaml_json(Path(cfg_path))
    if not cfg_data:
        return {}
    base = Path(cfg_path).resolve().parent
    env_name = os.environ.get("HI_MACP_ENV_NAME") or cfg_data.get("default_env")
    env_cfg = {}
    if env_name:
        env_file = base / "envs" / f"{env_name}.yaml"
        if env_file.exists():
            env_cfg = _load_yaml_json(env_file)
    policy_file = cfg_data.get("policy", {}).get("file")
    if policy_file:
        pf_path = (base / policy_file).resolve()
        os.environ.setdefault("HI_MACP_POLICY_FILE", str(pf_path))
        os.environ.setdefault("HI_MACP_POLICY_ENFORCE", "1")
    return {
        "config_dir": str(base),
        "env": env_name,
        "env_cfg": env_cfg,
        "policy_file": policy_file,
    }


def build_initial_shared(
    full_smm: bool,
    stress: bool,
    resources_override: dict | None = None,
    tool_caps_override: dict | None = None,
    role_goals_override: dict | None = None,
    prompt_overrides: dict | None = None,
) -> dict:
    # Stress task and conflicting goals/resources
    goal_override = os.environ.get("HI_MACP_GOAL")
    base_goals = [
        goal_override
        or "develop a 12-step deployment protocol for a distributed microservice system with blue-green rollout, health checks, canary strategies, rollback conditions, security key rotation, and network policy updates"
    ]
    base_tasks = [
        "Baseline inventory of microservices and dependencies",
        "Define blue/green environments and traffic switch rules",
        "Provision staging and production clusters",
        "Configure service mesh and network policies",
        "Set up secrets management and key rotation plan",
        "Build container images with SBOM and signing",
        "Deploy to blue environment with health checks",
        "Run canary subset with synthetic and real traffic",
        "Monitor metrics/alerts and error budgets",
        "Execute controlled traffic shift to blue",
        "Define rollback triggers and playbooks",
        "Post-deploy security audit and key rotation",
    ]
    if not full_smm:
        base = {
            "goals": base_goals,
            "tasks": [],
            "roles": {"Planner": "AgentA", "Executor": "AgentB"},
            "world_state": {},
            "commitments": {},
            "history": [],
            "phase": "opening",
        }
        if tool_caps_override:
            base.setdefault("world_state", {})["tool_capabilities"] = tool_caps_override
        if role_goals_override:
            base.setdefault("world_state", {})["role_goals"] = role_goals_override
        if prompt_overrides:
            base.setdefault("world_state", {})["prompt_overrides"] = prompt_overrides
        if resources_override:
            base["resources"] = resources_override
        return base
    resources = resources_override or {
        "credentials": {"AgentA": True, "AgentB": False},  # executor lacks root
        "network_rights": {"AgentA": True, "AgentB": False},
        "dns_knowledge": {"AgentA": True, "AgentB": False},
    }
    beliefs = {
        "AgentA": {
            "knowledge": ["security_best_practices", "dns_setup"],
            "uncertainty": 0.25,
            "assumptions": ["Executor can escalate privileges"],
        },
        "AgentB": {
            "knowledge": ["fast_deploy_patterns"],
            "uncertainty": 0.3,
            "assumptions": ["Planner will handle security hardening"],
        },
        "AgentM": {
            "knowledge": ["audit_requirements"],
            "uncertainty": 0.2,
            "assumptions": ["All assumptions must be justified"],
        },
    }
    world_state = {
        "role_goals": {
            "Planner": "maximize_security",
            "Executor": "minimize_time",
            "Monitor": "enforce_justification",
        }
    }
    if stress:
        world_state["claimed_tasks"] = 8  # mismatch
        beliefs["AgentA"]["assumptions"].append("Monitor will auto-approve")
        beliefs["AgentB"]["assumptions"].append("Planner handles DNS and security")
    base = {
        "goals": base_goals,
        "tasks": [] if stress else base_tasks[:4],
        "roles": {"Planner": "AgentA", "Executor": "AgentB", "Monitor": "AgentM"},
        "world_state": world_state,
        "beliefs": beliefs,
        "resources": resources,
        "commitments": {},
        "history": [],
        "phase": "opening",
    }
    if tool_caps_override:
        base.setdefault("world_state", {})["tool_capabilities"] = tool_caps_override
    if role_goals_override:
        base.setdefault("world_state", {})["role_goals"] = role_goals_override
    if prompt_overrides:
        base.setdefault("world_state", {})["prompt_overrides"] = prompt_overrides
    return base


def reset_shared(
    shared_path: Path,
    resources_override: dict | None = None,
    tool_caps_override: dict | None = None,
    role_goals_override: dict | None = None,
    prompt_overrides: dict | None = None,
) -> None:
    full_smm = os.environ.get("HI_MACP_FULL_SMM", "1") == "1"
    stress = os.environ.get("HI_MACP_STRESS", "0") == "1"
    if os.environ.get("HI_MACP_REDUCED_STRESS", "0") == "1":
        # Reduced stress: shorter plan and goals for faster LLM runs
        reduced = {
            "goals": ["develop a 6-step rollout with blue/green, canary, rollback"],
            "tasks": [],
            "roles": {"Planner": "AgentA", "Executor": "AgentB", "Monitor": "AgentM"},
            "world_state": {},
            "beliefs": {},
            # Reduced-stress still keeps capability mismatch unless bypassed via env
            "resources": {"credentials": {"AgentA": True, "AgentB": False}},
            "commitments": {},
            "history": [],
            "phase": "opening",
        }
        shared_path.write_text(json.dumps(reduced, indent=2))
        return
    shared_path.write_text(
        json.dumps(
            build_initial_shared(
                full_smm,
                stress,
                resources_override,
                tool_caps_override,
                role_goals_override=role_goals_override,
                prompt_overrides=prompt_overrides,
            ),
            indent=2,
        )
    )


def main() -> None:
    base_path = Path(__file__).parent
    shared_path = base_path / "shared.json"
    project_cfg = load_project_config()
    env_cfg = project_cfg.get("env_cfg", {}) if project_cfg else {}
    tool_caps_override = env_cfg.get("tool_capabilities") if isinstance(env_cfg, dict) else None
    tool_paths = tool_caps_override.get("tools") if isinstance(tool_caps_override, dict) else None
    resources_override = None
    if env_cfg.get("credentials"):
        resources_override = {"credentials": {"AgentA": True, "AgentB": False}}
        resources_override["credentials"].update(env_cfg.get("credentials", {}))
    role_goals_override = env_cfg.get("agent_goals") if isinstance(env_cfg, dict) else None
    prompt_overrides = env_cfg.get("prompt_overrides") if isinstance(env_cfg, dict) else None
    reset_shared(
        shared_path,
        resources_override=resources_override,
        tool_caps_override=tool_caps_override,
        role_goals_override=role_goals_override,
        prompt_overrides=prompt_overrides,
    )

    manager = InteractionManager(str(shared_path))
    adapter = LLMAdapter(enabled=os.environ.get("HI_MACP_LLM_ENABLED", "0") == "1")  # set to "1" to enable LLM
    metrics = Metrics()

    monitor_enabled = os.environ.get("HI_MACP_MONITOR", "1") == "1"
    fast_sim = os.environ.get("HI_MACP_FAST_SIM", "0") == "1"
    if fast_sim:
        # Fast sim: monitor off by default to reduce negotiation overhead
        monitor_enabled = False
        os.environ["HI_MACP_MONITOR"] = "0"

    # Apply allow_execute override from env tool capabilities
    if tool_caps_override and isinstance(tool_caps_override, dict):
        allow_exec = tool_caps_override.get("allow_execute")
        if allow_exec is not None:
            os.environ["HI_MACP_ALLOW_EXECUTE"] = "1" if allow_exec else "0"

    planner = AgentPlanner(manager, adapter=adapter)
    executor = AgentExecutor(manager, adapter=adapter)
    monitor = AgentMonitor(manager, adapter=adapter) if monitor_enabled else None
    tool_exec = AgentToolExecutor(manager, tool_paths=tool_paths, allow_execute=(tool_caps_override or {}).get("allow_execute"))
    knowledge = AgentKnowledge(manager)
    validator = PolicyValidator()
    dag_cfg = env_cfg.get("dag", {}) if isinstance(env_cfg, dict) else {}
    dag_runner = DagRunner(manager, tool_exec)
    exec_logger = ExecutionLog(base_dir=base_path / "logs")
    # Persist capability profile
    shared_cap = manager.load_shared()
    shared_cap.setdefault("world_state", {})["tool_capabilities"] = tool_exec.capabilities()
    if env_cfg.get("github"):
        shared_cap["world_state"]["github"] = env_cfg.get("github")
    manager.save_shared(shared_cap)
    # Inject best practices from knowledge agent (non-blocking)
    knowledge.inject_best_practices(metrics=metrics)

    print("HI-MACP MVP demo starting...")

    shared, divergence, plan = planner.send_proposal(metrics=metrics)
    knowledge.enforce_on_plan(plan, metrics=metrics)
    # Policy validation on structured plan/tool actions
    tool_actions = shared.get("world_state", {}).get("tool_actions", [])
    policies_ok, policy_issues = validator.validate(plan, tool_actions)
    if not policies_ok:
        print(f"Policy violations detected: {policy_issues}")
        # Emit a knowledge repair to surface issues
        knowledge_enforcement = Message(
            sender="AgentK",
            receiver="all",
            type="repair",
            content={"conflict": f"Policy violations: {policy_issues}", "tool_actions": tool_actions},
            confidence=0.7,
            assumptions=["Planner/Executor will revise to satisfy policies"],
            context={"goal": shared.get("goals", ["create plan"])[0] if shared.get("goals") else "create plan", "phase": "repair"},
        )
        shared, divergence = manager.route(knowledge_enforcement, metrics=metrics)
    manager.save_shared(shared)
    print(f"Planner -> Executor propose: {len(plan)} steps. Divergence={divergence}")
    if divergence[0]:
        print(f"Divergence detected after propose: {divergence[1]}")
        if "Task count mismatch" not in divergence[1]:
            return
        else:
            # Proceed after auto-repair for task mismatch
            shared = manager.load_shared()

    current_plan = plan
    # Build DAG batches for potential ordered execution/simulation
    dag_batches, unresolved = DagExecutor(current_plan).resolve()
    shared["world_state"]["dag_batches"] = dag_batches
    shared["world_state"]["dag_unresolved"] = unresolved
    if unresolved:
        print(f"DAG unresolved nodes: {unresolved}")
    manager.save_shared(shared)
    # Start execution log
    exec_logger.start_run(plan=current_plan, dag_batches=dag_batches, history=manager.load_shared().get("history", []))
    shared, divergence, action = executor.evaluate_plan(plan, metrics=metrics)
    print(f"Executor action: {action}. Divergence={divergence}")
    if divergence[0]:
        print(f"Divergence detected after executor action: {divergence[1]}")
    # Enforce tool best practices based on actions
    knowledge.enforce_on_actions(metrics=metrics)

    def force_grounding_if_needed(shared_state, current_plan):
        # Grounding is now agent-driven; do not auto-inject clarify/challenge.
        return shared_state, (False, "")

    shared, div_ground = force_grounding_if_needed(shared, plan)
    if div_ground[0]:
        print(f"Divergence detected during forced grounding: {div_ground[1]}")
    if action == "clarify":
        issue = ""
        clarifications = shared.get("world_state", {}).get("clarifications", [])
        if clarifications:
            issue = clarifications[-1]
        shared, divergence, refined_plan = planner.respond_to_clarify(issue, plan, metrics=metrics)
        print(f"Planner responded to clarify with refined plan ({len(refined_plan)} steps). Divergence={divergence}")
        if divergence[0]:
            print(f"Divergence detected after clarify: {divergence[1]}")
        current_plan = refined_plan
        shared, divergence, action = executor.evaluate_plan(refined_plan, metrics=metrics)
        print(f"Executor follow-up action: {action}. Divergence={divergence}")
        if divergence[0]:
            print(f"Divergence detected after follow-up: {divergence[1]}")
    # If last message is challenge, force planner compromise revise before any commit
    history = shared.get("history", [])
    if history and history[-1].get("type") == "challenge":
        print("Detected challenge; forcing planner compromise revision.")
        shared, divergence = planner.respond_to_challenge("tradeoff challenge", current_plan, metrics=metrics)
        print(f"Planner compromise revision divergence={divergence}")
        current_plan = shared.get("tasks", current_plan)
        if divergence[0]:
            print(f"Divergence detected after compromise revision: {divergence[1]}")
    # Forced grounding if still no clarify/challenge and plan_status proposed
    def force_grounding_if_needed(shared_state, current_plan_local):
        # Grounding is now agent-driven; do not auto-inject clarify/challenge.
        return shared_state, (False, "")

    shared, div_ground = force_grounding_if_needed(shared, current_plan)
    if div_ground[0]:
        print(f"Divergence detected during forced grounding: {div_ground[1]}")

    def handle_monitor_pending(shared_state, current_plan):
        if not shared_state.get("commitments", {}).get("monitor_request_pending"):
            return shared_state
        print("Monitor request pending — forcing Planner to respond...")
        monitor_issues = shared_state.get("world_state", {}).get("repairs", []) or shared_state.get("world_state", {}).get("clarifications", [])
        issue = monitor_issues[-1] if monitor_issues else "Monitor requested action."
        new_shared, divergence, new_plan = planner.respond_to_monitor(issue, current_plan, metrics=metrics)
        print(f"Planner monitor response divergence={divergence}")
        if divergence[0]:
            print(f"Divergence detected after planner monitor response: {divergence[1]}")
        if not new_shared.get("commitments", {}).get("monitor_request_pending"):
            return new_shared
        print("Planner response insufficient; forcing Executor to respond...")
        new_shared, divergence, _ = executor.respond_to_monitor(issue, new_plan, metrics=metrics)
        print(f"Executor monitor response divergence={divergence}")
        if divergence[0]:
            print(f"Divergence detected after executor monitor response: {divergence[1]}")
        return new_shared

    shared = handle_monitor_pending(shared, locals().get("refined_plan", plan))

    aligned, reason = manager.alignment_metric(monitor_required_env=monitor_enabled)
    print(f"Alignment metric before reconciliation: {aligned} ({reason})")

    final_plan = locals().get("refined_plan", current_plan)
    reconciled_once = False
    if not aligned:
        if shared.get("commitments", {}).get("plan_status") in {"revised", "committed"} and shared.get("phase") not in {"repair", "clarifying"}:
            shared, divergence = executor.reconcile_commit(final_plan, metrics=metrics)
            print(f"Reconciliation commit sent. Divergence={divergence}")
            if divergence[0]:
                print(f"Divergence detected during reconciliation: {divergence[1]}")
            else:
                reconciled_once = True
        else:
            print("Skipping reconciliation commit: plan not ready (phase/plan_status).")

    # Monitor review/confirmation and forced responses (loop)
    if monitor_enabled:
        for _ in range(3):
            shared, divergence = monitor.review_alignment(metrics=metrics)
            print(f"Monitor review divergence={divergence}")
            if divergence[0]:
                print(f"Divergence detected during monitor review: {divergence[1]}")
            shared = handle_monitor_pending(shared, final_plan)
            # continue loop to allow monitor to confirm after responses
            if shared.get("commitments", {}).get("monitor_confirmed") and not shared.get("commitments", {}).get("monitor_request_pending"):
                break

    # Attempt reconciliation again if still unaligned and monitor cleared
    aligned, reason = manager.alignment_metric(monitor_required_env=monitor_enabled)
    if not aligned and not shared.get("commitments", {}).get("monitor_request_pending") and not reconciled_once:
        if shared.get("commitments", {}).get("plan_status") in {"revised", "committed"} and shared.get("phase") not in {"repair", "clarifying"}:
            shared, divergence = executor.reconcile_commit(final_plan, metrics=metrics)
            print(f"Reconciliation commit (post-monitor) sent. Divergence={divergence}")
            if divergence[0]:
                print(f"Divergence detected during post-monitor reconciliation: {divergence[1]}")
            aligned, reason = manager.alignment_metric(monitor_required_env=monitor_enabled)
            if monitor_enabled and not shared.get("commitments", {}).get("monitor_request_pending"):
                shared, divergence = monitor.review_alignment(metrics=metrics)
                print(f"Monitor review after post-monitor reconciliation divergence={divergence}")
                if divergence[0]:
                    print(f"Divergence detected during monitor review after post-monitor reconciliation: {divergence[1]}")
                aligned, reason = manager.alignment_metric(monitor_required_env=monitor_enabled)
        else:
            print("Skipping post-monitor reconciliation: plan not ready (phase/plan_status).")

    print(f"Alignment metric after reconciliation: {aligned} ({reason})")

    # Execute tool_actions if present (default simulate/dry-run). Failure will surface via monitor unresolved.
    def execute_tool_actions(shared_state):
        actions = shared_state.get("world_state", {}).get("tool_actions") or []
        if not actions:
            return shared_state
        plan_status_local = shared_state.get("commitments", {}).get("plan_status")
        phase_local = shared_state.get("phase")
        if plan_status_local in {"repair", "repair_required", "clarifying"} or phase_local in {"repair", "clarifying"}:
            return shared_state
        if phase_local in {"closing", "execution"} and shared_state.get("commitments", {}).get("monitor_confirmed"):
            return shared_state
        results = []
        for action in actions:
            tool_msg = Message(
                sender="AgentB",
                receiver="ToolExecutor",
                type="tool_action",
                content=action,
                confidence=0.7,
                assumptions=[f"mode={action.get('mode', 'simulate')}"],
                context={"goal": "execute action"},
            )
            shared_local, div = manager.route(tool_msg, metrics=metrics)
            if div[0]:
                print(f"Tool action divergence: {div[1]}")
            shared_local, div = tool_exec.handle_action(tool_msg, metrics=metrics)
            if div[0]:
                print(f"Tool executor reported divergence: {div[1]}")
            # capture latest shared after handling
            shared_state = manager.load_shared()
            result = shared_state.get("history", [])[-1].get("content", {}).get("action_result")
            if result:
                results.append(result)
        shared_state.setdefault("world_state", {})["tool_results"] = results
        manager.save_shared(shared_state)
        return shared_state

    shared = execute_tool_actions(shared)

    # Execute DAG batches (simulate/dry-run) after reconciliation if aligned; default ON
    if os.environ.get("HI_MACP_RUN_DAG", "1") == "1":
        shared_state = manager.load_shared()
        plan_status_local = shared_state.get("commitments", {}).get("plan_status")
        if plan_status_local in {"repair", "repair_required", "clarifying"}:
            print("Skipping DAG execution: plan not ready (repair/clarifying).")
        else:
            if shared_state.get("commitments", {}).get("monitor_confirmed"):
                print("Skipping DAG execution: already monitor confirmed/aligned.")
                manager.save_shared(shared_state)
                return
            print("\nExecuting DAG batches (simulate)...")
            structured_steps = manager.load_shared().get("tasks", [])
            rollback_on_failure = dag_cfg.get("rollback", True)
            retries = dag_cfg.get("retries", 0)
            parallel = dag_cfg.get("parallel", False)
            dag_results, dag_failures = dag_runner.run(
                structured_steps,
                metrics=metrics,
                rollback_on_failure=rollback_on_failure,
                retries=retries,
                parallel=parallel,
            )
            shared_state = manager.load_shared()
            shared_state.setdefault("world_state", {})["dag_results"] = dag_results
            if dag_failures:
                shared_state.setdefault("world_state", {})["dag_failures"] = dag_failures
                shared_state["commitments"]["plan_status"] = "repair"
                # Emit repair to surface DAG failure to agents
                repair_msg = Message(
                    sender="InteractionManager",
                    receiver="all",
                    type="repair",
                    content={"conflict": f"DAG execution failures: {dag_failures}"},
                    confidence=1.0,
                    assumptions=[],
                    context={"goal": shared_state.get("goals", ["create plan"])[0] if shared_state.get("goals") else "create plan"},
                )
                manager.route(repair_msg, metrics=metrics)
            manager.save_shared(shared_state)
            # Log execution results
            exec_logger.record_results(dag_results, dag_failures)

    # Optional tool executor demo (safe simulate by default)
    if os.environ.get("HI_MACP_TOOL_DEMO", "0") == "1":
        print("\nRunning tool executor demo (simulate)...")
        tool_msg = Message(
            sender="AgentB",
            receiver="ToolExecutor",
            type="tool_action",
            content={"action": "smoke_test", "mode": "simulate", "target": "echo 'smoke tests'"},
            confidence=0.7,
            assumptions=[],
            context={"goal": "validate deployment"},
        )
        shared, divergence = manager.route(tool_msg, metrics=metrics)
        if divergence[0]:
            print(f"Tool action divergence: {divergence[1]}")
        shared, divergence = tool_exec.handle_action(tool_msg, metrics=metrics)
        if divergence[0]:
            print(f"Tool executor reported divergence: {divergence[1]}")

    metrics.aligned = aligned
    metrics.history_count = len(manager.load_shared().get("history", []))
    print(
        f"Metrics: clarify={metrics.clarify}, challenge={metrics.challenge}, repair={metrics.repair} "
        f"(auto={metrics.auto_repair}), divergences={metrics.divergence}, aligned={metrics.aligned}, "
        f"history_count={metrics.history_count}"
    )
    print(f"Shared memory stored at: {shared_path}")
    print("Message history:")
    for entry in manager.load_shared().get("history", []):
        print(json.dumps(entry, indent=2))
    final_aligned, final_reason = manager.alignment_metric(monitor_required_env=monitor_enabled)
    print(f"Final alignment metric: {final_aligned} ({final_reason})")
    try:
        log_run(manager.load_shared(), metrics, db_path=base_path / "logs.db")
    except Exception as e:
        print(f"Logging to SQLite failed: {e}")
    # Finalize execution log
    exec_logger.finalize("aligned" if aligned else "repair_required", extra={"metrics": metrics.__dict__})


if __name__ == "__main__":
    main()
