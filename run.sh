#!/usr/bin/env bash
# ====================================================================
#  Start the AI Recruitment System (Mac / Linux).
#  Run:  bash run.sh    then open  http://127.0.0.1:8000/ui/
# ====================================================================
set -e
cd "$(dirname "$0")"
if [ ! -f .venv/bin/activate ]; then
  echo "[X] Not set up yet. Run:  bash setup.sh"
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "Open http://127.0.0.1:8000/ui/  (Ctrl+C to stop)"
python -m uvicorn main:app --host 127.0.0.1 --port 8000
