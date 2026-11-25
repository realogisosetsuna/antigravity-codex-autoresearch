#!/usr/bin/env python3
"""Turnkey 24/7 headless Clash (`mihomo`) setup & health-check utility.

Eliminates dependence on laptop SSH reverse-tunnel (`127.0.0.1:17899`) so Antigravity (`agy`),
Codex (`gpt-6.2-astra`), and dataset/model downloads keep running uninterrupted when your
client machine sleeps or disconnects.
"""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
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


def build_subscription_config(sub_url: str, secret: str) -> str:
    return f"""# Auto-generated 24/7 headless Mihomo config for Antigravity & Codex Auto Research
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
    interval: 21600
    path: ./providers/main_sub.yaml
    header:
      User-Agent:
        - "Wget/1.21.2"
    health-check:
      enable: true
      interval: 180
      url: "https://www.gstatic.com/generate_204"

proxy-groups:
  - name: "AI-PROXY"
    type: fallback
    proxies:
      - "AI-US-SG-TW-IEPL"
      - "AI-IEPL-AUTO"
      - "AI-REGION-AUTO"
      - "ALL-AUTO"
    url: "https://www.gstatic.com/generate_204"
    interval: 180

  - name: "AI-US-SG-TW-IEPL"
    type: url-test
    use:
      - main_sub
    filter: '(?i)(美国|US|新加坡|SG|台湾|TW).*IEPL|IEPL.*(美国|US|新加坡|SG|台湾|TW)'
    exclude-filter: '(?i)(香港|HK|俄罗斯|RU|下载专用|免费|剩余|到期|官网|套餐|重置|群)'
    url: "https://www.gstatic.com/generate_204"
    interval: 180
    tolerance: 50

  - name: "AI-IEPL-AUTO"
    type: url-test
    use:
      - main_sub
    filter: '(?i)(IEPL)'
    exclude-filter: '(?i)(香港|HK|Hong Kong|俄罗斯|RU|下载专用|免费|剩余|到期|官网|套餐|重置|群)'
    url: "https://www.gstatic.com/generate_204"
    interval: 180
    tolerance: 50

  - name: "AI-REGION-AUTO"
    type: url-test
    use:
      - main_sub
    filter: '(?i)(US|United States|America|美国|SG|Singapore|新加坡|狮城|JP|Japan|日本|东京|大阪|TW|Taiwan|台湾|KR|Korea|韩国|UK|英国|DE|德国|CA|加拿大|AU|澳大利亚|FR|法国)'
    exclude-filter: '(?i)(香港|HK|Hong Kong|俄罗斯|RU|下载专用|免费|剩余|到期|官网|套餐|重置|群)'
    url: "https://www.gstatic.com/generate_204"
    interval: 180
    tolerance: 60

  - name: "ALL-AUTO"
    type: url-test
    use:
      - main_sub
    exclude-filter: '(?i)(香港|HK|俄罗斯|RU|下载专用|免费|剩余|到期|官网|套餐|重置|群)'
    url: "https://www.gstatic.com/generate_204"
    interval: 180
    tolerance: 80

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


def build_local_provider_config(local_yaml_rel: str, secret: str) -> str:
    return f"""# Auto-generated 24/7 headless Mihomo config using imported Clash YAML
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
      enable: true
      interval: 180
      url: "https://www.gstatic.com/generate_204"

proxy-groups:
  - name: "AI-PROXY"
    type: fallback
    proxies:
      - "AI-REGION-AUTO"
      - "ALL-AUTO"
    url: "https://www.gstatic.com/generate_204"
    interval: 180

  - name: "AI-REGION-AUTO"
    type: url-test
    use:
      - imported_sub
    filter: '(?i)(US|United States|America|美国|SG|Singapore|新加坡|狮城|JP|Japan|日本|东京|大阪|TW|Taiwan|台湾|KR|Korea|韩国|UK|英国|DE|德国)'
    exclude-filter: '(?i)(香港|HK|俄罗斯|RU|下载专用|免费|剩余|到期|官网|套餐|重置|群)'
    url: "https://www.gstatic.com/generate_204"
    interval: 180
    tolerance: 60

  - name: "ALL-AUTO"
    type: url-test
    use:
      - imported_sub
    exclude-filter: '(?i)(香港|HK|俄罗斯|RU|下载专用|免费|剩余|到期|官网|套餐|重置|群)'
    url: "https://www.gstatic.com/generate_204"
    interval: 180
    tolerance: 80

rules:
  - IP-CIDR,127.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - DOMAIN-SUFFIX,local,DIRECT
  - DOMAIN-SUFFIX,cn,DIRECT
  - DOMAIN-SUFFIX,baidu.com,DIRECT
  - DOMAIN-SUFFIX,aliyun.com,DIRECT
  - DOMAIN-SUFFIX,tsinghua.edu.cn,DIRECT
  - MATCH,AI-PROXY
