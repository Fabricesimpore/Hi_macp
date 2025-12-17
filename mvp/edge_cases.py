# Edge case harness scaffold for HI-MACP.
# Each function describes a scenario to be wired into automated tests.

EDGE_CASES = {
    "EC1_refusal": "Agent refuses to revise/clarify despite monitor/proposal requests.",
    "EC2_nonsense_clarify": "Agent sends nonsensical clarification unrelated to plan.",
    "EC3_self_contradiction": "Agent contradicts earlier statement/commitment.",
    "EC4_hallucinated_steps": "Agent adds steps never proposed or agreed.",
    "EC5_goal_drift": "Agent changes the goal mid-conversation.",
    "EC6_endless_challenge": "Challenge loop with no resolution.",
    "EC7_resource_disagreement": "Planner assumes resources; executor denies.",
    "EC8_monitor_cannot_resolve": "Monitor unable to clear conflict; should force fail mode.",
    "EC9_invalid_plan": "Empty/duplicate/contradictory steps in plan.",
    "EC10_memory_corruption": "Shared state rewritten incorrectly (simulate corruption).",
    "EC11_schema_failures": "Repeated schema errors (>3) trigger fallback chain.",
    "EC12_role_confusion": "Agent acts as a different role.",
    "EC13_deadlock": "Unreachable alignment detected; graceful termination.",
    "EC14_multi_task": "Plan A completes, Plan B starts; ensure memory isolation.",
    "EC15_timeout_cascade": "Single LLM timeout should not break entire protocol.",
}


def list_edge_cases():
    return EDGE_CASES


def main():
    parser = argparse.ArgumentParser(description="Edge case harness (stub)")
    parser.add_argument("--case", type=str, default=None, help="Edge case id, e.g., EC1_refusal")
    args = parser.parse_args()

    if args.case:
        desc = EDGE_CASES.get(args.case)
        if not desc:
            print(json.dumps({"case": args.case, "status": "UNKNOWN"}))
            return
        # Stub: not implemented
        print(
            json.dumps(
                {
                    "case": args.case,
                    "status": "NOT_IMPLEMENTED",
                    "description": desc,
                    "alignment": False,
                    "failure_mode": "not_implemented",
                }
            )
        )
    else:
        print(json.dumps({"cases": EDGE_CASES}))


if __name__ == "__main__":
    main()
import argparse
import json
from typing import Dict
