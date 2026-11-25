# Antigravity Dynamic Workflow (`run_workflow` script)
# Fan-out competing research hypotheses across isolated Carbon explorers, then cross-audit with GPT-6.2-Astra.
#
# Invoke via `run_workflow`:
#   ScriptPath="<auto_research_dir>/workflows/dual_hypothesis_tournament.py"
#   Args={
#       "project_dir": "<auto_research_dir>/projects/demo",
#       "question": "How to reduce false positives on noisy short utterances?",
#       "hypotheses": [
#           "Energy/VAD-gated frame pooling before classifier head",
#           "Focal + label-smoothing calibration with duration-aware temperature"
#       ]
#   }

project_dir = args.get("project_dir", WORKSPACES[0] if WORKSPACES else ".")
question = args.get("question", "Identify the highest-expected-value next experiment.")
hypotheses = args.get("hypotheses") or []

PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "hypothesis": {"type": "string"},
        "minimal_change": {"type": "string"},
        "verification_cmd": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "expected_signal": {"type": "string"},
    },
    "required": ["hypothesis", "minimal_change", "verification_cmd", "risks", "expected_signal"],
}

RANKING_SCHEMA = {
    "type": "object",
    "properties": {
        "winner": {"type": "string"},
        "ranking_rationale": {"type": "string"},
        "astra_critique_summary": {"type": "string"},
        "execution_checklist": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["winner", "ranking_rationale", "astra_critique_summary", "execution_checklist"],
}

if not hypotheses:
    phase("Generate Candidate Hypotheses")
    gen_schema = {
        "type": "object",
        "properties": {"hypotheses": {"type": "array", "items": {"type": "string"}}},
        "required": ["hypotheses"],
    }
    generated = await agent(
        f"Inspect `{project_dir}` (`RESEARCH_PLAN.md`, `EXPERIMENTS.tsv`, `BLOCKERS.md`, and code). "
        f"Propose 3 distinct, falsifiable hypotheses for: {question}",
        schema=gen_schema,
        role="Hypothesis Generator",
    )
    hypotheses = generated["hypotheses"][:3]

log(f"Evaluating {len(hypotheses)} hypotheses for {project_dir}")

phase("Parallel Hypothesis Design")
proposals = await parallel(
    hypotheses,
    lambda h: agent(
        f"Project directory: `{project_dir}`\nResearch question: {question}\n"
        f"Design the smallest rigorous experiment to test hypothesis: {h}\n"
        f"Read existing code/configs in `{project_dir}` (read-only) and specify exact files, metrics, and failure modes.",
        schema=PROPOSAL_SCHEMA,
        role=f"Design: {h[:36]}",
    ),
)

phase("Astra Cross-Review & Final Ranking")
ranking = await agent(
    f"Project directory: `{project_dir}`\nResearch question: {question}\n"
    f"Candidate proposals JSON:\n{proposals}\n\n"
    f"Call the `astra_consult` MCP tool (`cwd='{project_dir}'`) to get GPT-6.2-Astra's independent "
    f"ranking, blind-spot check, and data-leakage critique of these proposals, then synthesize the "
    f"winning experiment plan.",
    schema=RANKING_SCHEMA,
    role="Carbon + Astra Judge",
)

log(f"Winner: {ranking['winner']}")
print(ranking)
