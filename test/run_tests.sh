#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Allow selecting a specific build dir via $BUILD_DIR or --build-dir=...
# Default is ../build (the standard dev build). Handy overrides:
#   BUILD_DIR=build-asan bash test/run_tests.sh
#   bash test/run_tests.sh --build-dir=build-release
BUILD_DIR="${BUILD_DIR:-build}"
for arg in "$@"; do
    case "$arg" in
        --build-dir=*) BUILD_DIR="${arg#--build-dir=}" ;;
    esac
done
ORCH="${SCRIPT_DIR}/../${BUILD_DIR}/orchestrator/orchestrator"

# Color output when stdout is a TTY
if [ -t 1 ]; then
    C_PASS='\033[32m'; C_FAIL='\033[31m'; C_SKIP='\033[33m'; C_RESET='\033[0m'
else
    C_PASS=''; C_FAIL=''; C_SKIP=''; C_RESET=''
fi

if [ ! -x "$ORCH" ]; then
    echo "ERROR: orchestrator not found at $ORCH — run 'cmake --build $BUILD_DIR' first"
    exit 1
fi

# Apply sanitizer suppressions when running against an ASan build. The files
# filter out third-party (ed25519) UB patterns and firmware-lifetime "leaks"
# from StaticPoolPacketManager — both are benign for our simulator's purpose.
if [[ "$BUILD_DIR" == *asan* ]]; then
    export UBSAN_OPTIONS="suppressions=${SCRIPT_DIR}/ubsan_suppressions.txt:print_stacktrace=1"
    export LSAN_OPTIONS="suppressions=${SCRIPT_DIR}/lsan_suppressions.txt"
    echo "ASan mode: using suppressions from ${SCRIPT_DIR}/{ubsan,lsan}_suppressions.txt"
fi

pass=0
fail=0
skip=0
skipped_tests=""
errors=""

# Detect available firmware plugins next to the orchestrator binary.
# Build a bash array of exact plugin names (no space-separated string matching).
ORCH_DIR="$(dirname "$ORCH")"
declare -a available_plugins=()
for so in "$ORCH_DIR"/fw_*.so; do
    [ -f "$so" ] && available_plugins+=("$(basename "$so" .so)")
done

# Exact-match lookup: returns 0 if $1 is present in available_plugins array.
have_plugin() {
    local needle="$1"
    local p
    for p in "${available_plugins[@]}"; do
        [ "$p" = "$needle" ] && return 0
    done
    return 1
}

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

    # Read _requires_plugins via python with argv (avoids shell-escaping of the path).
    required=$(python3 -c '
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
for p in d.get("_requires_plugins", []):
    print(p)
' "$t" 2>/dev/null || true)

    skip_test=false
    missing_plugin=""
    for plugin in $required; do
        if ! have_plugin "$plugin"; then
            skip_test=true
            missing_plugin="$plugin"
            break
        fi
    done
    if $skip_test; then
        printf "%bSKIP%b (missing plugin: %s)\n" "$C_SKIP" "$C_RESET" "$missing_plugin"
        skipped_tests="${skipped_tests}  - ${name}: missing plugin ${missing_plugin}"$'\n'
        skip=$((skip + 1))
        continue
    fi

    if stderr=$("$ORCH" "$t" 2>&1 >/dev/null); then
        printf "%bPASS%b\n" "$C_PASS" "$C_RESET"
        pass=$((pass + 1))
    else
        printf "%bFAIL%b\n" "$C_FAIL" "$C_RESET"
        errors="${errors}"$'\n'"--- ${name} ---"$'\n'"${stderr}"
        fail=$((fail + 1))
    fi
done

total=$((pass + fail))
echo ""
if [ "$skip" -gt 0 ]; then
    echo "${pass}/${total} passed (${skip} skipped)"
    printf "%bSkipped tests:%b\n%s" "$C_SKIP" "$C_RESET" "$skipped_tests"
else
    echo "${pass}/${total} passed"
fi

if [ "$fail" -gt 0 ]; then
    printf "\nFailure details:\n%s\n" "$errors"
    exit 1
fi
