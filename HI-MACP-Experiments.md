# HI-MACP: From Protocol Implementation to LLM Stress Evaluation (Updated)

For the full formal specification, see `HI-MACP-Spec-v1.md` (message schema, state machines, divergence taxonomy, alignment/termination, delegation, tradeoff rules, and sequence diagram).

## 1. Abstract
We implemented a human-inspired multi-agent communication protocol (HI-MACP) with structured messages, shared mental models, grounding/repair cycles, role-based turn-taking, and reconciliation. We added an LLM adapter to generate protocol-compliant messages and ran stress tests to expose failures in schema adherence, grounding, and shared state alignment. Findings: (1) deterministic agents complete end-to-end cycles reliably; (2) LLM agents are overly agreeable under simple tasks; (3) under stress (conflicting goals/resources and complex tasks), LLMs produce schema omissions, premature commits, and unresolved assumptions that trigger repairs and prevent alignment—valuable for protocol evaluation.

## 2. System Implementation (Code)
- **Protocol & Layers**: `protocol.py` (Message schema; types: propose, clarify, revise, challenge, repair, commit, confirm; grounding helpers).
- **Interaction Manager**: `interaction_manager.py` (routing, model updates, divergence checks, auto-repair, validation failure logging, alignment metric).
- **Agents**:
  - Planner `agent_planner.py` (propose/revise/repair; optional LLM).
  - Executor `agent_executor.py` (clarify/challenge/repair/commit; reconciliation; confidence thresholds; optional LLM).
  - Monitor `agent_monitor.py` (strict confirmation; repairs if tasks/commit/assumptions missing; optional LLM).
- **LLM Adapter**: `llm_adapter.py` (uses provider modules; OpenAI path with JSON schema response_format; normalization; fallback to deterministic; loads key from `OPENAI_API_KEY` or `openai.json`; env-configurable timeout/retries).
- **LLM compliance helpers**: adapter now coerces `steps`/string into `plan` lists, fills missing `commitment`, and forces clarify when grounding is required (prevents premature commits). Harness LLM mode via `HI_MACP_LLM_HARNESS=1`.
- **LLM Providers**: `llm_adapter/providers/openai_provider.py` (structured output via response_format); adapter fills id/timestamp/sender/receiver/confidence post-LLM.
- **Metrics**: `metrics.py` (counts clarify/challenge/repair/auto-repair/divergence; alignment flag).
- **Runner**: `mvp/run.py` (env toggles for LLM, Monitor, full SMM, stress; reconciliation; metrics reporting; reduced-stress mode).
- **Shared Memory**: `mvp/shared.json` (reset per run; SMM fields vary with mode).
- **Edge-Case Harness**: `mvp/tests_edge_cases.py` (EC1–EC24 deterministic checks; EC24 validates LLM tool_actions when network available).
- **Tool Executor**: `mvp/agent_tool_executor.py` + `mvp/tools/command_runner.py` (simulate/dry-run/execute DevOps actions; capability profile; optional demo via `HI_MACP_TOOL_DEMO=1`).
- **Knowledge Agent**: `agent_knowledge.py` (injects best practices; challenges missing rollback/health/tests/templates).
- **Policy Validator**: `policy_validator.py` (configurable via env/file; enforces dry-run before execute, rollback/health/smoke for deploy, terraform plan).
- **DAG Runner**: `dag_runner.py` executes structured steps in dependency order; failures emit repair and block commit.
- **Execution Log**: `execution_log.py` writes JSON per run; `view_run.py` provides concise/full summaries.

## 3. Experimental Conditions
- **Env toggles**:
  - `HI_MACP_LLM_ENABLED` (0/1): enable LLM messages.
  - `HI_MACP_MONITOR` (0/1): enable Monitor agent.
  - `HI_MACP_FULL_SMM` (0/1): include beliefs/resources/role goals.
  - `HI_MACP_STRESS` (0/1): inject inconsistencies (task-count mismatch, belief conflicts).
  - `HI_MACP_REDUCED_STRESS` (0/1): short 6-step scenario for faster LLM tests.
  - `HI_MACP_LLM_TIMEOUT`, `HI_MACP_LLM_RETRIES`: configure per-call timeout and retries for the LLM adapter.
- **LLM model**: gpt-4o-mini via `openai` client (falls back to scripted if unavailable).
- **Tasks**:
  - Simple: 4-step deploy plan (baseline).
  - Stress: 12-step distributed microservice rollout with blue/green, canary, rollback, key rotation, network policies.
  - Reduced stress: 6-step rollout for faster LLM tests.
- **Conflicting goals/resources (stress)**:
  - Planner goal: maximize_security; Executor goal: minimize_time; Monitor goal: enforce_justification.
  - Executor lacks root/network/DNS rights; Planner assumes escalation possible.

## 4. Protocol Behaviors Implemented
- Structured JSON envelope with uncertainty, assumptions, context.
- Speech-act semantics (propose/clarify/revise/challenge/repair/commit/confirm) and grounding.
- Shared mental model updates (tasks, commitments, roles; beliefs/resources in full SMM).
- Divergence detection: truncated plans, missing tasks on commit, repair-state conflicts.
- Auto-repair injection by InteractionManager.
- Reconciliation phase: Executor can send full-plan commit; Monitor confirmation required when enabled.
- Validation failure logging: `VALIDATION_FAILURE` entries recorded on schema errors.

## 5. Results
### 5.1 Deterministic Baseline (LLM off)
- End-to-end cycle completes with clarify → revise → challenge → repair → truncated commit → auto-repair → reconciliation → monitor confirm (when enabled). Alignment = True.

### 5.2 LLM Minimal (Monitor off, minimal SMM, stress off)
- LLM often “polite”: confirm + commit with minimal challenge.
- Alignment reached after reconciliation; few repairs; risk of missing `commitment` field in commits.

