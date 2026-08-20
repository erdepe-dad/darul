#!/usr/bin/env bash
set -euo pipefail

darul_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$darul_root/.graph_engine/neo4j.env"
python_bin="$darul_root/.venv/bin/python3"

if [[ ! -x "$python_bin" ]]; then
  echo "Darul virtual environment is missing. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [[ -f "$env_file" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
fi

export PYTHONPATH="$darul_root${PYTHONPATH:+:$PYTHONPATH}"
exec "$python_bin" -m graph_engine.mcp_server
