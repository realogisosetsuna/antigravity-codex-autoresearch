#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
HUB_ROOT="$(cd -- "$(dirname -- "${SCRIPT_PATH}")/.." && pwd)"
PLUGIN_SRC="${HUB_ROOT}/plugins/auto-research"
PYTHON_BIN="${PYTHON_BIN:-$(command -v /usr/bin/python3 || command -v python3)}"
AGY_BIN="${AGY_BIN:-$(command -v agy || echo "${HOME}/.local/bin/agy")}"
if [[ ! -x "${AGY_BIN}" ]] && [[ -x "${HOME}/agy" ]]; then
  AGY_BIN="${HOME}/agy"
fi

# Render machine-local absolute paths into plugin mcp_config.json & hooks.json from templates
HUB_ROOT="${HUB_ROOT}" PYTHON_BIN="${PYTHON_BIN}" PLUGIN_SRC="${PLUGIN_SRC}" "${PYTHON_BIN}" - <<'PY'
import os
from pathlib import Path

plugin_src = Path(os.environ["PLUGIN_SRC"])
hub_root = os.environ["HUB_ROOT"]
py_bin = os.environ["PYTHON_BIN"]

for stem in ("mcp_config", "hooks"):
    tpl = Path(hub_root) / "templates" / "plugin" / f"{stem}.template.json"
    dst = plugin_src / f"{stem}.json"
    text = tpl.read_text(encoding="utf-8")
    text = text.replace("{{HUB_ROOT}}", hub_root).replace("{{PYTHON_BIN}}", py_bin)
    dst.write_text(text, encoding="utf-8")
PY

"${AGY_BIN}" plugin validate --strict "${PLUGIN_SRC}"
"${AGY_BIN}" plugin install "${PLUGIN_SRC}"
"${AGY_BIN}" plugin enable auto-research
"${AGY_BIN}" plugin list
