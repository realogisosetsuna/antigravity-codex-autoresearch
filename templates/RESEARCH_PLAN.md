# Research Plan: {{PROJECT_NAME}}

- **Created**: {{CREATED_AT}}
- **Objective**: {{OBJECTIVE}}
- **Primary Orchestrator**: Gemini 4 Carbon (`agy`)
- **Partner Specialist / Auditor**: GPT-6.2-Astra (`auto-research-codex` MCP)

## 1. Problem Statement & Success Criteria
- **Primary Metric**: `TODO (e.g., val_wer <= X% / macro_f1 >= Y)`
- **Guardrail Metrics**: `TODO (e.g., latency, RTF, memory, zero train/eval overlap)`
- **Baseline Command**: `TODO (e.g., python3 src/eval.py --split dev)`

## 2. Data & Compute Boundaries
- **Read-Only Source Manifests / Data**: `TODO`
- **Large Artifacts / Checkpoints Target (`/mnt/data1..4/$USER/...`)**: `TODO (symlink into ./runs or ./data)`
- **Authorized Node & GPU Cap**: `TODO (verify hostname & deployment.json; no Slurm)`

## 3. Hypothesis Tree
- [ ] **H0 (Baseline)**: Reproduce clean baseline and record exact metric in `EXPERIMENTS.tsv`.
- [ ] **H1**: `TODO`
- [ ] **H2**: `TODO`
- [ ] **H3**: `TODO`

## 4. Verification & Stopping Protocol
1. Smoke-test every code change on a tiny slice before full runs.
2. Run `astra_review` prior to multi-GPU or multi-hour training jobs.
3. Log every completed run into `EXPERIMENTS.tsv` via `log_experiment.py`.
4. Mark `"completed": true` in `RESEARCH_GOAL.json` only when primary + guardrail criteria are verified.
