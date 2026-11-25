#!/usr/bin/env python3
"""Turnkey 24/7 headless Clash (`mihomo`) setup & health-check utility.

Designed for stable, sticky single-region exit routing (default: Japan 1x IEPL) so Google
account region affinity and low traffic multipliers are preserved, switching nodes ONLY when
the active node fails or suffers severe latency degradation (>250ms hysteresis).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HUB_ROOT = Path(os.environ.get("AUTO_RESEARCH_HOME") or Path(__file__).resolve().parents[1]).resolve()
sys.path.insert(0, str(HUB_ROOT / "scripts"))
from ar_config import USER_HOME, load_cluster_config  # noqa: E402

CLUSTER_CFG, _ = load_cluster_config()
MIHOMO_CFG = CLUSTER_CFG.get("local_mihomo") or {}

CONF_DIR = USER_HOME / ".config" / "mihomo" / "ai-research"
CONF_FILE = CONF_DIR / "config.yaml"
PROVIDER_DIR = CONF_DIR / "providers"
SYSTEMD_USER_DIR = USER_HOME / ".config" / "systemd" / "user"
SERVICE_NAME = str(MIHOMO_CFG.get("service_name") or f"{USER_HOME.name}-ai-mihomo.service")
SERVICE_FILE = SYSTEMD_USER_DIR / SERVICE_NAME

PROXY_PORT = int(MIHOMO_CFG.get("proxy_port") or 27890)
CTRL_PORT = int(MIHOMO_CFG.get("ctrl_port") or 29090)

REGION_PATTERNS: dict[str, str] = {
    "jp": r"(?i)(日本|JP|Japan|东京|大阪|Tokyo|Osaka)",
    "sg": r"(?i)(新加坡|狮城|SG|Singapore)",
    "us": r"(?i)(美国|US|United States|America)",
    "tw": r"(?i)(台湾|台北|TW|Taiwan)",
}

LOW_MULT_EXCLUDE = (
    r"(?i)(香港|HK|俄罗斯|RU|下载专用|免费|剩余|到期|官网|套餐|重置|群|"
    r"x1[.][1-9]|x[2-9]|x[1-9][0-9]|[2-9]x|[1-9][0-9]x)"
)

# Probe an actual AI API TLS endpoint instead of `gstatic.com/generate_204`: some proxy nodes pass
# `gstatic.com` with low ping while resetting TLS to OpenAI / Google CloudCode APIs.
HEALTHCHECK_URL = "https://api.openai.com/v1/models"


def find_mihomo_bin() -> Path | None:
    candidates = [
        os.environ.get("MIHOMO_BIN", ""),
        str(USER_HOME / ".local" / "bin" / "mihomo"),
        shutil.which("mihomo") or "",
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return Path(c)
    return None


def is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def yaml_quote(s: str) -> str:
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def read_existing_secret_and_url() -> tuple[str | None, str | None]:
    if not CONF_FILE.is_file():
        return None, None
    txt = CONF_FILE.read_text(encoding="utf-8", errors="replace")
    m_sec = re.search(r'^secret:\s*"([^"]+)"', txt, re.M)
    m_prov = re.search(r'proxy-providers:.*?^\s+url:\s*"(https?://[^"]+)"', txt, re.M | re.S)
    if m_prov:
        sub_url = m_prov.group(1)
    else:
        urls = [
            u for u in re.findall(r'^\s*url:\s*"(https?://[^"]+)"', txt, re.M)
            if not any(d in u for d in ("gstatic.com", "chatgpt.com", "openai.com", "antigravity.google", "googleapis.com"))
        ]
        sub_url = urls[0] if urls else None
    return (m_sec.group(1) if m_sec else None), sub_url


def build_sticky_groups_and_rules(
    provider_name: str,
    region: str,
    tolerance_ms: int,
    interval_sec: int,
    cross_region_fallback: bool = False,
) -> str:
    reg_pat = REGION_PATTERNS[region]
    region_upper = region.upper()
    if not cross_region_fallback:
        groups_yaml = f"""proxy-groups:
  # Pure single-region sticky pool ({region_upper} <=1x only):
  # - Zero background probes to other countries (prevents multi-country dashboard noise & Google region drift)
  # - Probes actual AI TLS endpoint ({HEALTHCHECK_URL}) so nodes that pass gstatic ping but drop AI TLS are excluded
  # - High hysteresis (`tolerance: {tolerance_ms}` ms) + `interval: {interval_sec}` s switches ONLY when the
  #   current node fails or degrades by >{tolerance_ms}ms relative to another healthy {region_upper} node.
  - name: "AI-PROXY"
    type: url-test
    use:
      - {provider_name}
    filter: '{reg_pat}'
    exclude-filter: '{LOW_MULT_EXCLUDE}'
    url: "{HEALTHCHECK_URL}"
    interval: {interval_sec}
    tolerance: {tolerance_ms}
    max-failed-times: 2
    lazy: false"""
    else:
        groups_yaml = f"""proxy-groups:
  - name: "AI-PROXY"
    type: fallback
    proxies:
      - "{region_upper}-STICKY"
      - "OVERSEAS-EMERGENCY"
    url: "{HEALTHCHECK_URL}"
    interval: {interval_sec}
    lazy: false

  - name: "{region_upper}-STICKY"
    type: url-test
    use:
      - {provider_name}
    filter: '{reg_pat}'
    exclude-filter: '{LOW_MULT_EXCLUDE}'
    url: "{HEALTHCHECK_URL}"
    interval: {interval_sec}
    tolerance: {tolerance_ms}
    max-failed-times: 2
    lazy: false

  - name: "OVERSEAS-EMERGENCY"
    type: url-test
    use:
      - {provider_name}
    filter: '(?i)(日本|JP|新加坡|SG|美国|US|台湾|TW|韩国|KR)'
    exclude-filter: '(?i)(香港|HK|俄罗斯|RU|下载专用|免费|剩余|到期|官网|套餐|重置|群)'
    url: "{HEALTHCHECK_URL}"
    interval: {interval_sec}
    tolerance: {tolerance_ms}
    lazy: true"""

    return f"""{groups_yaml}

