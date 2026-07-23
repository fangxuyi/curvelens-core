#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${CURVELENS_DASHBOARD_PORT:-8501}"

cd "${REPO_ROOT}/ccvm"
exec env -u CCVM_PRODUCT -u CCVM_DATA_DIR \
  .venv/bin/streamlit run app/dashboard.py \
  --server.headless true \
  --server.address 127.0.0.1 \
  --server.port "${PORT}"
