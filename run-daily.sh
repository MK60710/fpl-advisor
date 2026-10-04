#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
python3 -m agents.run_weekly
