---
name: auto-research
description: >-
  End-to-end runbook for initializing and running long-horizon autonomous research projects
  (Auto Research) with Gemini 4 Carbon, GPT-6.2-Astra (ultra), cross-node SSH GPU scheduling,
  NAS/local-disk data routing, and Stop-hook goal gating. Activate when starting, planning,
  resuming, or executing autonomous experiments or ingesting user research survey/plan Markdown documents.
---

# Auto Research Runbook (Gemini 4 Carbon × Astra Ultra)

Locate the Auto Research hub via `$AUTO_RESEARCH_HOME` (or `~/auto_research`, or two levels above this plugin directory).
For multi-node GPU scheduling, standby GPU reserve SOP, shared NAS vs node-local `/mnt/data*`, and `rsync` transfer details, **always read** [cluster_gpu_storage.md](./references/cluster_gpu_storage.md).

## 1. Bootstrapping from User Survey / Research Plan Markdown Files

When the user hands you one or more existing survey, literature review, or research plan `.md` files in a new session:
1. **Initialize a dedicated Git-backed project** (choose a clear slug `<exp_name>` and a local data disk with ample free space verified via `cluster_ctl.py status`):
   ```bash
   "${AUTO_RESEARCH_HOME:-$HOME/auto_research}/scripts/init_experiment.sh" \
     <exp_name> "<one-line objective>" --data-disk /mnt/data1/$USER
   ```
2. **Preserve source documents**: Copy the user's input Markdown files into `<project_dir>/docs/` and commit them so the project repository is self-contained.
3. **Synthesize operational contracts**:
   - Distill the user's Markdown documents into `<project_dir>/RESEARCH_PLAN.md` (concrete dataset paths, baseline script, falsifiable hypothesis tree `H0..Hk`, primary + guardrail metrics, and node/GPU budget).
   - Populate `<project_dir>/RESEARCH_GOAL.json` (set `"conversation_id"` to your current Conversation ID so the `Stop` hook binds cleanly to this session even if `agy` was launched from a parent folder).
4. **Joint Pre-Flight Critique with Astra Ultra**:
   - Call `astra_consult` (`cwd="<project_dir>"`) pointing Astra at `docs/*.md` and `RESEARCH_PLAN.md` to audit split leakage risks, baseline validity, and hypothesis priority before starting long GPU runs.

## 2. Discover & Claim Cluster GPUs Safely (`cluster_ctl.py`)

```bash
python3 "${AUTO_RESEARCH_HOME:-$HOME/auto_research}/scripts/cluster_ctl.py" status
python3 "${AUTO_RESEARCH_HOME:-$HOME/auto_research}/scripts/cluster_ctl.py" recommend-gpus --count 1 --min-free-gb 50
```

- **If using standby GPUs (`RESERVED_BY_US`)**: Atomically claim first with the `claim_cmd` printed by `cluster_ctl.py` (`control.sh release --gpus <ID>`), keep `<ID>` exempted across all rounds, and return with `control.sh watch --gpus <ID>` only after all experiments complete. Never touch another task's `excluded_gpus`.
- Always bind `CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=<ID>` (`cluster_ctl.py exec/launch-bg --gpus <ID>` handles this automatically).

## 3. Autonomous Execution & Goal Gating

1. Smoke-test baseline & evaluation pipeline on a tiny slice first.
2. Set `"active": true`, `"completed": false`, `"min_experiments": <N>`, `"conversation_id": "<your-conversation-id>"` in `RESEARCH_GOAL.json`.
3. On each iteration:
   - Delegate complex 0-to-1 modules to `astra_code` (`ultra`, isolated worktree); run `astra_review` (`ultra`) before multi-GPU or cross-node runs.
   - Launch long jobs via `cluster_ctl.py launch-bg` and monitor via `schedule` + `cluster_ctl.py check-job` (recording active `node`, `pid`, `gpus`, and `log` path into `runs/active_job.json` for crash-resilient recovery).
   - Log every completed run into `EXPERIMENTS.tsv` via `scripts/log_experiment.py`.
4. Set `"completed": true` in `RESEARCH_GOAL.json` (and return any borrowed standby GPUs via `control.sh watch --gpus <ID>`) once all acceptance criteria are genuinely verified.

## 4. Resuming After SSH Disconnect / Session Restart

When asked to resume an ongoing Auto Research project:
1. Read `RESEARCH_PLAN.md`, `RESEARCH_GOAL.json`, `EXPERIMENTS.tsv` (`log_experiment.py --show`), `BLOCKERS.md`, and `git log -n 5`.
2. Check if any detached GPU job is still alive via `runs/active_job.json` + `cluster_ctl.py check-job` (never re-launch a duplicate job on top of an already-running PID).
3. Check `${AUTO_RESEARCH_HOME:-$HOME/auto_research}/logs/astra_calls/` if you need the latest Astra `thread_id` for `astra_resume`.
4. Continue seamlessly from the next unfinished hypothesis/step.
