#!/usr/bin/env python3
"""End-to-end verification suite for Auto Research Hub."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HUB = Path(os.environ.get("AUTO_RESEARCH_HOME") or Path(__file__).resolve().parents[1]).resolve()
USER_HOME = Path.home().resolve()
PLUGIN_DIR = HUB / "plugins" / "auto-research"
MCP_SCRIPT = HUB / "mcp" / "codex_astra_mcp.py"
PRE_HOOK = HUB / "hooks" / "pre_tool_guard.py"
STOP_HOOK = HUB / "hooks" / "stop_goal_gate.py"
INIT_SH = HUB / "scripts" / "init_experiment.sh"
LOG_PY = HUB / "scripts" / "log_experiment.py"
CLUSTER_CTL = HUB / "scripts" / "cluster_ctl.py"
MIHOMO_SETUP = HUB / "proxy" / "setup_local_mihomo.py"
LAUNCHER = HUB / "bin" / "agy-research"
PYTHON_BIN = shutil.which("/usr/bin/python3") or sys.executable


def check(cond: bool, label: str, detail: str = "") -> None:
    if not cond:
        print(f"[FAIL] {label}" + (f": {detail}" if detail else ""))
        raise AssertionError(label)
    print(f"[ OK ] {label}" + (f" ({detail})" if detail else ""))


def ensure_plugin_rendered() -> None:
    for stem in ("mcp_config", "hooks"):
        tpl = HUB / "templates" / "plugin" / f"{stem}.template.json"
        dst = PLUGIN_DIR / f"{stem}.json"
        if tpl.is_file() and not dst.is_file():
            dst.write_text(
                tpl.read_text(encoding="utf-8")
                .replace("{{HUB_ROOT}}", str(HUB))
                .replace("{{PYTHON_BIN}}", PYTHON_BIN),
                encoding="utf-8",
            )


def run_json_hook(script: Path, payload: dict, extra_env: dict[str, str] | None = None) -> dict:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [PYTHON_BIN, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    check(proc.returncode == 0, f"{script.name} exit 0", proc.stderr.strip())
    return json.loads(proc.stdout.strip() or "{}")


def test_binaries_and_plugin() -> None:
    ensure_plugin_rendered()
    agy = shutil.which("agy") or str(USER_HOME / ".local" / "bin" / "agy")
    codex = shutil.which("codex") or str(USER_HOME / ".local" / "bin" / "codex")
    check(os.path.isfile(agy) and os.access(agy, os.X_OK), "agy binary executable", agy)
    check(os.path.isfile(codex) and os.access(codex, os.X_OK), "codex binary executable", codex)

    launcher_txt = LAUNCHER.read_text(encoding="utf-8")
    check("SANDBOXED=0" in launcher_txt and '{"enableTerminalSandbox":false}' in launcher_txt, "agy-research defaults to unsandboxed SSH/GPU/network")

    res = subprocess.run(
        [agy, "plugin", "validate", "--strict", str(PLUGIN_DIR)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    check(
        res.returncode == 0,
        "agy plugin validate --strict",
        (res.stdout + " " + res.stderr).strip(),
    )


def test_mcp_protocol_and_ultra_default(mock_cfg_env: dict[str, str], mock_raw_dir: str) -> None:
    mcp_src = MCP_SCRIPT.read_text(encoding="utf-8")
    check('DEFAULT_EFFORT = os.environ.get("ASTRA_REASONING_EFFORT", "ultra")' in mcp_src, "MCP default reasoning_effort is ultra")

    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "self-check", "version": "1.2"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "astra_code",
                "arguments": {"prompt": "test", "cwd": mock_raw_dir},
            },
        },
    ]
    stdin_data = "\n".join(json.dumps(r) for r in requests) + "\n"
    env = os.environ.copy()
    env.update(mock_cfg_env)
    proc = subprocess.run(
        [PYTHON_BIN, "-u", str(MCP_SCRIPT)],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    check(proc.returncode == 0, "MCP stdio process clean exit", proc.stderr.strip())
    lines = [json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip()]
    check(len(lines) == 3, "MCP responses count (notification produces no reply)", f"got {len(lines)}")
    tools = lines[1]["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    check(
        tool_names == ["astra_consult", "astra_code", "astra_resume", "astra_review"],
        "MCP tools/list exposes all 4 Astra tools",
        ",".join(tool_names),
    )
    for t in tools:
        eff_enum = t["inputSchema"]["properties"]["reasoning_effort"]["enum"]
        check("ultra" in eff_enum, f"{t['name']} supports reasoning_effort=ultra")
    check(lines[2]["result"]["isError"] is True, "MCP blocks writes to configured frozen_readonly_paths")


def test_cluster_and_proxy_clis(mock_cfg_env: dict[str, str], mock_raw_dir: str) -> None:
    res_help = subprocess.run(
        [PYTHON_BIN, str(CLUSTER_CTL), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    check(res_help.returncode == 0 and "recommend-gpus" in res_help.stdout, "cluster_ctl.py CLI ready")

    env = os.environ.copy()
    env.update(mock_cfg_env)
    res_forbid = subprocess.run(
        [
            PYTHON_BIN,
            str(CLUSTER_CTL),
            "sync",
            "--src",
            "/tmp",
            "--dst",
            f"{mock_raw_dir}/sub",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    check(res_forbid.returncode != 0, "cluster_ctl.py blocks sync into frozen_readonly_paths")

    res_mihomo = subprocess.run(
        [PYTHON_BIN, str(MIHOMO_SETUP), "setup", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    check(res_mihomo.returncode == 0 and "--prompt-url" in res_mihomo.stdout, "setup_local_mihomo.py CLI ready")


def test_hooks_and_project_lifecycle(mock_cfg_env: dict[str, str], mock_raw_dir: str) -> None:
    r_slurm = run_json_hook(
        PRE_HOOK,
        {"toolCall": {"name": "run_command", "args": {"CommandLine": "sbatch train.sh"}}, "cwd": "/tmp"},
        mock_cfg_env,
    )
    check(r_slurm.get("decision") == "force_ask", "PreToolUse gates Slurm sbatch with force_ask")

    r_reserve_stop = run_json_hook(
        PRE_HOOK,
        {"toolCall": {"name": "run_command", "args": {"CommandLine": "gpu_reserve_24h/control.sh stop --gpus 7"}}, "cwd": "/tmp"},
        mock_cfg_env,
    )
    check(r_reserve_stop.get("decision") == "deny", "PreToolUse blocks control.sh stop")

    r_reserve_watch_all = run_json_hook(
        PRE_HOOK,
        {"toolCall": {"name": "run_command", "args": {"CommandLine": "gpu_reserve_24h/control.sh watch --all"}}, "cwd": "/tmp"},
        mock_cfg_env,
    )
    check(r_reserve_watch_all.get("decision") == "deny", "PreToolUse blocks control.sh watch --all")

    r_reserve_release_ok = run_json_hook(
        PRE_HOOK,
        {"toolCall": {"name": "run_command", "args": {"CommandLine": "gpu_reserve_24h/control.sh release --gpus 7"}}, "cwd": "/tmp"},
        mock_cfg_env,
    )
    check(r_reserve_release_ok == {"decision": "allow"}, "PreToolUse allows valid control.sh release --gpus <ID>")

    r_secret = run_json_hook(
        PRE_HOOK,
        {"toolCall": {"name": "run_command", "args": {"CommandLine": "cat ~/agent.md"}}, "cwd": "/tmp"},
        mock_cfg_env,
    )
    check(r_secret.get("decision") == "deny", "PreToolUse blocks printing agent.md")

    r_codex_unsafe = run_json_hook(
        PRE_HOOK,
        {"toolCall": {"name": "run_command", "args": {"CommandLine": "codex exec -s danger-full-access 'hi'"}}, "cwd": "/tmp"},
        mock_cfg_env,
    )
    check(r_codex_unsafe.get("decision") == "deny", "PreToolUse blocks codex danger-full-access")

    r_raw_write = run_json_hook(
        PRE_HOOK,
        {"toolCall": {"name": "write_file", "args": {"Path": f"{mock_raw_dir}/test.txt"}}, "cwd": "/tmp"},
        mock_cfg_env,
    )
    check(r_raw_write.get("decision") == "deny", "PreToolUse blocks write_file on frozen_readonly_paths")

    r_safe_cmd = run_json_hook(
        PRE_HOOK,
        {"toolCall": {"name": "run_command", "args": {"CommandLine": "pytest -q"}}, "cwd": "/tmp"},
        mock_cfg_env,
    )
    check(r_safe_cmd == {"decision": "allow"}, "PreToolUse explicitly returns {\"decision\": \"allow\"} on safe command")

    r_safe_write = run_json_hook(
        PRE_HOOK,
        {"toolCall": {"name": "write_file", "args": {"Path": "/tmp/ok.txt"}}, "cwd": "/tmp"},
        mock_cfg_env,
    )
    check(r_safe_write == {"decision": "allow"}, "PreToolUse explicitly returns {\"decision\": \"allow\"} on safe write_file")

    with tempfile.TemporaryDirectory(prefix="ar_smoke_") as tmp:
        exp_dir = Path(tmp) / "smoke_exp"
        init_res = subprocess.run(
            ["bash", str(INIT_SH), str(exp_dir), "Verify smoke pipeline"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        check(init_res.returncode == 0, "init_experiment.sh creates Git-backed experiment", init_res.stdout.splitlines()[0])

        s_inactive = run_json_hook(
            STOP_HOOK,
            {"terminationReason": "NO_TOOL_CALL", "continuationActive": False, "cwd": str(exp_dir)},
        )
        check(s_inactive.get("decision") == "stop", "Stop hook allows stop when goal active=false")

        goal_path = exp_dir / "RESEARCH_GOAL.json"
        goal_cfg = json.loads(goal_path.read_text(encoding="utf-8"))
        goal_cfg["active"] = True
        goal_cfg["min_experiments"] = 1
        goal_path.write_text(json.dumps(goal_cfg, indent=2), encoding="utf-8")

        s_unmet = run_json_hook(
            STOP_HOOK,
            {"terminationReason": "NO_TOOL_CALL", "continuationActive": False, "cwd": str(exp_dir)},
        )
        check(s_unmet.get("decision") == "continue", "Stop hook re-enters loop when active goal is unmet")

        s_loop_guard = run_json_hook(
            STOP_HOOK,
            {"terminationReason": "NO_TOOL_CALL", "continuationActive": True, "cwd": str(exp_dir)},
        )
        check(s_loop_guard.get("decision") == "stop", "Stop hook honors continuationActive loop guard")

        log_res = subprocess.run(
            [
                PYTHON_BIN,
                str(LOG_PY),
                "--project-dir",
                str(exp_dir),
                "--exp-id",
                "exp00",
                "--hypothesis",
                "Baseline check",
                "--metric",
                "acc=90.0%",
                "--decision",
                "baseline",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        check(log_res.returncode == 0, "log_experiment.py records trial into EXPERIMENTS.tsv")

        goal_cfg["completed"] = True
        goal_path.write_text(json.dumps(goal_cfg, indent=2), encoding="utf-8")
        s_done = run_json_hook(
            STOP_HOOK,
            {"terminationReason": "NO_TOOL_CALL", "continuationActive": False, "cwd": str(exp_dir)},
        )
        check(s_done.get("decision") == "stop", "Stop hook allows stop once completed=true")


def main() -> int:
    print("=== Auto Research Infrastructure Self-Check (v1.2 Portable) ===")
    with tempfile.TemporaryDirectory(prefix="ar_cfg_") as tmp:
        mock_raw = Path(tmp) / "mock_frozen_raw"
        mock_raw.mkdir(parents=True, exist_ok=True)
        mock_raw_str = str(mock_raw)
        cfg_file = Path(tmp) / "cluster.test.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "nodes": {"local": {"ip": "127.0.0.1", "user": USER_HOME.name, "home": str(USER_HOME), "disks": ["/tmp"]}},
                    "frozen_readonly_paths": [mock_raw_str],
                    "gpu_auto_reserve": {"service_name": "gpu-auto-reserve.service"},
                }
            ),
            encoding="utf-8",
        )
        mock_env = {"AUTO_RESEARCH_CLUSTER_CONFIG": str(cfg_file)}
        test_binaries_and_plugin()
        test_mcp_protocol_and_ultra_default(mock_env, mock_raw_str)
        test_cluster_and_proxy_clis(mock_env, mock_raw_str)
        test_hooks_and_project_lifecycle(mock_env, mock_raw_str)
    print("=== ALL CHECKS PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
