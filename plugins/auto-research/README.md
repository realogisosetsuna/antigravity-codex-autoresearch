# Auto Research & Astra Bridge (`auto-research`)

Native Antigravity plugin bundling everything needed for long-horizon autonomous research with **Gemini 4 Carbon** and **GPT-6.2-Astra (`ultra`)**:

- **MCP Server (`auto-research-codex`)**: Exposes `astra_consult`, `astra_code`, `astra_resume`, and `astra_review` backed by local `codex exec` / `codex exec review` (`ultra` effort by default) with enforced `read-only` / `workspace-write` sandboxing and audit logs.
- **Lifecycle Hooks (`hooks.json`)**:
  - `PreToolUse` (`pre_tool_guard.py`): Blocks accidental deletion of configured frozen raw archives or critical roots, blocks echoing credential/subscription secrets, protects standby GPU reserve daemons, and blocks `danger-full-access` Codex invocations.
  - `Stop` (`stop_goal_gate.py`): Re-enters the research loop once per turn when an active `RESEARCH_GOAL.json` contract is not yet completed.
- **Rules & Skills**: Always-on scientific ledger & single-writer discipline (`rules/AGENTS.md`) plus on-demand `auto-research` and `astra-collab` runbooks.
