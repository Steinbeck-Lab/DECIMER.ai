#!/usr/bin/env bash
# Local mirror of .github/workflows/ci.yml: run before every commit.
# Usage: scripts/check.sh [--no-e2e] [--release] [--live]
#   --release  also runs the release gate (web/src/release.test.ts): it fails while any [CONFIRM: ...] marker is
#              left in the copy (web/src/strings.ts) or in the built web/dist/. For the cutover, not for every commit.
#   --live     also runs the live spec (web/e2e/live) against a stack that is already running, one job at a time:
#              LIVE_BASE_URL (default http://127.0.0.1:8090). Opt-in; never part of CI or of the default run.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
export PATH="$HOME/.local/opt/node/bin:$HOME/.local/bin:$PATH"

step() { printf '\n== %s\n' "$*"; }

E2E=1 RELEASE_GATE=0 LIVE=0
for arg in "$@"; do
  case $arg in
    --no-e2e) E2E=0 ;;
    --release) RELEASE_GATE=1 ;;
    --live) LIVE=1 ;;
    *) echo "unknown option: $arg (usage: scripts/check.sh [--no-e2e] [--release] [--live])" >&2; exit 2 ;;
  esac
done

for project in api services/common services/seg services/ocsr services/cls services/chem golden; do
  step "python: $project"
  (cd "$project" && uv sync --locked -q && uv run -q ruff check . && uv run -q ruff format --check . \
    && uv run -q mypy && uv run -q pytest -q)
  if [[ $project == api ]]; then
    # The web app's types come from the API's OpenAPI schema: fails when the API changed and web/src/api/openapi.json
    # did not (run "npm run gen:api" in web/ and commit). No Node needed.
    step "api: OpenAPI schema matches web/src/api/openapi.json"
    (cd api && uv run -q python ../web/scripts/export-openapi.py | diff - ../web/src/api/openapi.json)
  fi
done

step "web: lint, types, unit tests, build"
(cd web && npm ci --silent && npm run -s lint && npm run -s typecheck && npm test --silent \
  && node scripts/gen-api.mjs --check --types-only && npm run -s build >/dev/null)

if [[ $RELEASE_GATE == 1 ]]; then
  step "web: release gate (no [CONFIRM] marker left in the copy or in dist/)"
  (cd web && RELEASE=1 npx vitest run src/release.test.ts)
fi

if [[ $E2E == 1 ]]; then
  step "web: e2e (Playwright image)"
  docker run --rm --network host --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$ROOT/web":/work -w /work mcr.microsoft.com/playwright:v1.64.1-noble npx playwright test
fi

if [[ $LIVE == 1 ]]; then
  step "web: live e2e against ${LIVE_BASE_URL:-http://127.0.0.1:8090} (running stack, Playwright image)"
  # golden/ is mounted for the PDF the spec uploads.
  docker run --rm --network host --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -e LIVE_BASE_URL="${LIVE_BASE_URL:-http://127.0.0.1:8090}" -e LIVE_PDF=/golden/zou2024_penicillium_shentong.pdf \
    -v "$ROOT/web":/work -v "$ROOT/golden":/golden:ro -w /work \
    mcr.microsoft.com/playwright:v1.64.1-noble npx playwright test --config e2e/live/live.config.ts
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
