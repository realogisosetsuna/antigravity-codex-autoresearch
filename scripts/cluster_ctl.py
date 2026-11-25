#!/usr/bin/env python3
"""Zero-echo multi-node GPU, disk, background job, and data-transfer controller for Auto Research.

Reads node topology and storage/reserve policies from `config/cluster.local.json` (gitignored)
or `~/.config/auto-research/cluster.json`, falling back automatically to single-node local discovery
when cloned on a standalone GPU host.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HUB_ROOT = Path(os.environ.get("AUTO_RESEARCH_HOME") or Path(__file__).resolve().parents[1]).resolve()
sys.path.insert(0, str(HUB_ROOT / "scripts"))
from ar_config import USER_HOME, load_cluster_config  # noqa: E402

CLUSTER_CFG, _ = load_cluster_config()
NODES: dict[str, dict[str, Any]] = CLUSTER_CFG.get("nodes") or {}
EXISTING_ASKPASS = Path(str(CLUSTER_CFG.get("ssh_askpass_helper") or "")) if CLUSTER_CFG.get("ssh_askpass_helper") else None
AGENT_CRED_FILE = Path(str(CLUSTER_CFG.get("credential_file") or (USER_HOME / "agent.md")))
PROD_ROOT = Path(str(CLUSTER_CFG.get("production_root") or "")) if CLUSTER_CFG.get("production_root") else None
FROZEN_RAW_PATHS = tuple(str(p) for p in (CLUSTER_CFG.get("frozen_readonly_paths") or []) if p)

RESERVE_CFG = CLUSTER_CFG.get("gpu_auto_reserve") or {}
RESERVE_HOST = str(RESERVE_CFG.get("host") or "").strip()
RESERVE_STATUS = str(RESERVE_CFG.get("status_file") or "").strip()
RESERVE_CTL = str(RESERVE_CFG.get("control_script") or "control.sh").strip()


def build_askpass_code(cred_path: Path) -> str:
    return f"""#!/usr/bin/env python3
import re, sys
from pathlib import Path
p = Path({json.dumps(str(cred_path))})
if not p.is_file():
    sys.exit(1)
text = p.read_text(encoding="utf-8", errors="replace")
for pat in (
    r"(?:密码|password|passwd|pwd)[^\\n:`]*[:：]\\s*`([^`\\n]+)`",
    r"(?:密码|password|passwd|pwd)[^\\n:]*[:：]\\s*(\\S+)",
    r"`([^`\\s]{{6,}})`",
):
    m = re.search(pat, text, flags=re.IGNORECASE)
    if m:
        sys.stdout.write(m.group(1).strip() + "\\n")
        sys.exit(0)
for ln in text.splitlines():
    s = ln.strip()
    if s and not s.startswith("#") and not s.startswith("-"):
        sys.stdout.write(s + "\\n")
        sys.exit(0)
