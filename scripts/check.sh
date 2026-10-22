#!/usr/bin/env bash
# Local mirror of .github/workflows/ci.yml: run before every commit.
# Usage: scripts/check.sh [--no-e2e]
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
export PATH="$HOME/.local/opt/node/bin:$HOME/.local/bin:$PATH"

step() { printf '\n== %s\n' "$*"; }

for project in api services/common services/seg services/ocsr services/cls golden; do
  step "python: $project"
  (cd "$project" && uv sync --locked -q && uv run -q ruff check . && uv run -q ruff format --check . \
    && uv run -q mypy && uv run -q pytest -q)
done

step "web: lint, types, unit tests, build"
(cd web && npm ci --silent && npm run -s lint && npm run -s typecheck && npm test --silent && npm run -s build >/dev/null)

if [[ "${1:-}" != "--no-e2e" ]]; then
  step "web: e2e (Playwright image)"
  docker run --rm --network host --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$ROOT/web":/work -w /work mcr.microsoft.com/playwright:v1.64.1-noble npx playwright test
fi

step "trivy (api and web fail on HIGH/CRITICAL; services report only)"
mkdir -p "$ROOT/.cache/trivy"
for target in api:1 web:1 services:0; do
  path=${target%%:*} code=${target##*:}
  docker run --rm -v "$ROOT/$path":/scan:ro -v "$ROOT/.cache/trivy":/root/.cache aquasec/trivy:0.76.1 \
    fs --quiet --severity HIGH,CRITICAL --ignore-unfixed --skip-dirs /scan/node_modules \
    --exit-code "$code" /scan
done

step "all checks passed"