"""


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

    secret = secrets.token_hex(16)
    if args.prompt_url:
        sub_url = getpass.getpass("Paste your Clash subscription URL (hidden, not echoed): ").strip()
        if not sub_url.startswith(("http://", "https://")):
            print("Error: subscription URL must start with http:// or https://", file=sys.stderr)
            return 1
        cfg_text = build_subscription_config(sub_url, secret)
    elif args.sub_url:
        sub_url = args.sub_url.strip()
        if not sub_url.startswith(("http://", "https://")):
            print("Error: --sub-url must start with http:// or https://", file=sys.stderr)
            return 1
        cfg_text = build_subscription_config(sub_url, secret)
    elif args.import_yaml:
        src_yaml = Path(args.import_yaml).expanduser().resolve()
        if not src_yaml.is_file():
            print(f"Error: YAML file not found: {src_yaml}", file=sys.stderr)
            return 1
        dst_provider = PROVIDER_DIR / "imported_nodes.yaml"
        dst_provider.write_bytes(src_yaml.read_bytes())
        os.chmod(dst_provider, 0o600)
        cfg_text = build_local_provider_config("./providers/imported_nodes.yaml", secret)
    else:
        print("Error: specify one of `--prompt-url`, `--sub-url <URL>`, or `--import-yaml <FILE>`.", file=sys.stderr)
        return 1

    CONF_FILE.write_text(cfg_text, encoding="utf-8")
    os.chmod(CONF_FILE, 0o600)

    val = subprocess.run([str(mihomo_bin), "-t", "-d", str(CONF_DIR), "-f", str(CONF_FILE)], capture_output=True, text=True)
    if val.returncode != 0:
        print(f"Mihomo config validation failed:\n{val.stdout}\n{val.stderr}", file=sys.stderr)
        return 1

    write_systemd_unit(mihomo_bin)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE_NAME], check=True)
    subprocess.run(["systemctl", "--user", "restart", SERVICE_NAME], check=True)

    for _ in range(24):
        if is_port_open("127.0.0.1", PROXY_PORT):
            break
        time.sleep(0.5)

    if not is_port_open("127.0.0.1", PROXY_PORT):
        print(f"Error: {SERVICE_NAME} did not open 127.0.0.1:{PROXY_PORT}. Check `journalctl --user -u {SERVICE_NAME} -n 40`.", file=sys.stderr)
        return 1

    time.sleep(4.0)
    print(f"Service `{SERVICE_NAME}` is ACTIVE on http://127.0.0.1:{PROXY_PORT} (survives SSH disconnect & client sleep).")
    probe_targets(f"http://127.0.0.1:{PROXY_PORT}")
    print(f"\nAll new `agy-research` sessions and Astra MCP calls will now automatically prefer 127.0.0.1:{PROXY_PORT}!")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    local_up = is_port_open("127.0.0.1", PROXY_PORT)
    tunnel_up = is_port_open("127.0.0.1", 17899)
    print(f"1. Local 24/7 AI Mihomo (127.0.0.1:{PROXY_PORT}, `{SERVICE_NAME}`): {'ONLINE (preferred)' if local_up else 'NOT CONFIGURED / OFFLINE'}")
    print(f"2. SSH Reverse Tunnel   (127.0.0.1:17899):                   {'ONLINE (client-dependent)' if tunnel_up else 'OFFLINE'}")
    if local_up:
        probe_targets(f"http://127.0.0.1:{PROXY_PORT}")
    elif tunnel_up:
        probe_targets("http://127.0.0.1:17899")
    return 0


def cmd_stop(_: argparse.Namespace) -> int:
    subprocess.run(["systemctl", "--user", "disable", "--now", SERVICE_NAME], check=False)
    print(f"Stopped `{SERVICE_NAME}`.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Setup and manage 24/7 local Mihomo proxy for Auto Research")
    sub = p.add_subparsers(dest="subcmd", required=True)

    s_up = sub.add_parser("setup", help=f"Configure and start 24/7 `{SERVICE_NAME}` on 127.0.0.1:{PROXY_PORT}")
    grp = s_up.add_mutually_exclusive_group(required=True)
    grp.add_argument("--prompt-url", action="store_true", help="Prompt interactively for Clash subscription URL (hidden, no bash_history)")
    grp.add_argument("--sub-url", help="Clash subscription HTTP(S) URL")
    grp.add_argument("--import-yaml", help="Path to an exported Clash YAML file")
    s_up.set_defaults(func=cmd_setup)

    s_st = sub.add_parser("status", help="Check local proxy vs SSH tunnel status and test AI endpoints")
    s_st.set_defaults(func=cmd_status)

    s_sp = sub.add_parser("stop", help=f"Stop and disable `{SERVICE_NAME}`")
    s_sp.set_defaults(func=cmd_stop)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