sys.exit(1)
"""


def local_node_name() -> str:
    h = socket.gethostname().split(".")[0].strip()
    if h in NODES:
        return h
    try:
        ips = set(subprocess.check_output(["hostname", "-I"], text=True, timeout=3).split())
        for name, meta in NODES.items():
            if meta.get("ip") in ips:
                return name
    except Exception:
        pass
    return h


@contextlib.contextmanager
def askpass_env():
    """Yield an environment dict configured with a zero-echo SSH_ASKPASS helper."""
    env = os.environ.copy()
    if EXISTING_ASKPASS and EXISTING_ASKPASS.is_file() and os.access(EXISTING_ASKPASS, os.X_OK):
        env.update(
            SSH_ASKPASS=str(EXISTING_ASKPASS),
            SSH_ASKPASS_REQUIRE="force",
            DISPLAY=env.get("DISPLAY") or "auto-research:0",
        )
        yield env
        return

    with tempfile.TemporaryDirectory(prefix="ar_askpass_") as tmpdir:
        helper = Path(tmpdir) / "askpass.py"
        helper.write_text(build_askpass_code(AGENT_CRED_FILE), encoding="utf-8")
        os.chmod(helper, 0o700)
        env.update(
            SSH_ASKPASS=str(helper),
            SSH_ASKPASS_REQUIRE="force",
            DISPLAY=env.get("DISPLAY") or "auto-research:0",
        )
        yield env


def run_on_node(node: str, remote_bash: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    if node not in NODES:
        raise ValueError(f"Unknown node `{node}` (configured nodes: {', '.join(NODES)})")

    here = local_node_name()
    if here == node or NODES[node].get("ip") in ("127.0.0.1", "localhost"):
        return subprocess.run(
            ["bash", "-lc", remote_bash],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )

    target = f"{NODES[node]['user']}@{NODES[node]['ip']}"
    base_ssh = [
        "ssh",
        "-T",
        "-n",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]

    batch_cmd = base_ssh + ["-o", "BatchMode=yes", target, f"bash -lc {shlex.quote(remote_bash)}"]
    r1 = subprocess.run(
        batch_cmd,
        capture_output=True,
        text=True,
        timeout=min(timeout, 12),
        stdin=subprocess.DEVNULL,
    )
    if r1.returncode == 0:
        return r1

    if "Permission denied" not in (r1.stderr or "") and r1.returncode != 255:
        return r1

    with askpass_env() as env:
        ask_cmd = base_ssh + [
            "-o",
            "NumberOfPasswordPrompts=1",
            target,
            f"bash -lc {shlex.quote(remote_bash)}",
        ]
        return subprocess.run(
            ask_cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )


REMOTE_PROBE_PY = r"""
import csv, json, os, shutil, socket, subprocess
from pathlib import Path

disks = __DISKS__
reserve_status_str = __RESERVE_STATUS__
out = {
    "hostname": socket.gethostname().split(".")[0],
    "gpus": [],
    "disks": [],
    "auto_reserve": None,
}

if reserve_status_str:
    rp = Path(reserve_status_str)
    if rp.is_file():
        try:
            out["auto_reserve"] = json.loads(rp.read_text(encoding="utf-8"))
        except Exception as e:
            out["auto_reserve_error"] = str(e)

if shutil.which("nvidia-smi"):
    try:
        raw_g = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,uuid,name,memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"],
            text=True, timeout=8
        )
        by_uuid = {}
        for row in csv.reader(raw_g.splitlines(), skipinitialspace=True):
            if len(row) < 6:
                continue
            item = {
                "index": int(row[0]),
                "uuid": row[1],
                "name": row[2],
                "used_mib": int(row[3]),
                "total_mib": int(row[4]),
                "util_pct": int(row[5]),
                "pids": [],
            }
            by_uuid[item["uuid"]] = item
            out["gpus"].append(item)
        raw_p = subprocess.check_output(
            ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,used_gpu_memory", "--format=csv,noheader,nounits"],
            text=True, timeout=8
        )
        for row in csv.reader(raw_p.splitlines(), skipinitialspace=True):
            if len(row) >= 2 and row[0] in by_uuid:
                by_uuid[row[0]]["pids"].append(int(row[1]))
        for g in out["gpus"]:
            g["idle"] = (g["used_mib"] <= 1024 and g["util_pct"] <= 10 and len(g["pids"]) == 0)
    except Exception as e:
        out["gpu_error"] = str(e)

for d in disks:
    if not os.path.exists(d):
        continue
    try:
        df_b = subprocess.check_output(["df", "-BG", "--output=size,used,avail,pcent,target", d], text=True, timeout=5).splitlines()[-1].split()
        df_i = subprocess.check_output(["df", "--output=ipcent", d], text=True, timeout=5).splitlines()[-1].strip()
        out["disks"].append({
            "path": d,
            "size_gb": int(df_b[0].rstrip("G")),
            "used_gb": int(df_b[1].rstrip("G")),
            "avail_gb": int(df_b[2].rstrip("G")),
            "use_pct": df_b[3],
            "inode_pct": df_i,
        })
    except Exception:
        pass

