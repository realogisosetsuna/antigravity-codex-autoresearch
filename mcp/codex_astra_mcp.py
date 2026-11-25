#!/usr/bin/env python3
"""Stdio MCP Server bridging Antigravity (Gemini 4 Carbon) to OpenAI Codex CLI (GPT-6.2-Astra).

Zero third-party dependencies (Python 3.10+ stdlib only).
Runs under Antigravity's host Language Server (`ls_core`) outside the terminal sandbox,
while enforcing strict Codex-side sandboxing (`read-only` or `workspace-write`) on every call
so Codex never inherits global `danger-full-access`.
Defaults to GPT-6.2-Astra with `model_reasoning_effort = "ultra"`.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

HUB_ROOT = Path(os.environ.get("AUTO_RESEARCH_HOME") or Path(__file__).resolve().parents[1]).resolve()
sys.path.insert(0, str(HUB_ROOT / "scripts"))
from ar_config import USER_HOME, load_cluster_config  # noqa: E402

SERVER_NAME = "auto-research-codex"
SERVER_VERSION = "1.2.0"
SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}
DEFAULT_PROTOCOL = "2024-11-05"

CALL_LOG_DIR = HUB_ROOT / "logs" / "astra_calls"
DEFAULT_CWD = USER_HOME
DEFAULT_MODEL = os.environ.get("ASTRA_MODEL", "gpt-6.2-astra")
DEFAULT_EFFORT = os.environ.get("ASTRA_REASONING_EFFORT", "ultra")
VALID_EFFORTS = ["low", "medium", "high", "xhigh", "max", "ultra"]
DEFAULT_TIMEOUT_SEC = 900
MAX_TIMEOUT_SEC = 3600


def get_runtime_settings() -> tuple[tuple[str, ...], str, int]:
    cfg, _ = load_cluster_config()
    frozen = [str(p) for p in (cfg.get("frozen_readonly_paths") or []) if p]
    protected = tuple(
        frozen
        + [
            str(USER_HOME / ".gemini"),
            str(USER_HOME / ".codex"),
            str(USER_HOME / ".ssh"),
        ]
    )
    cred_file = str(cfg.get("credential_file") or (USER_HOME / "agent.md"))
    mihomo_port = int((cfg.get("local_mihomo") or {}).get("proxy_port") or 27890)
    return protected, cred_file, mihomo_port


def find_codex_bin() -> str:
    candidates = [
        os.environ.get("CODEX_BIN", ""),
        str(USER_HOME / ".local" / "bin" / "codex"),
        str(USER_HOME / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"),
        shutil.which("codex") or "",
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    raise FileNotFoundError("Cannot locate executable `codex` binary.")


def is_forbidden_write_dir(path_str: str) -> str | None:
    protected, _, _ = get_runtime_settings()
    resolved = str(Path(path_str).expanduser().resolve())
    for prefix in protected:
        p_res = str(Path(prefix).expanduser().resolve(strict=False))
        if resolved == p_res or resolved.startswith(p_res + "/"):
            return f"Directory `{resolved}` is protected against automated writes ({prefix})."
    return None


def check_git_state(cwd: Path) -> tuple[bool, bool]:
    """Return (is_git_worktree, has_head_commit)."""
    try:
        r1 = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r1.returncode != 0 or r1.stdout.strip() != "true":
            return False, False
        r2 = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return True, (r2.returncode == 0)
    except Exception:
        return False, False


def redact_sensitive(text: str) -> str:
    if not text:
        return text
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]{16,}", r"\1[REDACTED]", text)
    text = re.sub(r"(sk-[A-Za-z0-9_\-]{16,})", "[REDACTED_KEY]", text)
    return text


def write_audit_log(tool_name: str, record: dict[str, Any]) -> str:
    try:
        CALL_LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:8]
        out_path = CALL_LOG_DIR / f"{ts}_{tool_name}_{short_id}.json"
        out_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(out_path)
    except Exception as exc:
        sys.stderr.write(f"[{SERVER_NAME}] audit log warning: {exc}\n")
        return ""


def apply_smart_proxy_env(env: dict[str, str]) -> None:
    """Prefer 24/7 local Mihomo daemon whenever online so Astra survives SSH reverse-tunnel drops."""
    _, _, mihomo_port = get_runtime_settings()
    no_proxy = "127.0.0.1,localhost,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    env["NO_PROXY"] = no_proxy
    env["no_proxy"] = no_proxy
    try:
        with socket.create_connection(("127.0.0.1", mihomo_port), timeout=0.25):
            proxy_url = f"http://127.0.0.1:{mihomo_port}"
            for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
                env[k] = proxy_url
            return
    except OSError:
        pass


def run_codex_command(
    cmd: list[str],
    prompt_stdin: str | None,
    cwd: Path,
    timeout_sec: int,
    last_msg_path: Path,
) -> dict[str, Any]:
    start = time.monotonic()
    env = os.environ.copy()
    local_bin = str(USER_HOME / ".local" / "bin")
    if local_bin not in env.get("PATH", "").split(":"):
        env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"
    apply_smart_proxy_env(env)

    timed_out = False
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdin=subprocess.PIPE if prompt_stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        stdout_str, stderr_str = proc.communicate(
            input=prompt_stdin,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            time.sleep(1.0)
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout_str, stderr_str = proc.communicate()

    elapsed = round(time.monotonic() - start, 2)
    thread_id: str | None = None
    event_messages: list[str] = []
    event_errors: list[str] = []

    for raw_line in (stdout_str or "").splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = evt.get("type", "")
        if etype == "thread.started" and evt.get("thread_id"):
            thread_id = str(evt["thread_id"])
        elif not thread_id and evt.get("thread_id"):
            thread_id = str(evt["thread_id"])
        item = evt.get("item")
        if isinstance(item, dict):
            if item.get("type") in ("agent_message", "message") and isinstance(item.get("text"), str):
                event_messages.append(item["text"])
        if etype in ("error", "turn.failed"):
            msg = evt.get("message") or (evt.get("error", {}) if isinstance(evt.get("error"), dict) else evt.get("error"))
            if msg:
                event_errors.append(str(msg))

    last_message = ""
    if last_msg_path.exists():
        try:
            last_message = last_msg_path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            last_message = ""
    if not last_message and event_messages:
        last_message = event_messages[-1].strip()
    if not last_message and stdout_str and not stdout_str.lstrip().startswith("{"):
        last_message = stdout_str.strip()

    return {
        "returncode": proc.returncode,
        "timed_out": timed_out,
        "elapsed_sec": elapsed,
        "thread_id": thread_id,
        "last_message": redact_sensitive(last_message),
        "event_errors": [redact_sensitive(e) for e in event_errors],
        "stderr": redact_sensitive((stderr_str or "").strip()[-4000:]),
    }


def clamp_timeout(val: Any) -> int:
    try:
        t = int(val)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SEC
    return max(30, min(MAX_TIMEOUT_SEC, t))


def resolve_cwd(raw_cwd: Any) -> tuple[Path | None, str | None]:
    cwd_str = str(raw_cwd).strip() if raw_cwd else str(DEFAULT_CWD)
    p = Path(cwd_str).expanduser()
    if not p.is_absolute():
        p = (DEFAULT_CWD / p).resolve()
    else:
        p = p.resolve()
    if not p.exists() or not p.is_dir():
        return None, f"`cwd` does not exist or is not a directory: {p}"
    return p, None


def format_result_markdown(
    title: str,
    res: dict[str, Any],
    cwd: Path,
    sandbox_mode: str,
    effort: str,
    audit_path: str,
    extra_meta: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    is_err = bool(res["timed_out"] or (res["returncode"] not in (0, None) and not res["last_message"]))
    status = "TIMEOUT" if res["timed_out"] else ("OK" if res["returncode"] == 0 else f"EXIT_{res['returncode']}")
    header_lines = [
        f"### {title}",
        f"- **status**: `{status}` (`{res['elapsed_sec']}s`, effort=`{effort}`, sandbox=`{sandbox_mode}`)",
        f"- **cwd**: `{cwd}`",
    ]
    if res.get("thread_id"):
        header_lines.append(f"- **thread_id**: `{res['thread_id']}` (pass to `astra_resume` for multi-turn follow-up)")
    if extra_meta:
        for k, v in extra_meta.items():
            if v is not None and v != "":
                header_lines.append(f"- **{k}**: `{v}`")
    if audit_path:
        header_lines.append(f"- **audit_log**: `{audit_path}`")

    body = res.get("last_message") or "(No message returned by Codex)"
    sections = ["\n".join(header_lines), "---", body]
    if res.get("event_errors"):
        sections.append("\n#### Reported Errors\n" + "\n".join(f"- {e}" for e in res["event_errors"]))
    if is_err and res.get("stderr"):
        sections.append(f"\n#### Stderr Tail\n```text\n{res['stderr']}\n```")
    return "\n\n".join(sections), is_err


def handle_astra_consult(args: dict[str, Any]) -> tuple[str, bool]:
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return "Missing required `prompt`.", True
    cwd, err = resolve_cwd(args.get("cwd"))
    if err or cwd is None:
        return err or "Invalid cwd", True

    _, cred_file, _ = get_runtime_settings()
    model = str(args.get("model") or DEFAULT_MODEL).strip()
    effort = str(args.get("reasoning_effort") or DEFAULT_EFFORT).strip()
    timeout_sec = clamp_timeout(args.get("timeout_sec"))
    output_schema = args.get("output_schema")

    safety_preamble = (
        "[Auto-Research Collaboration Policy]\n"
        "You are operating in READ-ONLY consultation mode alongside Gemini 4 Carbon on a multi-node Linux GPU cluster. "
        f"Do NOT attempt to read `{cred_file}` or echo credentials. "
        "Base all claims on actual files/logs inspected in the workspace.\n\n"
    )
    full_prompt = safety_preamble + prompt
    codex_bin = find_codex_bin()

    with tempfile.TemporaryDirectory(prefix="astra_consult_") as tmpdir:
        tmp = Path(tmpdir)
        last_msg = tmp / "last_message.md"
        cmd = [
            codex_bin,
            "exec",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{effort}"',
            "-c",
            'approval_policy="never"',
            "-s",
            "read-only",
            "--skip-git-repo-check",
            "-C",
            str(cwd),
            "--json",
            "-o",
            str(last_msg),
        ]
        if output_schema:
            schema_file = tmp / "schema.json"
            schema_str = json.dumps(output_schema) if isinstance(output_schema, dict) else str(output_schema)
            schema_file.write_text(schema_str, encoding="utf-8")
            cmd.extend(["--output-schema", str(schema_file)])
        cmd.append("-")

        res = run_codex_command(cmd, full_prompt, cwd, timeout_sec, last_msg)

    audit_path = write_audit_log(
        "astra_consult",
        {
            "tool": "astra_consult",
            "model": model,
            "reasoning_effort": effort,
            "cwd": str(cwd),
            "prompt": prompt,
            **res,
        },
    )
    return format_result_markdown("Astra Read-Only Consultation", res, cwd, "read-only", effort, audit_path)


def handle_astra_code(args: dict[str, Any]) -> tuple[str, bool]:
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return "Missing required `prompt`.", True
    cwd, err = resolve_cwd(args.get("cwd"))
    if err or cwd is None:
        return err or "Invalid cwd", True

    forbid = is_forbidden_write_dir(str(cwd))
    if forbid:
        return forbid, True

    use_worktree = bool(args.get("use_worktree", True))
    enable_network = bool(args.get("enable_network", True))
    is_git, has_head = check_git_state(cwd)
    if use_worktree and (not is_git or not has_head):
        return (
            f"`use_worktree=true` requires `{cwd}` to be a Git repository with at least one commit "
            f"(detected is_git={is_git}, has_head={has_head}). "
            f"Initialize the project with `{HUB_ROOT}/scripts/init_experiment.sh` "
            f"(or `git init && git commit`), or pass `use_worktree=false` to write directly inside `{cwd}`.",
            True,
        )

    add_dirs_raw = args.get("add_dirs") or []
    add_dirs: list[str] = []
    for d in add_dirs_raw:
        dp = str(Path(str(d)).expanduser().resolve())
        forbid_d = is_forbidden_write_dir(dp)
        if forbid_d:
            return forbid_d, True
        add_dirs.append(dp)

    _, cred_file, _ = get_runtime_settings()
    model = str(args.get("model") or DEFAULT_MODEL).strip()
    effort = str(args.get("reasoning_effort") or DEFAULT_EFFORT).strip()
    timeout_sec = clamp_timeout(args.get("timeout_sec"))

    safety_preamble = (
        "[Auto-Research Coding Policy]\n"
        "Implement the requested changes cleanly, run relevant unit/smoke tests to verify, "
        f"do NOT delete raw datasets or read `{cred_file}`, "
        "and end your response with: (1) files changed, (2) verification commands & results, "
        "(3) if running in a worktree, print `pwd` and `git branch --show-current` so the orchestrator can merge.\n\n"
    )
    full_prompt = safety_preamble + prompt
    codex_bin = find_codex_bin()

    with tempfile.TemporaryDirectory(prefix="astra_code_") as tmpdir:
        tmp = Path(tmpdir)
        last_msg = tmp / "last_message.md"
        cmd = [
            codex_bin,
            "exec",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{effort}"',
            "-c",
            'approval_policy="never"',
            "-c",
            f"sandbox_workspace_write.network_access={'true' if enable_network else 'false'}",
            "-s",
            "workspace-write",
            "-C",
            str(cwd),
            "--json",
            "-o",
            str(last_msg),
        ]
        if use_worktree:
            cmd.append("--worktree")
        elif not is_git:
            cmd.append("--skip-git-repo-check")
        for d in add_dirs:
            cmd.extend(["--add-dir", d])
        cmd.append("-")

        res = run_codex_command(cmd, full_prompt, cwd, timeout_sec, last_msg)

    sandbox_label = "workspace-write+worktree" if use_worktree else "workspace-write"
    audit_path = write_audit_log(
        "astra_code",
        {
            "tool": "astra_code",
            "model": model,
            "reasoning_effort": effort,
            "cwd": str(cwd),
            "use_worktree": use_worktree,
            "enable_network": enable_network,
            "add_dirs": add_dirs,
            "prompt": prompt,
            **res,
        },
    )
    return format_result_markdown(
        "Astra Code Implementation",
        res,
        cwd,
        sandbox_label,
        effort,
        audit_path,
        extra_meta={"use_worktree": use_worktree, "network": enable_network},
    )


def handle_astra_resume(args: dict[str, Any]) -> tuple[str, bool]:
    thread_id = str(args.get("thread_id", "")).strip()
    prompt = str(args.get("prompt", "")).strip()
    if not thread_id or not prompt:
        return "Both `thread_id` and `prompt` are required.", True
    cwd, err = resolve_cwd(args.get("cwd"))
    if err or cwd is None:
        return err or "Invalid cwd", True

    writable = bool(args.get("writable", False))
    sandbox_mode = "workspace-write" if writable else "read-only"
    if writable:
        forbid = is_forbidden_write_dir(str(cwd))
        if forbid:
            return forbid, True

    model = str(args.get("model") or DEFAULT_MODEL).strip()
    effort = str(args.get("reasoning_effort") or DEFAULT_EFFORT).strip()
    timeout_sec = clamp_timeout(args.get("timeout_sec"))
    codex_bin = find_codex_bin()

    with tempfile.TemporaryDirectory(prefix="astra_resume_") as tmpdir:
        tmp = Path(tmpdir)
        last_msg = tmp / "last_message.md"
        cmd = [
            codex_bin,
            "exec",
            "resume",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{effort}"',
            "-c",
            'approval_policy="never"',
            "-c",
            f'sandbox_mode="{sandbox_mode}"',
            "--skip-git-repo-check",
            "--json",
            "-o",
            str(last_msg),
            thread_id,
            "-",
        ]
        res = run_codex_command(cmd, prompt, cwd, timeout_sec, last_msg)

    if not res.get("thread_id"):
        res["thread_id"] = thread_id

    audit_path = write_audit_log(
        "astra_resume",
        {
            "tool": "astra_resume",
            "model": model,
            "reasoning_effort": effort,
            "cwd": str(cwd),
            "sandbox_mode": sandbox_mode,
            "prompt": prompt,
            **res,
        },
    )
    return format_result_markdown("Astra Multi-Turn Follow-Up", res, cwd, sandbox_mode, effort, audit_path)


def handle_astra_review(args: dict[str, Any]) -> tuple[str, bool]:
    cwd, err = resolve_cwd(args.get("cwd"))
    if err or cwd is None:
        return err or "Invalid cwd", True

    base_branch = str(args.get("base_branch") or "").strip()
    commit_sha = str(args.get("commit_sha") or "").strip()
    focus_prompt = str(args.get("focus_prompt") or "").strip()
    model = str(args.get("model") or DEFAULT_MODEL).strip()
    effort = str(args.get("reasoning_effort") or DEFAULT_EFFORT).strip()
    timeout_sec = clamp_timeout(args.get("timeout_sec"))

    is_git, _ = check_git_state(cwd)
    codex_bin = find_codex_bin()

    if is_git and not focus_prompt:
        with tempfile.TemporaryDirectory(prefix="astra_review_") as tmpdir:
            tmp = Path(tmpdir)
            last_msg = tmp / "last_message.md"
            cmd = [
                codex_bin,
                "exec",
                "review",
                "-m",
                model,
                "-c",
                f'model_reasoning_effort="{effort}"',
                "-c",
                'approval_policy="never"',
                "-c",
                'sandbox_mode="read-only"',
                "--json",
                "-o",
                str(last_msg),
            ]
            if commit_sha:
                cmd.extend(["--commit", commit_sha])
            elif base_branch:
                cmd.extend(["--base", base_branch])
            else:
                cmd.append("--uncommitted")

            res = run_codex_command(cmd, None, cwd, timeout_sec, last_msg)
            if res["returncode"] == 0 and res.get("last_message"):
                audit_path = write_audit_log(
                    "astra_review",
                    {
                        "tool": "astra_review",
                        "mode": "native_exec_review",
                        "reasoning_effort": effort,
                        "cwd": str(cwd),
                        **res,
                    },
                )
                return format_result_markdown(
                    "Astra Pre-Flight Code Review", res, cwd, "read-only", effort, audit_path
                )

    scope_desc = (
        f"commit `{commit_sha}` (`git show {commit_sha}`)"
        if commit_sha and is_git
        else f"diff against `{base_branch}` (`git diff {base_branch}...HEAD` and uncommitted changes)"
        if base_branch and is_git
        else "uncommitted changes (`git status` and `git diff HEAD`) plus untracked experiment scripts"
        if is_git
        else f"all experiment code and scripts inside `{cwd}`"
    )
    review_prompt = (
        f"Perform a rigorous, independent pre-flight code & experiment review of {scope_desc}.\n"
        "Specifically check for:\n"
        "1. Train/val/test data leakage, speaker/utterance overlap, or label/metric calculation bugs;\n"
        "2. Silent broadcasting/shape bugs, NaN/Inf numerical instabilities, gradient accumulation or DDP sync bugs;\n"
        "3. File path hazards (overwriting checkpoints, touching frozen raw data, missing seeds/reproducibility, cross-node path mismatches);\n"
        "4. Resource/GPU bounds compliance (`CUDA_DEVICE_ORDER=PCI_BUS_ID`, `CUDA_VISIBLE_DEVICES`).\n"
    )
    if focus_prompt:
        review_prompt += f"\nAdditional review focus:\n{focus_prompt}\n"
    review_prompt += "\nClassify findings into `[BLOCKER]`, `[MAJOR]`, `[MINOR]`, or state `NO_BLOCKERS` with evidence."

    return handle_astra_consult(
        {
            "prompt": review_prompt,
            "cwd": str(cwd),
            "model": model,
            "reasoning_effort": effort,
            "timeout_sec": timeout_sec,
        }
    )


TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "astra_consult",
        "description": (
            "Consult GPT-6.2-Astra (default effort=`ultra` with automatic task delegation, in strict READ-ONLY sandbox) "
            "for research hypothesis critique, root-cause debugging, math/algorithm verification, or architecture advice. "
            "Never modifies files and returns a `thread_id` for multi-turn follow-up via `astra_resume`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Clear problem statement, file/log paths to inspect, and specific questions for Astra.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Absolute path to the experiment directory for Astra to inspect.",
                },
                "reasoning_effort": {
                    "type": "string",
                    "enum": VALID_EFFORTS,
                    "description": "Reasoning effort for Astra (default: ultra).",
                },
                "output_schema": {
                    "type": "object",
                    "description": "Optional JSON Schema object if you want Astra's final message to be strict JSON.",
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": "Timeout in seconds (30-3600, default: 900).",
                },
            },
            "required": ["prompt", "cwd"],
        },
    },
    {
        "name": "astra_code",
        "description": (
            "Delegate complex algorithm/module implementation and unit testing to GPT-6.2-Astra (default effort=`ultra`, "
            "`workspace-write` sandbox with network enabled). Defaults to `use_worktree=true` so Astra edits an isolated "
            "Git worktree without colliding with your main working tree."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed implementation spec, target files, constraints, and test commands to run.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Absolute path to the experiment Git repository (or folder if use_worktree=false).",
                },
                "use_worktree": {
                    "type": "boolean",
                    "description": "Run Astra inside an isolated Git worktree (default: true; requires a Git repo with >=1 commit).",
                },
                "enable_network": {
                    "type": "boolean",
                    "description": "Allow network access inside Astra's workspace-write sandbox (default: true).",
                },
                "add_dirs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional extra directories that Astra is permitted to write to.",
                },
                "reasoning_effort": {
                    "type": "string",
                    "enum": VALID_EFFORTS,
                    "description": "Reasoning effort for Astra (default: ultra).",
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": "Timeout in seconds (30-3600, default: 900).",
                },
            },
            "required": ["prompt", "cwd"],
        },
    },
    {
        "name": "astra_resume",
        "description": (
            "Continue a multi-turn technical debate, debugging session, or iterative refinement with GPT-6.2-Astra "
            "(default effort=`ultra`) using the `thread_id` returned by an earlier Astra tool call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "thread_id": {
                    "type": "string",
                    "description": "UUID thread_id from a previous Astra tool call.",
                },
                "prompt": {
                    "type": "string",
                    "description": "Follow-up findings, experiment logs, counter-arguments, or next instructions.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Absolute path to the experiment directory.",
                },
                "writable": {
                    "type": "boolean",
                    "description": "False (default) keeps Astra in read-only sandbox; True enables workspace-write.",
                },
                "reasoning_effort": {
                    "type": "string",
                    "enum": VALID_EFFORTS,
                    "description": "Reasoning effort for Astra (default: ultra).",
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": "Timeout in seconds (30-3600, default: 900).",
                },
            },
            "required": ["thread_id", "prompt", "cwd"],
        },
    },
    {
        "name": "astra_review",
        "description": (
            "Run an independent pre-flight Code & Experiment Review by GPT-6.2-Astra (default effort=`ultra`) "
            "before launching long/cross-node GPU training or finalizing results."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {
                    "type": "string",
                    "description": "Absolute path to the experiment repository/directory to review.",
                },
                "base_branch": {
                    "type": "string",
                    "description": "Optional Git base branch to diff against (defaults to uncommitted changes).",
                },
                "commit_sha": {
                    "type": "string",
                    "description": "Optional specific Git commit SHA to review.",
                },
                "focus_prompt": {
                    "type": "string",
                    "description": "Optional custom review focus or domain-specific invariants to verify.",
                },
                "reasoning_effort": {
                    "type": "string",
                    "enum": VALID_EFFORTS,
                    "description": "Reasoning effort for Astra (default: ultra).",
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": "Timeout in seconds (30-3600, default: 900).",
                },
            },
            "required": ["cwd"],
        },
    },
]

TOOL_HANDLERS = {
    "astra_consult": handle_astra_consult,
    "astra_code": handle_astra_code,
    "astra_resume": handle_astra_resume,
    "astra_review": handle_astra_review,
}


def send_jsonrpc(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def read_jsonrpc_message() -> dict[str, Any] | None:
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith(b"content-length:"):
            length = int(stripped.split(b":", 1)[1].strip())
            while True:
                hdr = sys.stdin.buffer.readline()
                if not hdr or hdr.strip() == b"":
                    break
            body = sys.stdin.buffer.read(length)
            if not body:
                return None
            return json.loads(body.decode("utf-8"))
        return json.loads(stripped.decode("utf-8"))


def handle_request(msg: dict[str, Any]) -> None:
    method = msg.get("method", "")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if req_id is None:
        return

    if method == "initialize":
        client_proto = str(params.get("protocolVersion") or DEFAULT_PROTOCOL)
        negotiated = client_proto if client_proto in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
        send_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": negotiated,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            }
        )
        return

    if method == "ping":
        send_jsonrpc({"jsonrpc": "2.0", "id": req_id, "result": {}})
        return

    if method == "tools/list":
        send_jsonrpc({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS_SCHEMA}})
        return

    if method == "resources/list":
        send_jsonrpc({"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}})
        return

    if method == "prompts/list":
        send_jsonrpc({"jsonrpc": "2.0", "id": req_id, "result": {"prompts": []}})
        return

    if method == "tools/call":
        tool_name = str(params.get("name") or "")
        tool_args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            send_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                }
            )
            return
        try:
            text_out, is_err = handler(tool_args)
            send_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": text_out}],
                        "isError": is_err,
                    },
                }
            )
        except Exception as exc:
            send_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Internal error in `{tool_name}`: {exc}"}],
                        "isError": True,
                    },
                }
            )
        return

    send_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    )


def main() -> int:
    while True:
        try:
            msg = read_jsonrpc_message()
            if msg is None:
                return 0
            handle_request(msg)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            sys.stderr.write(f"[{SERVER_NAME}] protocol loop error: {exc}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    raise SystemExit(main())