rules:
  - IP-CIDR,127.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - DOMAIN-SUFFIX,local,DIRECT
  - DOMAIN-SUFFIX,cn,DIRECT
  - DOMAIN-SUFFIX,baidu.com,DIRECT
  - DOMAIN-SUFFIX,aliyun.com,DIRECT
  - DOMAIN-SUFFIX,aliyuncs.com,DIRECT
  - DOMAIN-SUFFIX,tsinghua.edu.cn,DIRECT
  - DOMAIN-SUFFIX,ustc.edu.cn,DIRECT
  - MATCH,AI-PROXY
"""


def build_subscription_config(
    sub_url: str,
    secret: str,
    region: str = "jp",
    tolerance_ms: int = 300,
    interval_sec: int = 1800,
    cross_region_fallback: bool = False,
) -> str:
    tail = build_sticky_groups_and_rules("main_sub", region, tolerance_ms, interval_sec, cross_region_fallback)
    # Note: provider-level health-check is intentionally disabled (`enable: false`) so Mihomo does NOT
    # probe all 46 multi-country nodes in the background every few minutes and pollute backend logs.
    return f"""# Auto-generated 24/7 sticky-{region.upper()} Mihomo config for Antigravity & Codex Auto Research
# Permissions: 0600 (contains subscription provider URL)

mixed-port: {PROXY_PORT}
allow-lan: false
bind-address: "127.0.0.1"
mode: rule
log-level: warning
ipv6: false
external-controller: "127.0.0.1:{CTRL_PORT}"
secret: {yaml_quote(secret)}

profile:
  store-selected: true

dns:
  enable: false

