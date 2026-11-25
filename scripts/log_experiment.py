#!/usr/bin/env python3
"""Append or inspect structured Auto Research trials in EXPERIMENTS.tsv."""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
from pathlib import Path

HEADER = [
    "timestamp",
    "exp_id",
    "git_sha",
    "actor",
    "hypothesis",
    "config_summary",
    "primary_metric",
    "guardrail_metrics",
    "decision",
    "notes",
]


def clean_cell(val: str) -> str:
    return " ".join((val or "").replace("\t", " ").splitlines()).strip() or "-"


def detect_git_sha(project_dir: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(project_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            dirty = subprocess.run(
                ["git", "-C", str(project_dir), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            suffix = "-dirty" if dirty.returncode == 0 and dirty.stdout.strip() else ""
            return r.stdout.strip() + suffix
    except Exception:
        pass
    return "no-git"


def show_ledger(tsv_path: Path) -> int:
    if not tsv_path.is_file():
        print(f"No EXPERIMENTS.tsv found at {tsv_path}")
        return 1
    lines = [ln for ln in tsv_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    for idx, ln in enumerate(lines):
        prefix = "HDR" if idx == 0 else f"#{idx:02d}"
        print(f"[{prefix}] {ln}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Log or view Auto Research experiments in EXPERIMENTS.tsv")
    parser.add_argument("--project-dir", default=".", help="Path to the experiment project root")
    parser.add_argument("--show", action="store_true", help="Print current EXPERIMENTS.tsv rows and exit")
    parser.add_argument("--exp-id", help="Short experiment identifier, e.g. exp01")
    parser.add_argument("--git-sha", default="", help="Git commit SHA (auto-detected if omitted)")
    parser.add_argument("--actor", default="carbon", help="Primary author/model (e.g., carbon, astra, carbon+astra)")
    parser.add_argument("--hypothesis", help="Falsifiable hypothesis tested in this run")
    parser.add_argument("--config", default="-", help="Key hyperparameters / data slice summary")
    parser.add_argument("--metric", help="Primary metric result, e.g. val_wer=11.82%")
    parser.add_argument("--guardrails", default="ok", help="Guardrail metrics summary")
    parser.add_argument("--decision", choices=["keep", "discard", "investigate", "baseline"], default="investigate")
    parser.add_argument("--notes", default="-", help="Short conclusion or follow-up pointer")
    args = parser.parse_args()

    proj_dir = Path(args.project_dir).expanduser().resolve()
    tsv_path = proj_dir / "EXPERIMENTS.tsv"

    if args.show:
        return show_ledger(tsv_path)

    if not args.exp_id or not args.hypothesis or not args.metric:
        parser.error("--exp-id, --hypothesis, and --metric are required unless --show is passed.")

    if not tsv_path.exists():
        tsv_path.write_text("\t".join(HEADER) + "\n", encoding="utf-8")

    sha = args.git_sha.strip() or detect_git_sha(proj_dir)
    ts = dt.datetime.now().isoformat(timespec="seconds")
    row = [
        ts,
        clean_cell(args.exp_id),
        clean_cell(sha),
        clean_cell(args.actor),
        clean_cell(args.hypothesis),
        clean_cell(args.config),
        clean_cell(args.metric),
        clean_cell(args.guardrails),
        clean_cell(args.decision),
        clean_cell(args.notes),
    ]
    with tsv_path.open("a", encoding="utf-8") as f:
        f.write("\t".join(row) + "\n")

    print(f"Logged {args.exp_id} ({sha}) -> {tsv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
