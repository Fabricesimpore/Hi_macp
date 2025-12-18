# HI-MACP

Human-Inspired Multi-Agent Communication Protocol (HI-MACP) MVP.

## Protocol Docs
- Formal spec: `HI-MACP-Spec-v1.md` (message schema, state machines, divergence taxonomy EC1–EC20, alignment/termination, delegation, tradeoff rules, sequence diagram).
- Experiments & runs: `HI-MACP-Experiments.md` (what was tested, how to run, current status).

## Install (local)
Requires Python 3.10+.
```bash
pip install .
# Or editable for development
pip install -e .
```

## CLI usage
```bash
# Run with a goal
hi run --goal "deploy service A to staging"

# Check toolchain/config readiness
hi doctor

# View latest run summary
hi status

# View a run log (auto-uses latest if omitted)
hi view            # latest
hi view logs/run_YYYYMMDDTHHMMSS.json

# List logs
hi logs

# Scaffold a project (hi.yaml, envs/, policies/, manifests/)
hi init --envs --policies --manifests
```
Useful flags: `--kube-context` and `--namespace` guard kubectl/helm actions, `--require-approval/--approved-by` gate execute mode, `--env-name` picks envs/<name>.yaml, `--policy-file` overrides policies.
Environment/policy files load from `hi.yaml` and `envs/<name>.yaml` (set `HI_MACP_ENV_NAME` to choose env). Policies can be set via `HI_MACP_POLICY_FILE` or via `hi.yaml` policy file references.

Safety/exec guards:
- kube protections: `HI_MACP_KUBE_CONTEXT`, `HI_MACP_KUBE_NAMESPACE`, and denylist for system namespaces unless `HI_MACP_KUBE_ALLOW_SYSTEM=1`.
- execute gating: `allow_execute` in envs/<name>.yaml, optional approval via `HI_MACP_REQUIRE_APPROVAL=1` + `HI_MACP_APPROVED_BY`.
- Terraform apply requires a plan file unless `HI_MACP_REQUIRE_TF_PLAN=0`.
- Docker registry: use `DOCKER_USERNAME/DOCKER_PASSWORD` for `docker_login` actions.
- Tradeoffs required when role goals set (propose/revise/commit must include structured tradeoffs).

GitHub/CI POC hooks:
- Configure GitHub in `envs/<name>.yaml` under `github: { owner, repo, workflow_id, branch }` or via env vars `HI_MACP_GH_OWNER`, `HI_MACP_GH_REPO`, `HI_MACP_GH_WORKFLOW`, `HI_MACP_GH_BRANCH`.
- CI branch selection: by default CI polling uses `world_state.github.branch` (e.g., a PR/fix branch). Override with `HI_MACP_CI_BRANCH` if you need to force CI polling to a specific branch.
- Provide `GH_TOKEN` for GitHub API calls (list/rerun workflow runs). Alignment stays false if CI checks fail or no token/run is found.
- Git actions: `git_commit`, `git_push`; GitHub Actions: `github_list_workflow_runs`, `github_rerun_workflow_run`, `github_get_workflow_logs`, `github_create_pull_request`, `github_merge_pull_request` (all respect allow_execute).
- CI tags: CI classifier agent can emit `ci_failure_reason`/`ci_failure_location` from workflow logs for targeted repairs.
- PR mode: set `HI_MACP_PR_MODE=1` (and optional `HI_MACP_PR_BRANCH`) to branch and push fixes to a feature branch instead of main.
- Guardrails: `HI_MACP_PROTECTED_PATHS` (comma-separated) blocks commands touching those paths; `HI_MACP_MAX_COMMITS` (default 3) and `HI_MACP_MAX_CI_RERUNS` (default 2) limit CI repair churn.
- Autofix toggle: `HI_MACP_AUTO_APPLY_CI_FIXES=1` (default) lets Planner apply lightweight CI fixes (e.g., add missing dependency from ModuleNotFoundError) before committing; set to 0 to disable.
- Patch safety: `HI_MACP_ALLOWED_PATCH_DIRS` (comma-separated) restricts fs_write targets; `HI_MACP_FAIL_ON_MISSING_PATH=1` (default) fails writes to non-existent files to avoid zero-byte mistakes.
- CI tuning: `HI_MACP_CI_CONCURRENCY_SUFFIX` appends a suffix to CI fix branches to avoid Actions concurrency self-cancel; repeated CI failures set `ci_cache_suspect` tag to remind clearing caches.
- Simulate-only: `HI_MACP_SIMULATE_ONLY=1` relaxes task-length/DAG/tradeoff gating so dry runs converge; execute mode remains strict. Export a run transcript via `InteractionManager.export_transcript(<path>)` for debugging.
- GH token preflight: CLI will warn/skip GitHub CI if `GH_TOKEN` is missing/invalid when `allow_execute` is true and GH repo/workflow are set.
- SSH-first: CLI warns when no SSH key is found and prefers PR/simulate over direct pushes. Use `hi configure` to validate SSH + GH_TOKEN and set origin to SSH.
- Branch protection preflight: if the target branch is protected, the CLI forces PR-first mode. Direct push requires explicit `HI_MACP_ALLOW_DIRECT_PUSH=1`.
- Transport guardrails: git network ops must use SSH remotes (git@github.com); HTML in command/API responses is treated as a transport violation. Recommended one-time hardening: `git config --global url.\"git@github.com:\".insteadOf https://github.com/`.

