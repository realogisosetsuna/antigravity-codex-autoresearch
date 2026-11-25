#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
HUB_ROOT="$(cd -- "$(dirname -- "${SCRIPT_PATH}")/.." && pwd)"
TEMPLATES_DIR="${HUB_ROOT}/templates"
PROJECTS_ROOT="${HUB_ROOT}/projects"
PYTHON_BIN="${PYTHON_BIN:-$(command -v /usr/bin/python3 || command -v python3)}"

if [[ $# -lt 1 ]] || [[ "${1:-}" == "-h" ]] || [[ "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  init_experiment.sh <project_name_or_abs_path> [objective] [--data-disk /mnt/data1/<user>]

Initializes a Git-backed Auto Research repository with:
  - Shared Memory Bus: RESEARCH_PLAN.md, RESEARCH_GOAL.json, EXPERIMENTS.tsv, BLOCKERS.md
  - Workspace plugin link: .agents/plugins.json (loads `auto-research` MCP + hooks + skills)
  - Standard layout: src/, configs/, tests/, scripts/, docs/
  - Optional external data/runs symlink on <data_disk>/<project_name>
  - Initial Git commit on `main` (required by Codex `--worktree` and `astra_review`)
EOF
  exit 0
fi

RAW_TARGET="$1"
shift
OBJECTIVE="Autonomous research investigation"
if [[ $# -gt 0 ]] && [[ "${1:-}" != --* ]]; then
  OBJECTIVE="$1"
  shift
fi

DATA_DISK=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-disk)
      DATA_DISK="${2:?Missing path after --data-disk}"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ "${RAW_TARGET}" == /* ]]; then
  TARGET_DIR="${RAW_TARGET}"
  PROJ_NAME="$(basename "${TARGET_DIR}")"
else
  PROJ_NAME="${RAW_TARGET}"
  TARGET_DIR="${PROJECTS_ROOT}/${PROJ_NAME}"
fi

mkdir -p "${TARGET_DIR}"/{src,configs,tests,scripts,docs,.agents}
CREATED_AT="$(date -Iseconds)"

render_template() {
  local src="$1"
  local dst="$2"
  if [[ ! -f "${dst}" ]]; then
    PROJECT_NAME="${PROJ_NAME}" CREATED_AT="${CREATED_AT}" OBJECTIVE="${OBJECTIVE}" SRC="${src}" DST="${dst}" \
      "${PYTHON_BIN}" -c '
import os
from pathlib import Path
text = Path(os.environ["SRC"]).read_text(encoding="utf-8")
for k in ("PROJECT_NAME", "CREATED_AT", "OBJECTIVE"):
    text = text.replace("{{" + k + "}}", os.environ[k])
Path(os.environ["DST"]).write_text(text, encoding="utf-8")
'
  fi
}

render_template "${TEMPLATES_DIR}/RESEARCH_PLAN.md" "${TARGET_DIR}/RESEARCH_PLAN.md"
render_template "${TEMPLATES_DIR}/RESEARCH_GOAL.json" "${TARGET_DIR}/RESEARCH_GOAL.json"
render_template "${TEMPLATES_DIR}/EXPERIMENTS.tsv" "${TARGET_DIR}/EXPERIMENTS.tsv"
render_template "${TEMPLATES_DIR}/BLOCKERS.md" "${TARGET_DIR}/BLOCKERS.md"

cat > "${TARGET_DIR}/.agents/plugins.json" <<EOF
{
  "entries": [
    {
      "path": "${HUB_ROOT}/plugins"
    }
  ]
}
EOF

if [[ ! -f "${TARGET_DIR}/.gitignore" ]]; then
  cat > "${TARGET_DIR}/.gitignore" <<'EOF'
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.venv/
runs/
data/
checkpoints/
repos/
*.pt
*.pth
*.ckpt
*.safetensors
*.wav
*.flac
*.mp3
*.tar*
agent.md
EOF
fi

if [[ -n "${DATA_DISK}" ]]; then
  EXT_DIR="${DATA_DISK%/}/${PROJ_NAME}"
  mkdir -p "${EXT_DIR}/runs" "${EXT_DIR}/data" "${EXT_DIR}/checkpoints"
  ln -sfn "${EXT_DIR}/runs" "${TARGET_DIR}/runs"
  ln -sfn "${EXT_DIR}/data" "${TARGET_DIR}/data"
  ln -sfn "${EXT_DIR}/checkpoints" "${TARGET_DIR}/checkpoints"
else
  mkdir -p "${TARGET_DIR}/runs"
fi

if [[ ! -d "${TARGET_DIR}/.git" ]]; then
  git -C "${TARGET_DIR}" init -b main >/dev/null 2>&1 || git -C "${TARGET_DIR}" init >/dev/null
fi

DEFAULT_GIT_USER="${USER:-researcher}"
if ! git -C "${TARGET_DIR}" config user.name >/dev/null 2>&1; then
  git -C "${TARGET_DIR}" config user.name "${DEFAULT_GIT_USER}"
fi
if ! git -C "${TARGET_DIR}" config user.email >/dev/null 2>&1; then
  git -C "${TARGET_DIR}" config user.email "${DEFAULT_GIT_USER}@local.research"
fi

if ! git -C "${TARGET_DIR}" rev-parse --verify HEAD >/dev/null 2>&1; then
  git -C "${TARGET_DIR}" add RESEARCH_PLAN.md RESEARCH_GOAL.json EXPERIMENTS.tsv BLOCKERS.md .gitignore .agents/plugins.json
  git -C "${TARGET_DIR}" commit -m "chore: initialize Auto Research repository (${PROJ_NAME})" >/dev/null
fi

echo "Initialized Auto Research project at: ${TARGET_DIR}"
echo "Git HEAD: $(git -C "${TARGET_DIR}" rev-parse --short HEAD)"
