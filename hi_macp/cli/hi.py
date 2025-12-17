#!/usr/bin/env python3
"""HI-MACP CLI entrypoint using package imports."""
import argparse
import os
import json
from pathlib import Path
import sys
import shutil
import subprocess

from hi_macp.runtime import run as hi_run
from hi_macp.cli import view_run as hi_view
from hi_macp.runtime.run import load_project_config


def _logs_dir() -> Path:
    # Prefer package logs; fallback to ./logs in CWD
    pkg_logs = Path(__file__).resolve().parents[1] / "logs"
    if pkg_logs.exists():
        return pkg_logs
    cwd_logs = Path.cwd() / "logs"
    return cwd_logs


def _latest_log() -> Path | None:
    logs_dir = _logs_dir()
    if not logs_dir.exists():
        return None
    logs = sorted(logs_dir.glob("run_*.json"))
    return logs[-1] if logs else None


def _summarize_log(path: Path) -> dict:
    data = json.loads(path.read_text())
    failures = data.get("failures") or []
    results = data.get("results") or []
    tool_failures = [r for r in results if r.get("status") not in {"success", None}]
    policy_issues = [f for f in failures if "policy" in str(f).lower() or "missing" in str(f).lower()]
    return {
        "run_id": data.get("run_id"),
        "status": data.get("final_status"),
        "failures": failures,
        "policy": policy_issues,
        "tool_failures": tool_failures,
        "results": len(results),
        "rollback": data.get("rollback") or [],
        "started_at": data.get("started_at"),
        "ended_at": data.get("ended_at"),
    }


def cmd_run(args: argparse.Namespace) -> None:
    os.environ["HI_MACP_GOAL"] = args.goal or ""
    if args.config:
        os.environ["HI_MACP_CONFIG"] = args.config
    if args.env_name:
        os.environ["HI_MACP_ENV_NAME"] = args.env_name
    if args.kube_context:
        os.environ["HI_MACP_KUBE_CONTEXT"] = args.kube_context
    if args.namespace:
        os.environ["HI_MACP_KUBE_NAMESPACE"] = args.namespace
    if args.require_approval:
        os.environ["HI_MACP_REQUIRE_APPROVAL"] = "1"
    if args.approved_by:
        os.environ["HI_MACP_APPROVED_BY"] = args.approved_by
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
    logs_dir = _logs_dir()
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
    target = args.logfile
    if not target:
        latest = _latest_log()
        if not latest:
            print("No run log found.")
            return
        target = str(latest)
    sys.argv = ["view_run.py", target] + (["--full"] if args.full else [])
    hi_view.main()


def cmd_status(args: argparse.Namespace) -> None:
    path = Path(args.logfile) if args.logfile else _latest_log()
    if not path or not path.exists():
        print("No run log found.")
        return
    summary = _summarize_log(path)
    print(f"Run: {summary.get('run_id')} ({path.name})")
    print(f"Status: {summary.get('status')}")
    print(f"Started: {summary.get('started_at')}")
    if summary.get("ended_at"):
        print(f"Ended:   {summary.get('ended_at')}")
    print(f"Results: {summary.get('results')} entries")
    if summary.get("policy"):
        print("Policy issues:")
        for p in summary["policy"]:
            print(f" - {p}")
    if summary.get("tool_failures"):
        print("Tool failures:")
        for t in summary["tool_failures"]:
            print(f" - {t}")
    if summary.get("failures") and not summary.get("policy"):
        print("Failures:")
        for f in summary["failures"]:
            print(f" - {f}")
    if summary.get("rollback"):
        print("Rollback:")
        for r in summary["rollback"]:
            print(f" - {r}")


def _check_tool(name: str, version_args: list[str]) -> dict:
    path = shutil.which(name)
    if not path:
        return {"name": name, "status": "missing", "details": "not on PATH"}
    try:
        proc = subprocess.run([path] + version_args, capture_output=True, text=True, timeout=8)
        output = (proc.stdout or "") + (proc.stderr or "")
        output = output.strip()
        status = "ok" if proc.returncode == 0 else "error"
        return {"name": name, "status": status, "details": output or f"exit {proc.returncode}"}
    except subprocess.TimeoutExpired:
        return {"name": name, "status": "timeout", "details": "version command timed out"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"name": name, "status": "error", "details": str(exc)}


def _print_check(label: str, result: dict) -> None:
    status = result.get("status")
    details = result.get("details", "")
    prefix = "OK" if status in {"ok", "success"} else "WARN" if status not in {"missing", "error"} else "ERROR"
    print(f"[{prefix}] {label}: {details}")


