#!/usr/bin/env bash
# ====================================================================
#  AI Recruitment System - one-time setup (Mac / Linux)
#  Run once:   bash setup.sh
# ====================================================================
set -e
cd "$(dirname "$0")"
echo "=== AI Recruitment System: setup starting ==="

if ! command -v python3 >/dev/null 2>&1; then
  echo "[X] Python 3 is not installed. Install Python 3.11+ then re-run."
  exit 1
fi

echo "[1/4] Creating the virtual environment (.venv) ..."
python3 -m venv .venv

echo "[2/4] Activating it ..."
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[3/4] Installing required packages (a few minutes) ..."
python -m pip install --upgrade pip
pip install -r requirements.txt

echo "[4/4] Preparing your settings file ..."
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    Created .env from the template."
else
  echo "    .env already exists - left as-is."
fi

echo
echo "=== Setup complete! ==="
echo "NEXT: edit .env with your keys (see MIGRATION.md Step 3), then run:  bash run.sh"