proxy-providers:
  main_sub:
    type: http
    url: {yaml_quote(sub_url)}
    interval: 43200
    path: ./providers/main_sub.yaml
    header:
      User-Agent:
        - "Wget/1.21.2"
    health-check:
      enable: false

{tail}"""


def build_local_provider_config(
    local_yaml_rel: str,
    secret: str,
    region: str = "jp",
    tolerance_ms: int = 300,
    interval_sec: int = 1800,
    cross_region_fallback: bool = False,
) -> str:
    tail = build_sticky_groups_and_rules("imported_sub", region, tolerance_ms, interval_sec, cross_region_fallback)
    return f"""# Auto-generated 24/7 sticky-{region.upper()} Mihomo config using imported Clash YAML
mixed-port: {PROXY_PORT}
allow-lan: false
bind-address: "127.0.0.1"
mode: rule
log-level: warning
ipv6: false
external-controller: "127.0.0.1:{CTRL_PORT}"
secret: {yaml_quote(secret)}

profile:
  store-selected: true

dns:
  enable: false

proxy-providers:
  imported_sub:
    type: file
    path: {yaml_quote(local_yaml_rel)}
    health-check:
      enable: false

{tail}"""


def write_systemd_unit(mihomo_bin: Path) -> None:
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    unit = f"""[Unit]
Description=Dedicated 24/7 Mihomo Proxy for Antigravity & Codex Auto Research
After=network-online.target

