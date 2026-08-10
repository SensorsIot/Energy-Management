#!/usr/bin/env bash
# Run every add-on's test suite, each in its own pytest process.
#
# The suites cannot share one pytest session: every add-on ships a top-level
# `run.py` and `src/` package, so the first `src` imported wins in sys.modules
# and the rest fail to collect. Add-ons are separate containers at runtime and
# are tested the same way. The repo-root conftest.py rejects a mixed session
# with an explanation rather than letting it fail obscurely.
#
# Usage:
#   tools/run_tests.sh                # every suite
#   tools/run_tests.sh -q             # extra args are passed to pytest
#   tools/run_tests.sh -k Throttle
set -uo pipefail

cd "$(dirname "$0")/.."

# name:path — path is what pytest is pointed at.
SUITES=(
  "energy-manager:energy-manager/tests"
  "load-forecast:load-forecast/tests"
  "ocpp-server:ocpp-server/tests"
  "swiss-solar-forecast:swiss-solar-forecast"
)

failed=()
skipped=()

for entry in "${SUITES[@]}"; do
  name="${entry%%:*}"
  path="${entry#*:}"

  if [ ! -e "$path" ]; then
    echo "── ${name}: no suite at ${path}, skipping"
    skipped+=("$name")
    continue
  fi

  echo
  echo "──────────────────────────────────────────────────────────"
  echo "  ${name}  (${path})"
  echo "──────────────────────────────────────────────────────────"
  if ! python -m pytest "$path" "$@"; then
    failed+=("$name")
  fi
done

echo
echo "──────────────────────────────────────────────────────────"
if [ ${#skipped[@]} -gt 0 ]; then
  echo "  skipped: ${skipped[*]}"
fi
if [ ${#failed[@]} -gt 0 ]; then
  echo "  FAILED: ${failed[*]}"
  echo "──────────────────────────────────────────────────────────"
  exit 1
fi
echo "  all suites passed"
echo "──────────────────────────────────────────────────────────"
