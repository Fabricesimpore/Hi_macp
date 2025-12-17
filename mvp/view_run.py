import argparse
import json
from pathlib import Path


def summarize_plan(plan):
    print("\nPlan (summary):")
    for step in plan or []:
        if isinstance(step, dict):
            print(f"  - {step.get('id', step.get('type', 'step'))}: type={step.get('type')}, tool={step.get('tool')}, mode={step.get('mode')}")
        else:
            print(f"  - {step}")


def summarize_dag(batches, failures, rollback):
    print("\nDAG batches:")
    for i, batch in enumerate(batches or [], 1):
        print(f"  Batch {i}:")
        for step in batch:
            label = step.get("id") if isinstance(step, dict) else step
            print(f"    * {label}")
    if failures:
        print("\nDAG Failures:")
        for fail in failures:
            print(f"  - {fail}")
    if rollback:
        print("\nRollback:")
        for rb in rollback:
            print(f"  - {rb}")


def summarize_metrics(data):
    metrics = data.get("metrics") or {}
    if metrics:
        print("\nMetrics:")
        for k, v in metrics.items():
            print(f"  {k}: {v}")


def summarize_policies(data):
    issues = data.get("failures") or []
    # Heuristic: policy violations often listed in failures as strings
    policy_issues = [f for f in issues if "policy" in str(f).lower() or "missing" in str(f).lower()]
    if policy_issues:
        print("\nPolicy Violations:")
        for p in policy_issues:
            print(f"  - {p}")


def summarize_tools(data):
    results = data.get("results") or []
    tool_results = [r for r in results if r.get("action")]
    if tool_results:
        print("\nTool Actions:")
        for r in tool_results:
            print(f"  - {r.get('action')} status={r.get('status')} details={r.get('details')}")


def print_full_history(data):
    history = data.get("history") or data.get("messages") or []
    if history:
        print("\nFull History:")
        for entry in history:
            print(json.dumps(entry, indent=2))


def main():
    parser = argparse.ArgumentParser(description="View HI-MACP run log.")
    parser.add_argument("logfile", help="Path to run log JSON (e.g., logs/run_2025....json)")
    parser.add_argument("--full", action="store_true", help="Print full message history (verbose).")
    args = parser.parse_args()
    path = Path(args.logfile)
    if not path.exists():
        print(f"Log file not found: {path}")
        return
    data = json.loads(path.read_text())
    print(f"Run: {data.get('run_id')}  Status: {data.get('final_status')}")
    print(f"Started: {data.get('started_at')}  Ended: {data.get('ended_at')}")

    summarize_plan(data.get("plan"))
    summarize_dag(data.get("dag_batches"), data.get("failures"), data.get("rollback"))
    summarize_policies(data)
    summarize_tools(data)
    summarize_metrics(data)

    if args.full:
        print_full_history(data)


if __name__ == "__main__":
    main()
