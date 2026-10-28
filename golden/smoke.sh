#!/usr/bin/env bash
# The golden-set check as a deployment gate: runs the golden paper through a stack's /api and applies the pass rule
# (golden/README.md): at least as many structures right as the earlier version of decimer.ai (13 of the 24 scored
# labels), no more invalid SMILES than it (1), no more cut-off predictions (`incomplete`: the model stopped at its token
# limit) than the one in the drawing that is excluded from scoring (`1 (Fig 3)`), and no crash (a failed job, a refused
# upload or a timeout is exit 1 of `decimer-golden next`).
#
#   golden/smoke.sh [BASE_URL]        run from the repository root; default http://127.0.0.1:8090 (the edge, not a public URL)
#
# GOLDEN_MIN_CORRECT (13), GOLDEN_MAX_INVALID (1) and GOLDEN_MAX_INCOMPLETE (1) override the thresholds. It needs uv, takes
# about 80 to 100 s and loads the model services like one visitor with a 10-page PDF. The run is left in
# golden/runs/next-<time>/ (keep it as the record of the check, or delete it). Exit 0: pass; 1: the rule failed or the
# run crashed; 2: could not run.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
base=${1:-http://127.0.0.1:8090}
min_correct=${GOLDEN_MIN_CORRECT:-13}
max_invalid=${GOLDEN_MAX_INVALID:-1}
max_incomplete=${GOLDEN_MAX_INCOMPLETE:-1}
export PATH="$HOME/.local/bin:$PATH"
uv=${GOLDEN_UV:-uv}            # GOLDEN_UV and GOLDEN_DIR exist so that the pass rule can be tested with a stub
command -v "$uv" > /dev/null || { echo "golden/smoke.sh: uv not found (install it from https://docs.astral.sh/uv/)" >&2; exit 2; }

cd "${GOLDEN_DIR:-$here}"
out=$(nice -n 10 "$uv" run --quiet decimer-golden next --base-url "$base" --timeout 600 2>&1) || { printf '%s\n' "$out" >&2; echo "golden/smoke.sh: the run failed (no crash allowed)" >&2; exit 1; }
printf '%s\n' "$out"
run=$(printf '%s\n' "$out" | sed -n 's/^\(runs\/next-[^:]*\):.*/\1/p' | tail -n1)
[ -n "$run" ] && [ -f "$run/predictions.csv" ] || { echo "golden/smoke.sh: cannot find the run directory in the output" >&2; exit 2; }
line=$("$uv" run --quiet decimer-golden score "$run/predictions.csv" | head -n1)
printf '%s\n' "$line"
correct=$(printf '%s' "$line" | sed -n 's/^correct \([0-9]*\)\/\([0-9]*\).*/\1/p')
total=$(printf '%s' "$line" | sed -n 's/^correct \([0-9]*\)\/\([0-9]*\).*/\2/p')
invalid=$(printf '%s' "$line" | sed -n 's/.* invalid \([0-9]*\) .*/\1/p')
# The score line says "incomplete N" only for a run that has cut-off predictions; none is 0.
incomplete=$(printf '%s' "$line" | sed -n 's/.* incomplete \([0-9]*\) .*/\1/p')
incomplete=${incomplete:-0}
[ -n "$correct" ] && [ -n "$invalid" ] || { echo "golden/smoke.sh: cannot read the score line" >&2; exit 2; }
if [ "$correct" -ge "$min_correct" ] && [ "$invalid" -le "$max_invalid" ] && [ "$incomplete" -le "$max_incomplete" ]; then
    echo "golden smoke PASS: $correct/$total correct (need $min_correct), $invalid invalid (at most $max_invalid), $incomplete incomplete (at most $max_incomplete), $run"
else
    echo "golden smoke FAIL: $correct/$total correct (need $min_correct), $invalid invalid (at most $max_invalid), $incomplete incomplete (at most $max_incomplete), $run" >&2
    exit 1
fi