def cmd_doctor(_: argparse.Namespace) -> None:
    cfg = load_project_config() or {}
    env_cfg = cfg.get("env_cfg", {}) if isinstance(cfg, dict) else {}
    tool_caps = env_cfg.get("tool_capabilities", {}) if isinstance(env_cfg, dict) else {}
    allow_execute = bool(tool_caps.get("allow_execute")) or os.environ.get("HI_MACP_ALLOW_EXECUTE") == "1"
    env_name = cfg.get("env") or os.environ.get("HI_MACP_ENV_NAME") or "default"

    print(f"Project env: {env_name}")
    print(f"Execution allowed: {allow_execute} (set allow_execute in envs/<name>.yaml or HI_MACP_ALLOW_EXECUTE=1)")
    kube_context = os.environ.get("HI_MACP_KUBE_CONTEXT")
    kube_ns = os.environ.get("HI_MACP_KUBE_NAMESPACE")
    if kube_context:
        print(f"Kube context (from env): {kube_context}")
    if kube_ns:
        print(f"Kube namespace (from env): {kube_ns}")

    checks = [
        _check_tool("kubectl", ["version", "--client"]),
        _check_tool("helm", ["version"]),
        _check_tool("terraform", ["version"]),
        _check_tool("docker", ["--version"]),
    ]
    for chk in checks:
        _print_check(chk["name"], chk)

    # Kubernetes context
    if shutil.which("kubectl"):
        ctx = _check_tool("kubectl", ["config", "current-context"])
        if ctx["status"] == "ok" and not ctx.get("details"):
            ctx["details"] = "current-context not set"
        ctx_label = "kubectl context"
        _print_check(ctx_label, ctx)
        if ctx["status"] != "ok":
            print("       tip: kubectl config use-context <name> or export KUBECONFIG=<path>")

    # Docker daemon check (non-fatal)
    if shutil.which("docker"):
        info = _check_tool("docker", ["info", "--format", "{{.ServerVersion}}"])
        if info["status"] != "ok":
            info["details"] = "Docker daemon not reachable; start Docker Desktop or dockerd"
        _print_check("docker daemon", info)

    # OpenAI key check for LLM mode
    if os.environ.get("HI_MACP_LLM_ENABLED") == "1":
        has_key = bool(os.environ.get("OPENAI_API_KEY"))
        prefix = "OK" if has_key else "WARN"
        detail = "OPENAI_API_KEY present" if has_key else "OPENAI_API_KEY missing; LLM calls will fail"
        print(f"[{prefix}] llm: {detail}")


def cmd_init(args: argparse.Namespace) -> None:
    target = Path(args.path).resolve()
    target.mkdir(parents=True, exist_ok=True)
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

    pr = sub.add_parser("run", help="Run HI-MACP orchestrator")
    pr.add_argument("--goal", required=True, help="Goal or task, e.g., 'deploy service A to staging'")
    pr.add_argument("--env", default="staging", help="Environment label (informational)")
    pr.add_argument("--config", help="Path to hi.yaml (project config)")
    pr.add_argument("--env-name", help="Environment name to load from envs/<name>.yaml")
    pr.add_argument("--kube-context", help="Kube context to use (guards against empty)")
    pr.add_argument("--namespace", help="Namespace for kubectl/helm actions")
    pr.add_argument("--require-approval", action="store_true", help="Require approval before execute mode (HI_MACP_REQUIRE_APPROVAL=1)")
    pr.add_argument("--approved-by", help="Approver name/id to allow execute when approval is required")
    pr.add_argument("--llm", action="store_true", help="Enable LLM agents")
    pr.add_argument("--monitor", action="store_true", default=True, help="Enable Monitor (default on)")
    pr.add_argument("--stress", action="store_true", help="Enable stress mode")
    pr.add_argument("--reduced-stress", action="store_true", help="Enable reduced-stress mode")
    pr.add_argument("--policy-file", help="Path to policy config (yaml/json)")
    pr.set_defaults(func=cmd_run)

    pl = sub.add_parser("logs", help="List run logs")
    pl.set_defaults(func=cmd_logs)

    pv = sub.add_parser("view", help="View a run log")
    pv.add_argument("logfile", nargs="?", help="Path to run log JSON (if omitted, use latest in logs/)")
    pv.add_argument("--full", action="store_true", help="Show full history")
    pv.set_defaults(func=cmd_view)

    ps = sub.add_parser("status", help="Show latest run summary (or a specific log)")
    ps.add_argument("--logfile", help="Path to run log JSON; if omitted, uses latest")
    ps.set_defaults(func=cmd_status)

    pi = sub.add_parser("init", help="Scaffold a HI-MACP project")
    pi.add_argument("--path", default="hi-project", help="Target path (default: hi-project)")
    pi.add_argument("--envs", action="store_true", help="Create env files (dev/staging/prod)")
    pi.add_argument("--policies", action="store_true", help="Create default policy file")
    pi.add_argument("--manifests", action="store_true", help="Create sample manifest")
    pi.set_defaults(func=cmd_init)

    pd = sub.add_parser("doctor", help="Check toolchain and config readiness")
    pd.set_defaults(func=cmd_doctor)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