[Service]
Type=simple
Environment="HTTP_PROXY=" "HTTPS_PROXY=" "http_proxy=" "https_proxy=" "ALL_PROXY=" "all_proxy="
ExecStart={mihomo_bin} -d {CONF_DIR} -f {CONF_FILE}
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=default.target
"""
    SERVICE_FILE.write_text(unit, encoding="utf-8")


def controller_request(secret: str, path: str, method: str = "GET", payload: dict | None = None) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {"Authorization": f"Bearer {secret}"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{CTRL_PORT}{path}", data=data, headers=headers, method=method)
    with opener.open(req, timeout=8) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def inspect_active_route(secret: str) -> None:
    try:
        leaf = "COMPATIBLE"
        chain = ["AI-PROXY"]
        for _ in range(12):
            top = controller_request(secret, "/proxies/AI-PROXY")
            cur = top.get("now") or ""
            chain = ["AI-PROXY"]
            while cur:
                chain.append(cur)
                sub_info = controller_request(secret, f"/proxies/{urllib.parse.quote(cur, safe='')}")
                nxt = sub_info.get("now")
                if not nxt:
                    hist = sub_info.get("history") or []
                    delay = hist[-1].get("delay") if hist else None
                    leaf = f"{cur!r}" + (f" ({delay} ms)" if delay else "")
                    break
                cur = nxt
            if "COMPATIBLE" not in leaf and len(chain) >= 2:
                break
            time.sleep(0.5)
        route_str = " -> ".join(chain[:-1] + [leaf]) if len(chain) >= 2 else f"AI-PROXY -> {leaf}"
        print(f"Active Sticky Exit: {route_str}")
    except Exception as exc:
        print(f"Controller route query warning: {exc}")


def hot_reload_or_start(mihomo_bin: Path, old_secret: str | None) -> bool:
    write_systemd_unit(mihomo_bin)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", SERVICE_NAME], check=True)

    # Zero-downtime hot reload if already running (preserves active streams like running `ar-conpt`)
    if old_secret and is_port_open("127.0.0.1", CTRL_PORT):
        try:
            controller_request(old_secret, "/configs?force=true", method="PUT", payload={"path": str(CONF_FILE)})
            return True
        except Exception:
            pass

    subprocess.run(["systemctl", "--user", "restart", SERVICE_NAME], check=True)
    for _ in range(24):
        if is_port_open("127.0.0.1", PROXY_PORT):
            return True
        time.sleep(0.5)
    return is_port_open("127.0.0.1", PROXY_PORT)


def probe_targets(proxy_url: str) -> int:
    targets = [
        ("Antigravity (Google)", "https://antigravity.google"),
        ("CloudCode PA (API)", "https://daily-cloudcode-pa.googleapis.com"),
        ("OpenAI API", "https://api.openai.com/v1/models"),
        ("GitHub", "https://github.com"),
        ("HuggingFace", "https://huggingface.co"),
    ]
    ok_count = 0
    env = os.environ.copy()
    env["NO_PROXY"] = ""
    env["no_proxy"] = ""
    print(f"Testing outbound connectivity via {proxy_url} ...")
    for label, url in targets:
        r = subprocess.run(
            ["curl", "-sS", "-o", "/dev/null", "-w", "HTTP %{http_code} (%{time_total}s)", "--max-time", "8", "-x", proxy_url, url],
            capture_output=True,
            text=True,
            env=env,
        )
        stat_str = (r.stdout or "").strip()
        if r.returncode == 0 and stat_str and not stat_str.startswith("HTTP 000"):
            print(f"  [ OK ] {label:<22} -> {stat_str}")
            ok_count += 1
        else:
            err = (r.stderr or "").strip().splitlines()[-1] if r.stderr else f"exit {r.returncode}"
            print(f"  [WARN] {label:<22} -> {err}")
    return ok_count


def cmd_setup(args: argparse.Namespace) -> int:
    mihomo_bin = find_mihomo_bin()
    if not mihomo_bin:
        print(f"Error: `mihomo` binary not found in {USER_HOME / '.local/bin/mihomo'} or PATH.", file=sys.stderr)
        return 1

    CONF_DIR.mkdir(parents=True, exist_ok=True)
    PROVIDER_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONF_DIR, 0o700)

    old_secret, existing_url = read_existing_secret_and_url()
    secret = old_secret or secrets.token_hex(16)

    if args.prompt_url:
        sub_url = getpass.getpass("Paste your Clash subscription URL (hidden, not echoed): ").strip()
        if not sub_url.startswith(("http://", "https://")):
            print("Error: subscription URL must start with http:// or https://", file=sys.stderr)
            return 1
        cfg_text = build_subscription_config(
            sub_url, secret, args.region, args.tolerance, args.interval, args.cross_region_fallback
        )
    elif args.sub_url:
        sub_url = args.sub_url.strip()
        if not sub_url.startswith(("http://", "https://")):
            print("Error: --sub-url must start with http:// or https://", file=sys.stderr)
            return 1
        cfg_text = build_subscription_config(
            sub_url, secret, args.region, args.tolerance, args.interval, args.cross_region_fallback
        )
    elif args.reuse_existing:
        if not existing_url:
            print("Error: No existing subscription URL found in config.yaml to reuse.", file=sys.stderr)
            return 1
        cfg_text = build_subscription_config(
            existing_url, secret, args.region, args.tolerance, args.interval, args.cross_region_fallback
        )
    elif args.import_yaml:
        src_yaml = Path(args.import_yaml).expanduser().resolve()
        if not src_yaml.is_file():
            print(f"Error: YAML file not found: {src_yaml}", file=sys.stderr)
            return 1
        dst_provider = PROVIDER_DIR / "imported_nodes.yaml"
        dst_provider.write_bytes(src_yaml.read_bytes())
        os.chmod(dst_provider, 0o600)
        cfg_text = build_local_provider_config(
            "./providers/imported_nodes.yaml",
            secret,
            args.region,
            args.tolerance,
            args.interval,
            args.cross_region_fallback,
        )
    else:
        print("Error: specify `--prompt-url`, `--sub-url`, `--import-yaml`, or `--reuse-existing`.", file=sys.stderr)
        return 1

    CONF_FILE.write_text(cfg_text, encoding="utf-8")
    os.chmod(CONF_FILE, 0o600)

    val = subprocess.run([str(mihomo_bin), "-t", "-d", str(CONF_DIR), "-f", str(CONF_FILE)], capture_output=True, text=True)
    if val.returncode != 0:
        print(f"Mihomo config validation failed:\n{val.stdout}\n{val.stderr}", file=sys.stderr)
        return 1

    if not hot_reload_or_start(mihomo_bin, old_secret):
        print(f"Error: {SERVICE_NAME} did not open 127.0.0.1:{PROXY_PORT}.", file=sys.stderr)
        return 1

    time.sleep(0.5)
    try:
        q_url = urllib.parse.quote(HEALTHCHECK_URL, safe="")
        controller_request(secret, f"/group/AI-PROXY/delay?timeout=5000&url={q_url}")
    except Exception:
        time.sleep(2.0)

    mode_tag = f"{args.region.upper()}+cross-region-fallback" if args.cross_region_fallback else f"strict-{args.region.upper()}-only"
    print(
        f"Service `{SERVICE_NAME}` is ACTIVE on http://127.0.0.1:{PROXY_PORT} "
        f"(mode={mode_tag}, tolerance={args.tolerance}ms, check_interval={args.interval}s)."
    )
    inspect_active_route(secret)
    probe_targets(f"http://127.0.0.1:{PROXY_PORT}")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    local_up = is_port_open("127.0.0.1", PROXY_PORT)
    tunnel_up = is_port_open("127.0.0.1", 17899)
    print(f"1. Local 24/7 AI Mihomo (127.0.0.1:{PROXY_PORT}, `{SERVICE_NAME}`): {'ONLINE (preferred)' if local_up else 'NOT CONFIGURED / OFFLINE'}")
    print(f"2. SSH Reverse Tunnel   (127.0.0.1:17899):                   {'ONLINE (client-dependent)' if tunnel_up else 'OFFLINE'}")
    if local_up:
        sec, _ = read_existing_secret_and_url()
        if sec:
            inspect_active_route(sec)
        probe_targets(f"http://127.0.0.1:{PROXY_PORT}")
    elif tunnel_up:
        probe_targets("http://127.0.0.1:17899")
    return 0


def cmd_stop(_: argparse.Namespace) -> int:
    subprocess.run(["systemctl", "--user", "disable", "--now", SERVICE_NAME], check=False)
    print(f"Stopped `{SERVICE_NAME}`.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Setup and manage sticky single-region 24/7 local Mihomo proxy")
    sub = p.add_subparsers(dest="subcmd", required=True)

    s_up = sub.add_parser("setup", help=f"Configure and hot-reload/start `{SERVICE_NAME}` on 127.0.0.1:{PROXY_PORT}")
    grp = s_up.add_mutually_exclusive_group(required=True)
    grp.add_argument("--prompt-url", action="store_true", help="Prompt interactively for Clash subscription URL (hidden)")
    grp.add_argument("--sub-url", help="Clash subscription HTTP(S) URL")
    grp.add_argument("--import-yaml", help="Path to an exported Clash YAML file")
    grp.add_argument("--reuse-existing", action="store_true", help="Keep existing subscription URL and update region/stickiness policy")
    s_up.add_argument("--region", choices=sorted(REGION_PATTERNS.keys()), default="jp", help="Preferred sticky exit region (default: jp)")
    s_up.add_argument("--tolerance", type=int, default=300, help="Latency hysteresis in ms before switching nodes within region (default: 300)")
    s_up.add_argument("--interval", type=int, default=1800, help="Health-check interval in seconds (default: 1800 = 30 min)")
    s_up.add_argument(
        "--cross-region-fallback",
        action="store_true",
        help="Add cross-region fallback group (off by default so non-target countries receive zero probe traffic)",
    )
    s_up.set_defaults(func=cmd_setup)

    s_st = sub.add_parser("status", help="Show active sticky exit node and test AI endpoints")
    s_st.set_defaults(func=cmd_status)

    s_sp = sub.add_parser("stop", help=f"Stop and disable `{SERVICE_NAME}`")
    s_sp.set_defaults(func=cmd_stop)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
