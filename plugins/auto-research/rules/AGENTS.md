# Auto Research Operating Discipline (Carbon × Astra Ultra, Multi-Node GPU)

When working inside an Auto Research project (any repository containing `RESEARCH_PLAN.md` or `RESEARCH_GOAL.json`, or tasks under the Auto Research hub):

1. **Closed-Loop Scientific Execution**:
   - Follow `Hypothesis -> Minimal Implementation -> Reproducible Evaluation -> Ledger Recording` on every iteration.
   - Record every completed baseline or ablation trial into `EXPERIMENTS.tsv` (using `scripts/log_experiment.py` in the Auto Research hub) with exact Git commit SHA, node/GPU used, key hyperparameters, primary metric(s), and keep/discard conclusion. Never fabricate or extrapolate metrics.
   - Monitor long local or remote GPU jobs asynchronously via `schedule` + `scripts/cluster_ctl.py check-job` rather than `sleep` loops.

2. **Single-Writer Dual-Agent Collaboration (Gemini 4 Carbon + GPT-6.2-Astra `ultra`)**:
   - You (Gemini 4 Carbon) own the primary working tree, experiment ledger, and cross-node GPU scheduling.
   - Use the `auto-research-codex` MCP tools (`astra_consult`, `astra_code`, `astra_resume`, `astra_review`) — all configured to **GPT-6.2-Astra (`ultra` reasoning effort)** by default.
   - Default to read-only consultation (`astra_consult` / `astra_review`); when delegating code authoring (`astra_code`), keep `use_worktree: true` so Astra edits an isolated Git worktree, then verify and merge cleanly.
   - Escalate to Astra (`ultra`) at high-leverage points: (a) 0-to-1 core algorithm/module authoring, (b) >=2 consecutive stalled iterations or unresolved bugs, (c) mandatory `astra_review` before launching multi-GPU or cross-node training runs, or (d) mathematical/statistical verification.

3. **Multi-Node GPU Scheduling, Standby Reserve SOP & Storage**:
   - Never echo SSH credentials or subscription secrets to stdout, argv, or logs. Always use `scripts/cluster_ctl.py` (zero-echo SSH built in, configured via `config/cluster.local.json`).
   - **Standby GPU Reserve SOP (when `RESERVED_BY_US` / `gpu_reserve_24h` is active on a host)**:
     1. Run `cluster_ctl.py status`: `RESERVED_BY_US` GPUs appear ~100% busy in raw `nvidia-smi` because our standby daemon holds them, and ARE claimable; `EXCLUDED_IN_USE` GPUs belong to active production or other sessions — **never touch another task's `excluded_gpus`**.
     2. Before launching on a `RESERVED_BY_US` GPU `<ID>`, atomically claim & exempt it with `control.sh release --gpus <ID>` (never use `stop` or `kill`, never omit `--gpus`, and keep `<ID>` exempted across all rounds of an Auto Research loop).
     3. Bind explicitly with `export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=<ID>` (`cluster_ctl.py exec/launch-bg --gpus <ID>` sets both automatically).
     4. Return borrowed `<ID>` via `control.sh watch --gpus <ID>` only after ALL experiment rounds finish (never run `watch --all`).
   - **Storage & Transfer**: `/mnt/data*` disks are node-local (not shared across hosts); shared NAS mounts are read directly where mounted (frozen raw archives are strictly read-only); use `cluster_ctl.py sync` (`rsync`) to push code to remote nodes and pull metrics/winning checkpoints back. Read `skills/auto-research/references/cluster_gpu_storage.md` before cross-node runs.