### 5.3 LLM with Monitor (minimal SMM, stress off)
- Monitor adds confirmation; alignment True after reconciliation.
- LLM still tends to agree/commit early; little challenge/clarify.

### 5.4 LLM Stress Mode (monitor on, stress on, minimal SMM)
- LLM produced mismatched schema (tasks nested under content.steps, not tasks list).
- Clarify/challenge occurred; Planner repair asked about credentials; Executor committed assuming creds.
- InteractionManager auto-repair: “Commitment recorded but no tasks present.”
- Monitor clarified; final alignment False (intended under stress). Metrics: clarify↑, challenge↑, repair↑, auto-repair=1, aligned=False.

### 5.5 LLM Reduced-Stress Mode (monitor on, reduced stress, short timeout/retries)
- With structured output and system-filled ids/timestamps, flow completed: clarify → revise → challenge → repair → truncated commit → auto-repair → reconciliation commit → monitor confirm → aligned=True. Metrics: clarify=1, challenge=1, repair=1 (auto=1), aligned=True, history_count=10.

### 5.6 Observed Failure Modes
- Schema violations: missing `commitment` on commits; plan steps not mapped to shared tasks; placeholder ids/timestamps.
- Premature commits: commit before tasks synchronized.
- Assumption drift: Executor assumes credentials; Planner assumes escalation; Monitor finds task_count mismatch.
- Repair churn: auto-repair fires on mismatched tasks; Monitor triggers clarify when tasks empty.
- Task-count mismatch loops (stress mode): claimed_tasks vs plan length; mitigated by auto-repair updating claimed_tasks and forcing repair/revise.
- Timeout/latency in full stress LLM runs: mitigated by reduced-stress mode, short timeouts/retries, and schema enforcement; full stress remains heavy.
- Edge-case suite (deterministic): all EC1–EC15 pass with current guards—role gate rejects propose from Executor, clarify requires non-empty issue, contradictions/goal drift flagged via divergence, endless challenge and monitor deadlock prevented, memory corruption surfaced, timeout cascades recorded as failure_mode.

### 5.7 Latest Status (Dec 09)
- Cleaned commit gate to use the single unresolved-challenge guard (history-based) plus monitor/grounding gates; removed redundant challenge counters.
- Monitor tradeoff check simplified to history-based debate quality.
- EC1–EC20 now **PASS** (see `python mvp/tests_edge_cases.py`).
- Full LLM stress run converges with alignment=True when using delta memory + structured output; metrics in last run: clarify=0, challenge=1, repair=0, history=5, aligned=True.
- Logs persist to `logs.db`; shared state stays aligned after reconciliation + monitor confirm.

## 6. Instrumentation for Paper
- **Logs**: `mvp/shared.json` history captures all messages + validation failures.
- **Metrics**: clarify/challenge/repair/auto-repair/divergence/alignment counts printed per run.
- **Validation failures**: logged with `VALIDATION_FAILURE` type, error, and raw message.

## 7. How to Run
```
cd "/Users/fabrice/Desktop/HI MACP"
source .env  # sets OPENAI_API_KEY
# Minimal LLM (no monitor, minimal SMM)
HI_MACP_LLM_ENABLED=1 HI_MACP_MONITOR=0 HI_MACP_FULL_SMM=0 HI_MACP_STRESS=0 python mvp/run.py
# LLM + Monitor, minimal SMM
HI_MACP_LLM_ENABLED=1 HI_MACP_MONITOR=1 HI_MACP_FULL_SMM=0 HI_MACP_STRESS=0 python mvp/run.py
# LLM + Monitor, full SMM
HI_MACP_LLM_ENABLED=1 HI_MACP_MONITOR=1 HI_MACP_FULL_SMM=1 HI_MACP_STRESS=0 python mvp/run.py
# Fast LLM test (reduced stress, short timeout/retries)
HI_MACP_LLM_ENABLED=1 HI_MACP_MONITOR=1 HI_MACP_FULL_SMM=1 HI_MACP_STRESS=0 HI_MACP_REDUCED_STRESS=1 HI_MACP_LLM_TIMEOUT=5 HI_MACP_LLM_RETRIES=1 python mvp/run.py
# Full-stress LLM (conflicts + inconsistencies; convergence observed)
HI_MACP_PROVIDER=openai HI_MACP_LLM_ENABLED=1 HI_MACP_MONITOR=1 HI_MACP_FULL_SMM=1 HI_MACP_STRESS=1 HI_MACP_REDUCED_STRESS=0 HI_MACP_MEMORY_DELTA=1 HI_MACP_LLM_TIMEOUT=5 HI_MACP_LLM_RETRIES=1 python mvp/run.py
# Deterministic edge cases (EC1–EC24)
python mvp/tests_edge_cases.py
# View a run log (concise / full history)
python mvp/view_run.py mvp/logs/run_YYYYMMDDTHHMMSS.json
python mvp/view_run.py mvp/logs/run_YYYYMMDDTHHMMSS.json --full
```

## 8. Open Issues / Next Improvements
- Formal spec + diagrams: publish HI-MACP v1.0 (schema, phases, divergence/repair rules, delegation contract, alignment conditions).
- Config presets: `minimal`, `stress`, `research`, `production` for repeatable runs.
- Prompt/memory deltas: tighten further for cost/latency; make deltas the only context sent to LLM.
- Provider ergonomics: finalize provider factory and defaults for OpenAI/Anthropic/local.
- Chaos hooks: out-of-order phases, delayed clarifications, random SMM perturbations.
- Failure taxonomy + analytics: tag protocol/schema/SMM/assumption/commitment failures; auto diff planned vs committed tasks; summarize unresolved assumptions.
