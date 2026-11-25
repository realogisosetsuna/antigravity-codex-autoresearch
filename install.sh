#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
HUB_ROOT="$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v /usr/bin/python3 || command -v python3)}"

echo "=== Bootstrapping Auto Research Hub at: ${HUB_ROOT} ==="

mkdir -p "${HUB_ROOT}/logs/astra_calls" "${HUB_ROOT}/projects" "${HOME}/.local/bin" "${HOME}/.codex" "${HOME}/.agents"

chmod +x "${HUB_ROOT}/install.sh" \
         "${HUB_ROOT}/bin/agy-research" \
         "${HUB_ROOT}/mcp/codex_astra_mcp.py" \
         "${HUB_ROOT}/hooks/pre_tool_guard.py" \
         "${HUB_ROOT}/hooks/stop_goal_gate.py" \
         "${HUB_ROOT}/proxy/setup_local_mihomo.py" \
         "${HUB_ROOT}/scripts/ar_config.py" \
         "${HUB_ROOT}/scripts/cluster_ctl.py" \
         "${HUB_ROOT}/scripts/init_experiment.sh" \
         "${HUB_ROOT}/scripts/log_experiment.py" \
         "${HUB_ROOT}/scripts/sync_plugin.sh" \
         "${HUB_ROOT}/scripts/self_check.py"

# 1. Symlink launcher & safe Codex profiles
ln -sfn "${HUB_ROOT}/bin/agy-research" "${HOME}/.local/bin/agy-research"
ln -sfn "${HUB_ROOT}/codex_profiles/astra-ro.toml" "${HOME}/.codex/astra-ro.config.toml"
ln -sfn "${HUB_ROOT}/codex_profiles/astra-code.toml" "${HOME}/.codex/astra-code.config.toml"

# Ensure ~/.local/bin is on PATH for current process
export PATH="${HOME}/.local/bin:${PATH}"

# 2. Check agy & codex availability
if [[ -x "${HOME}/agy" ]] && [[ ! -e "${HOME}/.local/bin/agy" ]]; then
  ln -sfn "${HOME}/agy" "${HOME}/.local/bin/agy"
fi

MISSING=0
if ! command -v agy >/dev/null 2>&1; then
  echo "[WARN] 'agy' (Antigravity CLI) not found on PATH."
  echo "       Install via: curl -fsSL https://antigravity.google/cli/install.sh | bash"
  MISSING=1
fi
if ! command -v codex >/dev/null 2>&1; then
  echo "[WARN] 'codex' (OpenAI Codex CLI) not found on PATH."
  echo "       Ensure 'codex' is installed and authenticated ('codex login') before calling Astra tools."
  MISSING=1
fi

# 3. Render machine-local paths into plugin templates
PLUGIN_SRC="${HUB_ROOT}/plugins/auto-research"
HUB_ROOT="${HUB_ROOT}" PYTHON_BIN="${PYTHON_BIN}" PLUGIN_SRC="${PLUGIN_SRC}" "${PYTHON_BIN}" - <<'PY'
import os
from pathlib import Path

plugin_src = Path(os.environ["PLUGIN_SRC"])
hub_root = os.environ["HUB_ROOT"]
py_bin = os.environ["PYTHON_BIN"]

for stem in ("mcp_config", "hooks"):
    tpl = Path(hub_root) / "templates" / "plugin" / f"{stem}.template.json"
    dst = plugin_src / f"{stem}.json"
    dst.write_text(
        tpl.read_text(encoding="utf-8").replace("{{HUB_ROOT}}", hub_root).replace("{{PYTHON_BIN}}", py_bin),
        encoding="utf-8",
    )
PY

# Register workspace & global plugin manifests
cat > "${HOME}/.agents/plugins.json" <<EOF
{
  "entries": [
    {
      "path": "${HUB_ROOT}/plugins"
    }
  ]
}
EOF

# 4. Install & enable Antigravity plugin + run self-check if binaries are ready
if command -v agy >/dev/null 2>&1; then
  "${HUB_ROOT}/scripts/sync_plugin.sh"
fi

if [[ "${MISSING}" -eq 0 ]]; then
  "${PYTHON_BIN}" "${HUB_ROOT}/scripts/self_check.py"
fi

echo ""
echo "=== Auto Research Hub Ready ==="
echo "  Launcher : agy-research -t ar_demo"
if [[ ! -f "${HUB_ROOT}/config/cluster.local.json" ]]; then
  echo "  Multi-node (optional): cp ${HUB_ROOT}/config/cluster.example.json ${HUB_ROOT}/config/cluster.local.json && edit node IPs/disks"
fi
echo "  24/7 Proxy (optional): python3 ${HUB_ROOT}/proxy/setup_local_mihomo.py setup --prompt-url"
