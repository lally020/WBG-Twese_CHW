#!/bin/bash
set -e
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Install Python 3.11 or newer, then rerun this file."
  read -r -p "Press Return to close..." dummy
  exit 1
fi
if [ ! -d ".venv" ]; then
  echo "Preparing Python environment (first time needs internet to download dependencies)..."
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt
if [ -n "${COMPASS_LOCAL_MODEL:-}" ]; then
  python check_local_model.py
fi
python launch.py
