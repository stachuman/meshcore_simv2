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
skip=0
errors=""

# Detect available firmware plugins next to the orchestrator binary
ORCH_DIR="$(dirname "$ORCH")"
available_plugins=""
for so in "$ORCH_DIR"/fw_*.so; do
    [ -f "$so" ] && available_plugins="${available_plugins} $(basename "$so" .so)"
done

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

    # Check _requires_plugins — skip if any required plugin is not built
    required=$(python3 -c "
import json, sys
with open('$t') as f: d = json.load(f)
for p in d.get('_requires_plugins', []):
    print(p)
" 2>/dev/null || true)
    skip_test=false
    for plugin in $required; do
        if ! echo "$available_plugins" | grep -qw "$plugin"; then
            skip_test=true
            break
        fi
    done
    if $skip_test; then
        echo "SKIP (requires: $required)"
        skip=$((skip + 1))
        continue
    fi

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
if [ "$skip" -gt 0 ]; then
    echo "${pass}/${total} passed ($skip skipped)"
else
    echo "${pass}/${total} passed"
fi

if [ "$fail" -gt 0 ]; then
    printf "\nFailure details:%b\n" "${errors}"
    exit 1
fi
