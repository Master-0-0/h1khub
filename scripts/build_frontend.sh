#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/frontend"
npm install --no-audit --no-fund
npm run build
echo "Built $ROOT/frontend/dist"
