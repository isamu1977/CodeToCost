#!/bin/bash
# run_dashboard.sh — inicia o dashboard local do CodeToCost (Streamlit).
#
# Ativa o virtualenv do pipeline (onde o Streamlit e as engines moram) e sobe
# o app em pipeline/dashboard.py.
#
#   ./run_dashboard.sh
#   ./run_dashboard.sh --server.port 8502

set -euo pipefail

cd "$(dirname "$0")"

source pipeline/core/.venv/bin/activate 2>/dev/null || true

exec streamlit run pipeline/dashboard.py "$@"
