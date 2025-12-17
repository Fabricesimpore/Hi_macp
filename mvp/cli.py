import argparse
import os
from pathlib import Path
import run


def main():
    parser = argparse.ArgumentParser(description="HI-MACP CLI wrapper for deployment-style goals.")
    parser.add_argument("--goal", required=True, help="Goal or task, e.g., 'deploy my-app to staging'.")
    parser.add_argument("--env", default="staging", help="Environment name (informational).")
    parser.add_argument("--llm", action="store_true", help="Enable LLM-generated messages.")
    parser.add_argument("--monitor", action="store_true", help="Enable monitor agent (default on).")
    parser.add_argument("--stress", action="store_true", help="Enable stress mode.")
    parser.add_argument("--reduced-stress", action="store_true", help="Use reduced stress scenario (faster).")
    args = parser.parse_args()

    # Set env vars to drive run.py behavior
    os.environ["HI_MACP_GOAL"] = args.goal
    os.environ["HI_MACP_LLM_ENABLED"] = "1" if args.llm else "0"
    os.environ["HI_MACP_MONITOR"] = "1" if args.monitor else "0"
    os.environ["HI_MACP_STRESS"] = "1" if args.stress else "0"
    os.environ["HI_MACP_REDUCED_STRESS"] = "1" if args.reduced_stress else "0"

    print(f"Running HI-MACP for goal: {args.goal} (env={args.env})")
    run.main()


if __name__ == "__main__":
    main()
