#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ORCH="${SCRIPT_DIR}/../build/orchestrator/orchestrator"

if [ ! -x "$ORCH" ]; then
    echo "ERROR: orchestrator not found at $ORCH — run cmake --build build first"
    exit 1
fi

pass=0
fail=0
errors=""

for t in "$SCRIPT_DIR"/t*.json; do
    name=$(basename "$t" .json)
    printf "  %-40s " "$name"
    if stderr=$("$ORCH" "$t" 2>&1 >/dev/null); then
        echo "PASS"
        pass=$((pass + 1))
    else
        echo "FAIL"
        errors="${errors}\n--- ${name} ---\n${stderr}"
        fail=$((fail + 1))
    fi
done

total=$((pass + fail))
echo ""
echo "${pass}/${total} passed"

if [ "$fail" -gt 0 ]; then
    echo -e "\nFailure details:${errors}"
    exit 1
fi
