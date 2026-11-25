---
name: astra-collab
description: >-
  Protocol for pairing Gemini 4 Carbon (Antigravity) with GPT-6.2-Astra (`ultra` reasoning effort
  by default) via the `auto-research-codex` MCP server (`astra_consult`, `astra_code`,
  `astra_resume`, `astra_review`). Activate whenever you need Astra Ultra to author complex
  experiment code, audit changes before GPU runs, debate hypotheses, or break research blockers.
---

# Carbon × Astra Ultra Collaboration Guide

All 4 tools in the `auto-research-codex` MCP server invoke **GPT-6.2-Astra** with **`reasoning_effort="ultra"`** (*Maximum reasoning with automatic task delegation*) by default.

## 1. Escalation Policy (When to Call Astra Ultra)

Handle routine file edits, data inspection, glue scripts, and cluster scheduling yourself. Call Astra (`ultra`) at high-leverage inflection points:
1. **0-to-1 Core Module Implementation (`astra_code`)**: Custom losses, samplers, model architectures, complex feature pipelines, or distributed evaluation logic.
2. **Stuck >= 2 Iterations (`astra_consult` -> `astra_resume`)**: Non-converging loss, NaN/OOM mysteries, metric regressions, or conflicting empirical results. Summarize evidence in `BLOCKERS.md` first and point Astra at exact files/logs.
3. **Pre-Flight Audit Before GPU Runs (`astra_review`)**: Mandatory independent check for train/val/test leakage, metric bugs, shape/broadcasting traps, cross-node path mismatches, and `CUDA_VISIBLE_DEVICES` safety before multi-hour or cross-node GPU runs.
4. **Mathematical / Statistical Cross-Check (`astra_consult`)**: Independent verification of derivations, normalization factors, or significance tests.

## 2. Tool Parameters & Worktree Merge Flow

- **`astra_consult(prompt, cwd, reasoning_effort="ultra", output_schema=...)`**:
  - Strict `read-only` mode. Pass file paths (`RESEARCH_PLAN.md`, `EXPERIMENTS.tsv`, `BLOCKERS.md`, `src/...`, error logs) rather than pasting large files—Astra inspects them directly.
  - Save the returned `thread_id` for multi-turn follow-up.
- **`astra_resume(thread_id, prompt, cwd, writable=False, reasoning_effort="ultra")`**:
  - Resumes the exact Codex session (`thread_id`) after running new experiments or counter-tests.
- **`astra_code(prompt, cwd, use_worktree=True, enable_network=True, add_dirs=[...], reasoning_effort="ultra")`**:
  - Astra implements and tests the module in an isolated Git worktree (`workspace-write` + network enabled) and reports its worktree path and branch.
  - **Integration step (owned by Carbon)**:
    1. Inspect diff (`git -C <cwd> diff HEAD..<astra_branch>` or against the reported worktree path);
    2. Run smoke test locally or on target GPU node via `cluster_ctl.py exec`;
    3. Merge/cherry-pick into the main branch and log the trial in `EXPERIMENTS.tsv`.
- **`astra_review(cwd, base_branch=..., commit_sha=..., focus_prompt=..., reasoning_effort="ultra")`**:
  - Resolve all `[BLOCKER]` and `[MAJOR]` findings before launching full GPU training.
