#!/usr/bin/env python3
"""Antigravity Stop hook gatekeeper for autonomous Auto Research runs."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

HUB_ROOT = Path(os.environ.get("AUTO_RESEARCH_HOME") or Path(__file__).resolve().parents[1]).resolve()
USER_HOME = Path.home().resolve()
PROJECTS_DIR = HUB_ROOT / "projects"
LOG_FILE = HUB_ROOT / "logs" / "hooks.log"


def log_event(decision: str, reason: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().isoformat(timespec="seconds")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{ts}\tStop\t{decision}\t{reason}\n")
    except Exception:
        pass


def find_goal_file(cwd: Path, workspace_paths: list[str], conversation_id: str) -> Path | None:
    stop_roots = {Path(p).resolve(strict=False) for p in workspace_paths if p}
    stop_roots.add(USER_HOME)

    cur = cwd.resolve(strict=False)
    for _ in range(12):
        candidate = cur / "RESEARCH_GOAL.json"
        if candidate.is_file():
            return candidate
        if cur in stop_roots or cur.parent == cur:
            break
        cur = cur.parent

    if cur in (USER_HOME, HUB_ROOT) and PROJECTS_DIR.is_dir():
        conv_matches: list[Path] = []
        unclaimed_active: list[Path] = []
        for gp in sorted(PROJECTS_DIR.glob("*/RESEARCH_GOAL.json")):
            try:
                cfg = json.loads(gp.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not cfg.get("active", False) or cfg.get("completed", False):
                continue
            bound_cid = str(cfg.get("conversation_id") or "").strip()
            if conversation_id and bound_cid == conversation_id:
                conv_matches.append(gp)
            elif not bound_cid:
                unclaimed_active.append(gp)
        if conv_matches:
            return max(conv_matches, key=lambda p: p.stat().st_mtime)
        if len(unclaimed_active) == 1:
            return unclaimed_active[0]

    return None


def count_experiments(exp_tsv: Path) -> int:
    if not exp_tsv.is_file():
        return 0
    lines = [
        ln.strip()
        for ln in exp_tsv.read_text(encoding="utf-8", errors="replace").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    return max(0, len(lines) - 1)


def evaluate_goal(goal_path: Path) -> str | None:
    cfg = json.loads(goal_path.read_text(encoding="utf-8"))
    if not cfg.get("active", False) or cfg.get("completed", False):
        return None

    proj_dir = goal_path.parent
    unmet: list[str] = []

    min_exps = int(cfg.get("min_experiments") or 0)
    if min_exps > 0:
        done_exps = count_experiments(proj_dir / "EXPERIMENTS.tsv")
        if done_exps < min_exps:
            unmet.append(f"`EXPERIMENTS.tsv` logged {done_exps}/{min_exps} required experiments")

    req_files = cfg.get("required_files") or []
    for rel in req_files:
        fp = proj_dir / str(rel)
        if not fp.exists() or (fp.is_file() and fp.stat().st_size == 0):
            unmet.append(f"required deliverable `{rel}` is missing or empty")

    objective = str(cfg.get("objective") or "See RESEARCH_PLAN.md").strip()
    if unmet:
        details = "; ".join(unmet)
        return (
            f"[Auto-Research Goal Gate] Active research goal in `{goal_path}` is not yet satisfied ({details}). "
            f"Objective: {objective}. Continue the hypothesis -> implementation -> verification -> `EXPERIMENTS.tsv` "
            f"loop (or consult Astra via `astra_consult` if blocked). Once all acceptance criteria are genuinely met, "
            f"update `{goal_path}` with `\"completed\": true`."
        )

    return (
        f"[Auto-Research Goal Gate] `{goal_path}` still has `\"active\": true` and `\"completed\": false` "
        f"(Objective: {objective}). Verify that all acceptance criteria in `RESEARCH_PLAN.md` and `{goal_path}` "
        f"are genuinely satisfied; if more experiments are needed, run them now, otherwise set `\"completed\": true` "
        f"in `{goal_path}` and present the final summary."
    )


def main() -> int:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.stdout.write(json.dumps({"decision": "stop"}) + "\n")
            return 0
        payload: dict[str, Any] = json.loads(raw)
        term_reason = str(payload.get("terminationReason") or "")
        cont_active = bool(payload.get("continuationActive", False))

        if term_reason != "NO_TOOL_CALL" or cont_active:
            sys.stdout.write(json.dumps({"decision": "stop"}) + "\n")
            return 0

        cwd = Path(str(payload.get("cwd") or USER_HOME))
        ws_paths = [str(p) for p in (payload.get("workspacePaths") or [])]
        conv_id = str(payload.get("conversationId") or "").strip()
        goal_file = find_goal_file(cwd, ws_paths, conv_id)
        if goal_file is None:
            sys.stdout.write(json.dumps({"decision": "stop"}) + "\n")
            return 0

        reason = evaluate_goal(goal_file)
        if reason:
            log_event("continue", reason)
            sys.stdout.write(json.dumps({"decision": "continue", "reason": reason}, ensure_ascii=False) + "\n")
        else:
            sys.stdout.write(json.dumps({"decision": "stop"}) + "\n")
        sys.stdout.flush()
        return 0
    except Exception as exc:
        log_event("error_stop", str(exc))
        sys.stdout.write(json.dumps({"decision": "stop"}) + "\n")
        sys.stdout.flush()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