Autonomous deploy / CI-repair (safe defaults):
- Enable execution in your env (`allow_execute: true`) and pass a PAT in `GH_TOKEN` so pushes trigger CI. Approval gate via `HI_MACP_REQUIRE_APPROVAL=1` + `HI_MACP_APPROVED_BY`.
- Run: `hi run --goal "deploy app" --env-name dev --execute --kube-context <ctx> --namespace <ns>`. Kube context/namespace are mandatory before any execute mode step.
- Behavior on failure: Monitor waits for CI result, fetches logs, classifies failure (`ci_tags`), Planner adds a CI-fix task plus `git commit/push`, optional `github_rerun_workflow_run`, and (in PR mode) `github_create_pull_request`; if PR CI passes and a PR number is known, Planner can request `github_merge_pull_request`. Executor runs them up to the safety caps (`HI_MACP_MAX_COMMITS`, `HI_MACP_MAX_CI_RERUNS`, tracked via `ci_reruns`). No user prompt is required for fixes once execute mode is allowed.
- Current limitation: CI fixes beyond missing-dependency autofix still rely on human/LLM patches. To make it fully self-healing, map additional `ci_tags` → concrete edits (lint format, test fixes) or hook an LLM patch tool, then re-use the existing commit/push/rerun/merge loop.

## Tools supported (via ToolExecutor + CommandRunner)
- kubectl (apply, get)
- helm (template)
- docker (build, push, run)
- git (status, diff)
- filesystem (read/write)
- terraform (init, plan, apply)
All respect `allow_execute` flags; otherwise they simulate or dry-run.

## API server (FastAPI)
```bash
hi-api   # runs on 127.0.0.1:8000
```
Endpoints:
- POST `/run` with body `{ "goal": "...", "env_name": "staging", "llm": true, "monitor": true }`
- GET `/runs/latest`
- GET `/health`
- GET `/metrics` (Prometheus-style; protect with HI_MACP_API_TOKEN)
Security defaults: API token required (set HI_MACP_API_TOKEN), rate limit ~10 req/min, run behind TLS or enable HTTPS termination.

## Quick Run
```bash
cd "/Users/fabrice/Desktop/HI MACP"
source .env  # sets OPENAI_API_KEY
# Uses hi.yaml + envs/dev.yaml and the sample manifest in manifests/sample-deployment.yaml
# Minimal LLM (monitor off)
HI_MACP_LLM_ENABLED=1 HI_MACP_MONITOR=0 HI_MACP_FULL_SMM=0 HI_MACP_STRESS=0 python mvp/run.py
# LLM + Monitor, full SMM
HI_MACP_LLM_ENABLED=1 HI_MACP_MONITOR=1 HI_MACP_FULL_SMM=1 HI_MACP_STRESS=0 python mvp/run.py
# Full-stress LLM
HI_MACP_PROVIDER=openai HI_MACP_LLM_ENABLED=1 HI_MACP_MONITOR=1 HI_MACP_FULL_SMM=1 HI_MACP_STRESS=1 HI_MACP_REDUCED_STRESS=0 HI_MACP_MEMORY_DELTA=1 HI_MACP_LLM_TIMEOUT=5 HI_MACP_LLM_RETRIES=1 python mvp/run.py
# Deterministic edge cases (EC1–EC20)
python mvp/tests_edge_cases.py
# Optional tool demo (simulated smoke test)
HI_MACP_TOOL_DEMO=1 python mvp/run.py
# Run tests (requires pytest in your env)
pytest
# Configure SSH/GitHub (optional helper)
python -m hi_macp.cli.configure --repo Fabricesimpore/Hi_macp
# Push policy:
# - Default is PR-first; set HI_MACP_ALLOW_DIRECT_PUSH=1 only if direct push is allowed.
# Set allow_execute=true in envs/dev.yaml if you want real kubectl/helm/terraform/docker calls (defaults to simulate/dry-run).
# View execution log (concise / full)
python mvp/view_run.py mvp/logs/run_YYYYMMDDTHHMMSS.json
python mvp/view_run.py mvp/logs/run_YYYYMMDDTHHMMSS.json --full

## Governance boundary
- HI-MACP decides when automation is allowed; it will not self-escalate authority.
- If credentials/permissions are missing or restricted (SSH, branch protection, PAT scope), it will prefer PRs or simulate mode rather than forcing changes.
- Push policy: default is PR-first; force direct push only with `HI_MACP_ALLOW_DIRECT_PUSH=1`. Branch protection preflight forces PR mode. Tool/auth failures include guidance to switch to PR/SSH rather than self-escalating.
```
Autonomous PR test
autonomous pr test Wed Dec 17 11:51:55 CST 2025
autonomous pr test Wed Dec 17 17:07:52 CST 2025