print(json.dumps(out))
"""


def load_production_reservations() -> dict[str, set[int]]:
    reserved: dict[str, set[int]] = {n: set() for n in NODES}
    if not PROD_ROOT:
        return reserved
    dep_file = PROD_ROOT / "deployment.json"
    if not dep_file.is_file():
        return reserved
    try:
        dep = json.loads(dep_file.read_text(encoding="utf-8"))
        owners = dep.get("region_owners") or {}
        for region, owner in owners.items():
            for fname in (f"{region}-service.json", f"{region}-auxiliary-device.json"):
                svc_file = PROD_ROOT / "runtime" / fname
                if svc_file.is_file() and owner in reserved:
                    svc = json.loads(svc_file.read_text(encoding="utf-8"))
                    if svc.get("state") in ("running", "active", "starting", None):
                        for g in svc.get("gpus") or []:
                            reserved[owner].add(int(g))
                        if isinstance(svc.get("gpu"), int):
                            reserved[owner].add(int(svc["gpu"]))
    except Exception:
        pass
    return reserved


def probe_all_nodes(node_list: list[str]) -> dict[str, Any]:
    reserved = load_production_reservations()
    results: dict[str, Any] = {}

    def _one(n: str) -> dict[str, Any]:
        r_stat = RESERVE_STATUS if (RESERVE_HOST and n == RESERVE_HOST) else ""
        script = REMOTE_PROBE_PY.replace("__DISKS__", json.dumps(NODES[n]["disks"])).replace(
            "__RESERVE_STATUS__", json.dumps(r_stat)
        )
        cmd = f"python3 -c {shlex.quote(script)}"
        try:
            r = run_on_node(n, cmd, timeout=20)
            if r.returncode != 0:
                return {"node": n, "ok": False, "error": (r.stderr or r.stdout).strip()[-300:]}
            data = json.loads(r.stdout.strip().splitlines()[-1])
            prod_set = reserved.get(n, set())
            ar = data.get("auto_reserve") if (RESERVE_HOST and n == RESERVE_HOST) else None
            reserved_by_us = {int(x) for x in (ar.get("reserved_by_us") or [])} if isinstance(ar, dict) else set()
            excluded_set = {int(x) for x in (ar.get("excluded_gpus") or [])} if isinstance(ar, dict) else set()

            for g in data.get("gpus", []):
                idx = g["index"]
                g["prod_reserved"] = idx in prod_set
                g["reserved_by_us"] = idx in reserved_by_us
                g["excluded_in_use"] = idx in excluded_set
                if idx in prod_set or idx in excluded_set:
                    g["available_for_research"] = False
                elif idx in reserved_by_us:
                    g["available_for_research"] = True
                else:
                    g["available_for_research"] = bool(g["idle"])
            data["node"] = n
            data["ip"] = NODES[n]["ip"]
            data["home"] = NODES[n]["home"]
            data["ok"] = True
            return data
        except Exception as exc:
            return {"node": n, "ok": False, "error": str(exc)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(node_list))) as pool:
        futs = {n: pool.submit(_one, n) for n in node_list}
        for n in node_list:
            results[n] = futs[n].result()
    return results


def cmd_status(args: argparse.Namespace) -> int:
    node_list = [x.strip() for x in args.nodes.split(",") if x.strip() in NODES]
    data = probe_all_nodes(node_list)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    print(f"Local host: {local_node_name()}")
    for n in node_list:
        info = data[n]
        if not info.get("ok"):
            print(f"\n[{n} ({NODES[n]['ip']})] UNREACHABLE: {info.get('error')}")
            continue
        free_gpus = [
            f"{g['index']}(standby)" if g.get("reserved_by_us") else str(g["index"])
            for g in info.get("gpus", [])
            if g.get("available_for_research")
        ]
        print(
            f"\n[{n} ({info['ip']}) | home={info['home']}] "
            f"Claimable Research GPUs: [{','.join(free_gpus) if free_gpus else 'NONE'}]"
        )
        for g in info.get("gpus", []):
            if g.get("reserved_by_us"):
                tag = f"RESERVED_BY_US (claim via: {RESERVE_CTL} release --gpus {g['index']})"
            elif g.get("prod_reserved") or g.get("excluded_in_use"):
                tag = "EXCLUDED_IN_USE (production/active session — do NOT touch)"
            elif g.get("idle"):
                tag = "IDLE"
            else:
                tag = f"BUSY_EXTERNAL(pids={g.get('pids')})"
            print(
                f"  GPU {g['index']}: {g['name']} | {g['used_mib']}/{g['total_mib']} MiB | "
                f"util={g['util_pct']}% | {tag}"
            )
        for d in info.get("disks", []):
            print(
                f"  DISK {d['path']:<12}: avail={d['avail_gb']}G / {d['size_gb']}G "
                f"(use={d['use_pct']}, inode={d['inode_pct']})"
            )
    return 0


def cmd_recommend_gpus(args: argparse.Namespace) -> int:
    prefer = [x.strip() for x in args.prefer.split(",") if x.strip() in NODES]
    if not prefer:
        prefer = list(NODES)
    data = probe_all_nodes(prefer)
    best: dict[str, Any] | None = None

    for n in prefer:
        info = data.get(n) or {}
        if not info.get("ok"):
            continue
        avail_objs = [g for g in info.get("gpus", []) if g.get("available_for_research")]
        data_disks = [
            d for d in info.get("disks", [])
            if d["path"].startswith("/mnt/data") and d["avail_gb"] >= args.min_free_gb
        ]
        if len(avail_objs) >= args.count and (data_disks or args.min_free_gb <= 0):
            chosen = avail_objs[: args.count]
            gpu_ids = [g["index"] for g in chosen]
            csv_ids = ",".join(str(i) for i in gpu_ids)
            user_name = NODES[n].get("user") or USER_HOME.name
            top_disk = f"{max(data_disks, key=lambda x: x['avail_gb'])['path']}/{user_name}" if data_disks else info["home"]
            standby_ids = [str(g["index"]) for g in chosen if g.get("reserved_by_us")]
            best = {
                "node": n,
                "ip": info["ip"],
                "gpus": gpu_ids,
                "cuda_visible_devices": csv_ids,
                "recommended_data_dir": top_disk,
                "home": info["home"],
                "requires_standby_release": bool(standby_ids),
                "claim_cmd": f"{RESERVE_CTL} release --gpus {','.join(standby_ids)}" if standby_ids else None,
                "return_cmd": f"{RESERVE_CTL} watch --gpus {','.join(standby_ids)}" if standby_ids else None,
            }
            break

    if not best:
        print(json.dumps({"ok": False, "reason": "No single node satisfied GPU count and free disk criteria."}, indent=2))
        return 1
    print(json.dumps({"ok": True, **best}, ensure_ascii=False, indent=2))
    return 0


def check_unreleased_standby_gpus(node: str, gpus_str: str) -> str | None:
    if not RESERVE_HOST or node != RESERVE_HOST or not RESERVE_STATUS:
        return None
    req_ids = {int(x.strip()) for x in gpus_str.split(",") if x.strip().isdigit()}
    if not req_ids:
        return None
    r = run_on_node(node, f"cat {shlex.quote(RESERVE_STATUS)} 2>/dev/null || true", timeout=8)
    if r.returncode != 0 or not (r.stdout or "").strip():
        return None
    try:
        st = json.loads(r.stdout)
        held = sorted(req_ids.intersection({int(x) for x in (st.get("reserved_by_us") or [])}))
        if held:
            csv_held = ",".join(str(i) for i in held)
            return (
                f"GPU(s) {csv_held} on `{node}` are currently held by standby daemon (`reserved_by_us`). "
                f"Atomically claim and exempt them first with:\n"
                f"  {RESERVE_CTL} release --gpus {csv_held}\n"
                f"and return them only after ALL experiment rounds finish with:\n"
                f"  {RESERVE_CTL} watch --gpus {csv_held}"
            )
    except Exception:
        pass
    return None


def cmd_exec(args: argparse.Namespace) -> int:
    if args.gpus:
        err = check_unreleased_standby_gpus(args.node, args.gpus)
        if err:
            print(err, file=sys.stderr)
            return 2

    parts: list[str] = []
    if args.cwd:
        parts.append(f"cd {shlex.quote(args.cwd)}")
    if args.gpus is not None and args.gpus != "":
        parts.append(f"export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES={shlex.quote(args.gpus)}")
    parts.append(args.cmd)
    full_cmd = " && ".join(parts)
    res = run_on_node(args.node, full_cmd, timeout=args.timeout)
    if res.stdout:
        sys.stdout.write(res.stdout)
    if res.stderr:
        sys.stderr.write(res.stderr)
    return res.returncode


def cmd_launch_bg(args: argparse.Namespace) -> int:
    if args.gpus:
        err = check_unreleased_standby_gpus(args.node, args.gpus)
        if err:
            print(json.dumps({"ok": False, "node": args.node, "gpus": args.gpus, "error": err}, ensure_ascii=False, indent=2))
            return 2

    launch_script = (
        f"mkdir -p {shlex.quote(str(Path(args.log).parent))} {shlex.quote(str(Path(args.pid_file).parent))} && "
        f"cd {shlex.quote(args.workdir)} && "
        f"export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES={shlex.quote(args.gpus)} && "
        f"nohup bash -lc {shlex.quote(args.cmd)} </dev/null >> {shlex.quote(args.log)} 2>&1 & "
        f"pid=$!; echo $pid > {shlex.quote(args.pid_file)}; sleep 1; "
        f"if kill -0 $pid 2>/dev/null; then echo \"ALIVE:$pid\"; else echo \"DIED:$pid\"; exit 1; fi"
    )
    res = run_on_node(args.node, launch_script, timeout=30)
    out = (res.stdout or "").strip()
    alive = "ALIVE:" in out and res.returncode == 0
    pid = out.split("ALIVE:")[-1].splitlines()[0].strip() if "ALIVE:" in out else None
    payload = {
        "ok": alive,
        "node": args.node,
        "gpus": args.gpus,
        "workdir": args.workdir,
        "pid": int(pid) if pid and pid.isdigit() else pid,
        "log": args.log,
        "pid_file": args.pid_file,
        "detail": out or (res.stderr or "").strip(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if alive else 1


def cmd_check_job(args: argparse.Namespace) -> int:
    log_snip = (
        f" && echo '--- LOG TAIL ---' && tail -n {int(args.tail)} {shlex.quote(args.log)} 2>/dev/null || true"
        if args.log
        else ""
    )
    check_script = (
        f"if kill -0 {int(args.pid)} 2>/dev/null; then echo 'STATE=RUNNING'; "
        f"ps -p {int(args.pid)} -o pid,etime,pcpu,pmem,cmd --no-headers; "
        f"else echo 'STATE=STOPPED'; fi"
        f"{log_snip}"
    )
    res = run_on_node(args.node, check_script, timeout=20)
    if res.stdout:
        sys.stdout.write(res.stdout)
    if res.stderr:
        sys.stderr.write(res.stderr)
    return res.returncode


def resolve_rsync_endpoint(spec: str) -> tuple[str | None, str]:
    if ":" in spec and not spec.startswith("/"):
        node, path = spec.split(":", 1)
        if node not in NODES:
            raise ValueError(f"Unknown node `{node}` in `{spec}`")
        return node, path
    return None, spec


def cmd_sync(args: argparse.Namespace) -> int:
    src_node, src_path = resolve_rsync_endpoint(args.src)
    dst_node, dst_path = resolve_rsync_endpoint(args.dst)

    for frozen in FROZEN_RAW_PATHS:
        if dst_path == frozen or dst_path.startswith(frozen.rstrip("/") + "/"):
            print(f"Refusing sync into frozen raw archive `{frozen}`.", file=sys.stderr)
            return 1
    if src_node and dst_node and src_node != local_node_name() and dst_node != local_node_name():
        print("At least one endpoint of `sync` should be the current node (or use shared NAS).", file=sys.stderr)
        return 1

    here = local_node_name()
    src_arg = src_path if (src_node is None or src_node == here) else f"{NODES[src_node]['user']}@{NODES[src_node]['ip']}:{src_path}"
    dst_arg = dst_path if (dst_node is None or dst_node == here) else f"{NODES[dst_node]['user']}@{NODES[dst_node]['ip']}:{dst_path}"

    ssh_transport = "ssh -T -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new"
    rsync_cmd = ["rsync", "-az", "--partial", "--info=stats2", "-e", ssh_transport]
    if args.dry_run:
        rsync_cmd.append("--dry-run")
    for ex in args.exclude or []:
        rsync_cmd.extend(["--exclude", ex])
    rsync_cmd.extend([src_arg, dst_arg])

    with askpass_env() as env:
        res = subprocess.run(rsync_cmd, text=True, env=env, stdin=subprocess.DEVNULL, timeout=args.timeout)
        return res.returncode


def build_parser() -> argparse.ArgumentParser:
    default_nodes = ",".join(NODES.keys())
    p = argparse.ArgumentParser(description="Zero-echo multi-node GPU, storage, and job controller for Auto Research")
    sub = p.add_subparsers(dest="subcmd", required=True)

    s_st = sub.add_parser("status", help="Inspect live GPUs, standby/excluded GPUs, and disk space across nodes")
    s_st.add_argument("--nodes", default=default_nodes)
    s_st.add_argument("--json", action="store_true")
    s_st.set_defaults(func=cmd_status)

    s_rec = sub.add_parser("recommend-gpus", help="Pick claimable/idle non-production GPUs with sufficient local disk space")
    s_rec.add_argument("--count", type=int, default=1)
    s_rec.add_argument("--min-free-gb", type=int, default=50)
    s_rec.add_argument("--prefer", default=default_nodes)
    s_rec.set_defaults(func=cmd_recommend_gpus)

    s_ex = sub.add_parser("exec", help="Execute a command synchronously on a target node")
    s_ex.add_argument("--node", required=True, choices=list(NODES))
    s_ex.add_argument("--cmd", required=True)
    s_ex.add_argument("--cwd", default="")
    s_ex.add_argument("--gpus", default=None, help="Optional physical GPU IDs, e.g. '7' or '2,3'")
    s_ex.add_argument("--timeout", type=int, default=600)
    s_ex.set_defaults(func=cmd_exec)

    s_bg = sub.add_parser("launch-bg", help="Launch a detached background GPU job on a target node")
    s_bg.add_argument("--node", required=True, choices=list(NODES))
    s_bg.add_argument("--gpus", required=True, help="Physical GPU indices (`CUDA_DEVICE_ORDER=PCI_BUS_ID`), e.g. '7'")
    s_bg.add_argument("--workdir", required=True)
    s_bg.add_argument("--log", required=True)
    s_bg.add_argument("--pid-file", required=True)
    s_bg.add_argument("--cmd", required=True)
    s_bg.set_defaults(func=cmd_launch_bg)

    s_ck = sub.add_parser("check-job", help="Check whether a background PID on a target node is running and tail its log")
    s_ck.add_argument("--node", required=True, choices=list(NODES))
    s_ck.add_argument("--pid", type=int, required=True)
    s_ck.add_argument("--log", default="")
    s_ck.add_argument("--tail", type=int, default=40)
    s_ck.set_defaults(func=cmd_check_job)

    s_sy = sub.add_parser("sync", help="Rsync code/artifacts between nodes (`nodeX:/path` <-> `/local/path`)")
    s_sy.add_argument("--src", required=True)
    s_sy.add_argument("--dst", required=True)
    s_sy.add_argument("--exclude", action="append", default=[])
    s_sy.add_argument("--dry-run", action="store_true")
    s_sy.add_argument("--timeout", type=int, default=1800)
    s_sy.set_defaults(func=cmd_sync)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
