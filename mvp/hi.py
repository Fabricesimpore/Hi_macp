#!/usr/bin/env python3
"""
Simple CLI entry point for HI-MACP.

Subcommands:
  run         -> execute the orchestrator (wraps mvp/run.py)
  logs        -> list available run logs
  view        -> view a specific run log (concise or --full)
"""
import argparse
import os
import json
from pathlib import Path
import sys

# Local imports
import run as hi_run
import view_run as hi_view


def cmd_run(args: argparse.Namespace) -> None:
    # Set env vars for run.py
    os.environ["HI_MACP_GOAL"] = args.goal or ""
    if args.config:
        os.environ["HI_MACP_CONFIG"] = args.config
    if args.env_name:
        os.environ["HI_MACP_ENV_NAME"] = args.env_name
    os.environ["HI_MACP_LLM_ENABLED"] = "1" if args.llm else "0"
    os.environ["HI_MACP_MONITOR"] = "1" if args.monitor else "0"
    os.environ["HI_MACP_STRESS"] = "1" if args.stress else "0"
    os.environ["HI_MACP_REDUCED_STRESS"] = "1" if args.reduced_stress else "0"
    if args.policy_file:
        os.environ["HI_MACP_POLICY_FILE"] = args.policy_file
        os.environ["HI_MACP_POLICY_ENFORCE"] = "1"
    print(f"Running HI-MACP with goal='{args.goal}' env={args.env}")
    hi_run.main()


def cmd_logs(args: argparse.Namespace) -> None:
    logs_dir = Path(__file__).parent / "logs"
    if not logs_dir.exists():
        print("No logs directory found.")
        return
    logs = sorted(logs_dir.glob("run_*.json"))
    if not logs:
        print("No run logs found.")
        return
    for log in logs:
        print(log)


def cmd_view(args: argparse.Namespace) -> None:
    # Delegate to view_run
    sys.argv = ["view_run.py", args.logfile] + (["--full"] if args.full else [])
    hi_view.main()


def cmd_init(args: argparse.Namespace) -> None:
    """Scaffold a project with envs/policies/manifests."""
    target = Path(args.path).resolve()
    target.mkdir(parents=True, exist_ok=True)
    # hi.yaml
    hi_cfg = {
        "project": "my-hi-project",
        "default_env": "dev",
        "agents": {"planner": "maximize_security", "executor": "maximize_speed", "monitor": "strict"},
        "policy": {"file": "policies/default.yaml"},
    }
    (target / "hi.yaml").write_text(json.dumps(hi_cfg, indent=2))
    if args.envs:
        env_dir = target / "envs"
        env_dir.mkdir(exist_ok=True)
        for env_name in ["dev", "staging", "prod"]:
            env_cfg = {
                "env": env_name,
                "tool_capabilities": {
                    "allow_execute": False if env_name != "prod" else True,
                    "tools": {"kubectl": True, "helm": True, "terraform": env_name != "dev"},
                },
                "credentials": {"kubecontext": f"{env_name}-cluster"},
            }
            (env_dir / f"{env_name}.yaml").write_text(json.dumps(env_cfg, indent=2))
    if args.policies:
        pol_dir = target / "policies"
        pol_dir.mkdir(exist_ok=True)
        pol_cfg = {
            "policies": {
                "require_dry_run_before_execute": True,
                "require_smoke_test_for_deploy": True,
                "require_rollback_plan": True,
                "require_health_check": True,
                "require_terraform_plan": True,
            }
        }
        (pol_dir / "default.yaml").write_text(json.dumps(pol_cfg, indent=2))
    if args.manifests:
        man_dir = target / "manifests"
        man_dir.mkdir(exist_ok=True)
        sample = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "sample-service"},
            "spec": {
                "replicas": 2,
                "selector": {"matchLabels": {"app": "sample"}},
                "template": {
                    "metadata": {"labels": {"app": "sample"}},
                    "spec": {
                        "containers": [
                            {"name": "app", "image": "nginx:1.25", "ports": [{"containerPort": 80}]}
                        ]
                    },
                },
            },
        }
        (man_dir / "sample_deployment.yaml").write_text(json.dumps(sample, indent=2))
    print(f"Scaffold created at {target}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HI-MACP CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    pr = sub.add_parser("run", help="Run HI-MACP orchestrator")
    pr.add_argument("--goal", required=True, help="Goal or task, e.g., 'deploy service A to staging'")
    pr.add_argument("--env", default="staging", help="Environment label (informational)")
    pr.add_argument("--config", help="Path to hi.yaml (project config)")
    pr.add_argument("--env-name", help="Environment name to load from envs/<name>.yaml")
    pr.add_argument("--llm", action="store_true", help="Enable LLM agents")
    pr.add_argument("--monitor", action="store_true", default=True, help="Enable Monitor (default on)")
    pr.add_argument("--stress", action="store_true", help="Enable stress mode")
    pr.add_argument("--reduced-stress", action="store_true", help="Enable reduced-stress mode")
    pr.add_argument("--policy-file", help="Path to policy config (yaml/json)")
    pr.set_defaults(func=cmd_run)

    # logs
    pl = sub.add_parser("logs", help="List run logs")
    pl.set_defaults(func=cmd_logs)

    # view
    pv = sub.add_parser("view", help="View a run log")
    pv.add_argument("logfile", help="Path to run log JSON (e.g., mvp/logs/run_YYYYMMDDTHHMMSS.json)")
    pv.add_argument("--full", action="store_true", help="Show full history")
    pv.set_defaults(func=cmd_view)

    # init
    pi = sub.add_parser("init", help="Scaffold a HI-MACP project")
    pi.add_argument("--path", default="hi-project", help="Target path (default: hi-project)")
    pi.add_argument("--envs", action="store_true", help="Create env files (dev/staging/prod)")
    pi.add_argument("--policies", action="store_true", help="Create default policy file")
    pi.add_argument("--manifests", action="store_true", help="Create sample manifest")
    pi.set_defaults(func=cmd_init)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
