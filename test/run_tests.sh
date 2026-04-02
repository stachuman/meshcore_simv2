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

shopt -s nullglob
tests=("$SCRIPT_DIR"/t*.json)
shopt -u nullglob

if [ ${#tests[@]} -eq 0 ]; then
    echo "ERROR: no test files found in $SCRIPT_DIR"
    exit 1
fi

for t in "${tests[@]}"; do
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
    printf "\nFailure details:%b\n" "${errors}"
    exit 1
fi
