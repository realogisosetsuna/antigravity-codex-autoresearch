#!/usr/bin/env python3
"""Antigravity PreToolUse deterministic safety gate for long-running Auto Research.

Contract (protojson on stdin -> JSON on stdout):
- `{"decision": "deny", "reason": "..."}`      : hard-block irreversible/policy-violating actions
- `{"decision": "force_ask", "reason": "..."}` : require explicit human confirmation for high-impact actions
- `{"decision": "allow"}`                      : pass authorized operations cleanly for unattended execution
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

HUB_ROOT = Path(os.environ.get("AUTO_RESEARCH_HOME") or Path(__file__).resolve().parents[1]).resolve()
sys.path.insert(0, str(HUB_ROOT / "scripts"))
from ar_config import USER_HOME, load_cluster_config  # noqa: E402

LOG_FILE = HUB_ROOT / "logs" / "hooks.log"
ALLOW_DECISION: dict[str, Any] = {"decision": "allow"}


def build_guard_context() -> dict[str, Any]:
    cfg, _ = load_cluster_config()
    frozen = tuple(str(p) for p in (cfg.get("frozen_readonly_paths") or []) if p)
    prod = tuple(str(p) for p in (cfg.get("production_protected_paths") or []) if p)
    cred_file = str(cfg.get("credential_file") or (USER_HOME / "agent.md"))
    reserve_cfg = cfg.get("gpu_auto_reserve") or {}
    reserve_svc = str(reserve_cfg.get("service_name") or "").strip()

    user_name = USER_HOME.name
    roots = {
        "/",
        "/mnt",
        "/mnt/data",
        "/mnt/data1",
        "/mnt/data2",
        "/mnt/data3",
        "/mnt/data4",
        f"/mnt/data/{user_name}",
        f"/mnt/data1/{user_name}",
        f"/mnt/data2/{user_name}",
        f"/mnt/data3/{user_name}",
        f"/mnt/data4/{user_name}",
        "/mnt/nas2",
        f"/mnt/nas2/{user_name}",
        "/mnt/workspace",
        "/home",
        str(USER_HOME),
        "~",
    }
    return {
        "frozen": frozen,
        "prod": prod,
        "cred_file": cred_file,
        "reserve_svc": reserve_svc,
        "roots": roots,
    }


def log_event(decision: str, tool_name: str, reason: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().isoformat(timespec="seconds")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{ts}\tPreToolUse\t{decision}\t{tool_name}\t{reason}\n")
    except Exception:
        pass


def norm_path(p: str, cwd: str) -> str:
    if not p:
        return ""
    expanded = Path(p).expanduser()
    if not expanded.is_absolute() and cwd:
        expanded = Path(cwd) / expanded
    try:
        return str(expanded.resolve(strict=False))
    except Exception:
        return str(expanded)


def check_file_mutation(target_path: str, ctx: dict[str, Any]) -> dict[str, Any]:
    for frozen in ctx["frozen"]:
        f_res = str(Path(frozen).resolve(strict=False))
        if target_path == f_res or target_path.startswith(f_res + "/"):
            return {
                "decision": "deny",
                "reason": f"Blocked by Auto-Research Guard: `{target_path}` is inside frozen raw data archive `{frozen}`.",
            }

    cred_res = str(Path(ctx["cred_file"]).resolve(strict=False))
    if target_path == cred_res:
        return {
            "decision": "force_ask",
            "reason": f"Modifying `{cred_res}` (SSH credential store) requires explicit human approval.",
        }

    root_agents = str((USER_HOME / "AGENTS.md").resolve(strict=False))
    if target_path == root_agents:
        return {
            "decision": "force_ask",
            "reason": f"Modifying `{root_agents}` affects all workspace agents; please confirm.",
        }

    for prod in ctx["prod"]:
        p_res = str(Path(prod).resolve(strict=False))
        if target_path == p_res or target_path.startswith(p_res + "/"):
            return {
                "decision": "force_ask",
                "reason": f"Writing directly inside formal production directory `{prod}` requires human confirmation.",
            }

    return ALLOW_DECISION


def check_shell_command(cmd: str, cwd: str, ctx: dict[str, Any]) -> dict[str, Any]:
    s = cmd.strip()
    if not s:
        return ALLOW_DECISION

    # 1. Enforce GPU auto-reserve daemon redlines when configured
    reserve_svc = ctx["reserve_svc"]
    if reserve_svc:
        svc_stem = reserve_svc.removesuffix(".service")
        if re.search(re.escape(svc_stem) + r"(\.service)?", s) and re.search(
            r"\bsystemctl\b.*\b(stop|disable|mask)\b", s
        ):
            return {
                "decision": "deny",
                "reason": f"Blocked by Auto-Research Guard: Stopping or disabling `{reserve_svc}` is prohibited.",
            }

    if re.search(r"(gpu_reserve_24h/control\.sh|\breserve\.py)\b", s):
        if re.search(r"(control\.sh|\breserve\.py)\s+stop\b", s):
            return {
                "decision": "deny",
                "reason": "Blocked by Auto-Research Guard: Never use `control.sh stop` (auto_reserve will re-seize the GPU in 1s or release all standby GPUs). Use `control.sh release --gpus <ID>` instead.",
            }
        if re.search(r"(control\.sh|\breserve\.py)\s+release\b", s) and "--gpus" not in s:
            return {
                "decision": "deny",
                "reason": "Blocked by Auto-Research Guard: `control.sh release` without `--gpus <ID>` is prohibited (would release all standby GPUs).",
            }
        if re.search(r"(control\.sh|\breserve\.py)\s+watch\b", s) and (
            "--all" in s or "--gpus" not in s
        ):
            return {
                "decision": "deny",
                "reason": "Blocked by Auto-Research Guard: `control.sh watch --all` (or `watch` without `--gpus <ID>`) is prohibited because it clears `excluded_gpus` used by active production/sessions.",
            }

    # 2. Gate Slurm commands with explicit confirmation
    if re.search(r"(^|[;&|()\s])(sbatch|srun|salloc|scancel)\b", s):
        return {
            "decision": "force_ask",
            "reason": "Slurm submission/cancellation requires explicit human confirmation (Auto Research runs directly on authorized GPUs by default).",
        }

    # 3. Block sudo / system-level destructive utilities
    if re.search(r"(^|[;&|()\s])(sudo|mkfs(\.\w+)?|fdisk|parted|shutdown|reboot|poweroff)\b", s):
        return {
            "decision": "deny",
            "reason": "Blocked by Auto-Research Guard: System privilege escalation / disk formatting / power commands are prohibited.",
        }

    # 4. Prevent echoing or deleting credential / proxy subscription files
    if re.search(r"(agent\.md|\.codex/auth\.json|\.config/mihomo/ai-research)", s):
        if re.search(r"\b(rm|mv|cp)\b.*(agent\.md|\.config/mihomo/ai-research)", s):
            return {
                "decision": "deny",
                "reason": "Blocked by Auto-Research Guard: Deleting, moving, or copying credential/proxy config files is prohibited.",
            }
        if re.search(r"\b(cat|head|tail|nl|less|more|bat|sed|awk|grep|strings)\b.*(agent\.md|\.codex/auth\.json|\.config/mihomo/ai-research)", s):
            return {
                "decision": "deny",
                "reason": "Blocked by Auto-Research Guard: Never print `agent.md`, `auth.json`, or Mihomo subscription config contents to terminal stdout/logs.",
            }

    # 5. Prevent Codex CLI invocations with full unsandboxed bypass during automated runs
    if re.search(r"\bcodex\b", s) and (
        "--dangerously-bypass-approvals-and-sandbox" in s or "danger-full-access" in s
    ):
        return {
            "decision": "deny",
            "reason": "Blocked by Auto-Research Guard: Do not invoke `codex` with `danger-full-access` or `--dangerously-bypass-approvals-and-sandbox`; use `-s read-only`, `-s workspace-write`, or the `auto-research-codex` MCP tools.",
        }

    # 6. Protect frozen raw archive directories from mutation/deletion
    for frozen in ctx["frozen"]:
        base_name = Path(frozen).name
        if frozen in s or (base_name and f"{base_name}_nas2" in s):
            if re.search(r"\b(rm|rmdir|mv|truncate|dd|shred|chmod|chown)\b", s) or re.search(
                r"(>|>>)\s*[^\s]*" + re.escape(base_name), s
            ):
                return {
                    "decision": "deny",
                    "reason": f"Blocked by Auto-Research Guard: `{frozen}` is a frozen raw archive and must not be modified or deleted.",
                }

    # 7. Check `rm` commands against critical root paths and formal production directories
    if re.search(r"(^|[;&|()\s])rm\s+", s):
        for m in re.finditer(r"(?:^|[;&|()\s])rm\s+([^;&|\n]+)", s):
            arg_segment = m.group(1)
            tokens = [t.strip("\"'") for t in arg_segment.split() if not t.startswith("-")]
            for tok in tokens:
                clean = tok.rstrip("/") or "/"
                resolved = norm_path(clean, cwd).rstrip("/") or "/"
                if clean in ctx["roots"] or resolved in ctx["roots"] or tok in ("*", "/*", "~/*"):
                    return {
                        "decision": "deny",
                        "reason": f"Blocked by Auto-Research Guard: Refusing `rm` on critical root directory `{tok}` (`{resolved}`).",
                    }
                for prod in ctx["prod"]:
                    p_res = str(Path(prod).resolve(strict=False))
                    if resolved == p_res or resolved.startswith(p_res + "/"):
                        return {
                            "decision": "force_ask",
                            "reason": f"Deleting files under formal production directory `{prod}` requires explicit human confirmation.",
                        }

    # 8. Gate irreversible Git history rewrites with human confirmation
    if re.search(r"\bgit\s+(push\s+.*--force|push\s+-f\b|reset\s+--hard\b|clean\s+-[a-zA-Z]*f)", s):
        return {
            "decision": "force_ask",
            "reason": "Destructive Git operation (`push --force` / `reset --hard` / `clean -f`) requires human confirmation.",
        }

    return ALLOW_DECISION


def main() -> int:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.stdout.write(json.dumps(ALLOW_DECISION) + "\n")
            return 0
        payload = json.loads(raw)
        tool_call = payload.get("toolCall") or {}
        tool_name = str(tool_call.get("name") or "")
        args = tool_call.get("args") or {}
        cwd = str(payload.get("cwd") or USER_HOME)
        ctx = build_guard_context()

        result: dict[str, Any] = ALLOW_DECISION
        if tool_name in ("write_file", "edit_file"):
            target = norm_path(str(args.get("Path") or ""), cwd)
            result = check_file_mutation(target, ctx)
        elif tool_name == "run_command":
            cmd = str(args.get("CommandLine") or "")
            result = check_shell_command(cmd, cwd, ctx)

        if result.get("decision") != "allow":
            log_event(str(result.get("decision")), tool_name, str(result.get("reason", "")))
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 0
    except Exception as exc:
        log_event("error_allow", "unknown", str(exc))
        sys.stdout.write(json.dumps(ALLOW_DECISION) + "\n")
        sys.stdout.flush()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
