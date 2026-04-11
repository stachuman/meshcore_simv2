#!/usr/bin/env bash
set -euo pipefail

# Interactive mode integration tests
# Run from repo root: bash test/test_interactive.sh

ORCH="./build/orchestrator/orchestrator"

if [[ ! -x "$ORCH" ]]; then
    echo "ERROR: orchestrator not found at $ORCH. Run cmake --build build first."
    exit 1
fi

PASS=0
FAIL=0

run_test() {
    local name="$1"
    local config="$2"
    local commands="$3"
    local expected_pattern="$4"

    output=$(echo -e "$commands" | "$ORCH" -i "$config" 2>/dev/null) || true
    if echo "$output" | grep -qE "$expected_pattern"; then
        echo "  PASS: $name"
        PASS=$((PASS+1))
    else
        echo "  FAIL: $name"
        echo "    Expected pattern: $expected_pattern"
        echo "    Output (first 500 chars):"
        echo "$output" | head -c 500
        echo
        FAIL=$((FAIL+1))
    fi
}

# Like run_test but checks that pattern does NOT appear
run_test_not() {
    local name="$1"
    local config="$2"
    local commands="$3"
    local forbidden_pattern="$4"

    output=$(echo -e "$commands" | "$ORCH" -i "$config" 2>/dev/null) || true
    if echo "$output" | grep -qE "$forbidden_pattern"; then
        echo "  FAIL: $name"
        echo "    Forbidden pattern found: $forbidden_pattern"
        echo "    Output (first 500 chars):"
        echo "$output" | head -c 500
        echo
        FAIL=$((FAIL+1))
    else
        echo "  PASS: $name"
        PASS=$((PASS+1))
    fi
}

echo "=== Interactive Mode Tests ==="
echo
echo "--- REPL basics ---"

run_test "time command" \
    "test/t01_hot_start_neighbors.json" \
    "time\nquit" \
    '"time_ms":0'

run_test "nodes command" \
    "test/t01_hot_start_neighbors.json" \
    "nodes\nquit" \
    '"name":"alice"'

run_test "step advances time" \
    "test/t01_hot_start_neighbors.json" \
    "step 5000\ntime\nquit" \
    '"time_ms":5000'

run_test "multiple steps accumulate" \
    "test/t01_hot_start_neighbors.json" \
    "step 3000\nstep 2000\ntime\nquit" \
    '"time_ms":5000'

run_test "default step is 1000ms" \
    "test/t01_hot_start_neighbors.json" \
    "step\ntime\nquit" \
    '"time_ms":1000'

run_test "events returns JSON array" \
    "test/t01_hot_start_neighbors.json" \
    "step 1000\nevents 5\nquit" \
    '> \['

run_test "summary shows node_count" \
    "test/t01_hot_start_neighbors.json" \
    "summary\nquit" \
    '"node_count":3'

run_test "help lists repeater commands" \
    "test/t01_hot_start_neighbors.json" \
    "help\nquit" \
    'Repeater CLI'

run_test "help lists companion commands" \
    "test/t01_hot_start_neighbors.json" \
    "help\nquit" \
    'Companion CLI'

run_test "unknown command error" \
    "test/t01_hot_start_neighbors.json" \
    "bogus\nquit" \
    'ERROR: unknown command'

run_test "status unknown node" \
    "test/t01_hot_start_neighbors.json" \
    "status NOEXIST\nquit" \
    '"error"'

run_test "cmd unknown node" \
    "test/t01_hot_start_neighbors.json" \
    "cmd NOEXIST neighbors\nquit" \
    '"error"'

run_test "step clamps to duration" \
    "test/t01_hot_start_neighbors.json" \
    "step 999999\ntime\nquit" \
    '"finished":true'

echo
echo "--- Repeater neighbor discovery (t18: 4-repeater chain, hot-start) ---"

run_test "r1 sees r2 neighbor" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\nstatus r1\nquit" \
    '"name":"r2"'

run_test "r2 sees 2 neighbors" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\nstatus r2\nquit" \
    '"neighbor_count":2'

run_test "r4 sees r3 neighbor" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\nstatus r4\nquit" \
    '"name":"r3"'

run_test "cmd neighbors raw reply" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r2 neighbors\nquit" \
    '"reply":"[A-F0-9]'

echo
echo "--- Repeater CLI commands (t18: 4-repeater chain, hot-start) ---"

run_test "repeater: clock" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 clock\nquit" \
    'UTC'

run_test "repeater: ver" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 ver\nquit" \
    'Build:'

run_test "repeater: stats-radio" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 stats-radio\nquit" \
    'noise_floor.*last_rssi.*last_snr'

run_test "repeater: stats-packets" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 stats-packets\nquit" \
    'recv.*sent.*flood_tx'

run_test "repeater: stats-core" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 stats-core\nquit" \
    'battery_mv.*uptime_secs'

run_test "repeater: clear stats" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 clear stats\nquit" \
    'OK.*stats reset'

run_test "repeater: get txdelay" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 get txdelay\nquit" \
    '> [0-9]'

run_test "repeater: get rxdelay" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 get rxdelay\nquit" \
    '> [0-9]'

run_test "repeater: get af" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 get af\nquit" \
    '> [0-9]'

run_test "repeater: get name" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 get name\nquit" \
    '> r1'

run_test "repeater: get radio" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 get radio\nquit" \
    '> [0-9].*,[0-9]'

run_test "repeater: get flood.max" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 get flood.max\nquit" \
    '> [0-9]'

run_test "repeater: get public.key" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 get public.key\nquit" \
    '> [0-9a-fA-F]{16}'

run_test "repeater: set txdelay" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 set txdelay 0.5\ncmd r1 get txdelay\nquit" \
    '> 0.5'

run_test "repeater: advert" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 advert\nquit" \
    'Advert sent'

run_test "repeater: neighbor.remove" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 neighbor.remove 00000000\nquit" \
    '"reply":"OK"'

echo
echo "--- Companion contacts (t02: alice-relay1-bob, hot-start) ---"

run_test "companion has contacts" \
    "test/t02_hot_start_msg.json" \
    "step 6000\nstatus alice\nquit" \
    '"contact_count":[1-9]'

run_test "alice knows bob" \
    "test/t02_hot_start_msg.json" \
    "step 6000\nstatus alice\nquit" \
    '"name":"bob"'

run_test "bob knows alice" \
    "test/t02_hot_start_msg.json" \
    "step 6000\nstatus bob\nquit" \
    '"name":"alice"'

echo
echo "--- Companion CLI commands (t02: alice-relay1-bob, hot-start) ---"

run_test "companion: ver" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice ver\nquit" \
    'sim-companion'

run_test "companion: clock" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice clock\nquit" \
    'UTC.*epoch'

run_test "companion: list" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice list\nquit" \
    'bob'

run_test "companion: neighbors" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice neighbors\nquit" \
    'contacts.*bob'

run_test "companion: stats (initial)" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice stats\nquit" \
    'sent.*flood.*direct.*group.*recv'

run_test "companion: msg (flood)" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice msg bob hello\nquit" \
    'msg sent to bob'

run_test "companion: msga (ack tracked)" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice msga bob hello\nquit" \
    'msg sent to bob.*ack tracked'

run_test "companion: stats after msg" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice msg bob hi\ncmd alice stats\nquit" \
    'sent: 1 flood'

run_test "companion: path (flood)" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice path bob\nquit" \
    'flood'

run_test "companion: reset_path" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice reset_path bob\nquit" \
    'path reset for bob'

run_test "companion: reset path (space variant)" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice reset path bob\nquit" \
    'path reset for bob'

run_test "companion: disc_path" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice disc_path bob\nquit" \
    'path discovery sent to bob'

run_test "companion: advert" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice advert\nquit" \
    'advert sent.*flood'

run_test "companion: advert.zerohop" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice advert.zerohop\nquit" \
    'advert sent.*zero-hop'

run_test "companion: path unknown contact" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice path NOEXIST\nquit" \
    'ERROR.*contact not found'

run_test "companion: unknown command" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice boguscmd\nquit" \
    'ERROR.*unknown command'

echo
echo "--- Message injection generates events (t02) ---"

run_test "msg generates tx events" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice msg bob hello interactive\nstep 5000\nevents 50\nquit" \
    '"type":"tx"'

run_test "msg generates rx events" \
    "test/t02_hot_start_msg.json" \
    "step 6000\ncmd alice msg bob hello interactive\nstep 5000\nevents 50\nquit" \
    '"type":"rx"'

echo
echo "--- Command without advancing clock ---"

run_test "cmd does not advance clock" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 neighbors\ntime\nquit" \
    '"time_ms":10000'

run_test "multiple cmds same time" \
    "test/t18_repeater_neighbors_hot.json" \
    "step 10000\ncmd r1 neighbors\ncmd r2 neighbors\ncmd r3 neighbors\ntime\nquit" \
    '"time_ms":10000'

echo
echo "--- Isolated node (t04: no links) ---"

run_test "isolated node no contacts" \
    "test/t04_no_link_isolated.json" \
    "step 10000\nstatus alice\nquit" \
    '"contact_count":0'

echo
echo "--- Direct companion link (t05: alice-bob direct) ---"

run_test "direct companion contacts" \
    "test/t05_direct_link_no_relay.json" \
    "step 6000\nstatus alice\nquit" \
    '"contact_count":[1-9]'

echo
echo "--- next (run to next scheduled command) ---"

run_test "next finds scheduled cmd" \
    "test/t02_hot_start_msg.json" \
    "next\ntime\nquit" \
    '"time_ms":6[0-9][0-9][0-9]'

echo
echo "--- Events filtering ---"

run_test "events 0 returns empty" \
    "test/t01_hot_start_neighbors.json" \
    "events 0\nquit" \
    '> \[\]'

run_test "events captures init events" \
    "test/t01_hot_start_neighbors.json" \
    "events 5\nquit" \
    'node_ready'

echo
echo "--- Batch mode regression ---"

if "$ORCH" test/t01_hot_start_neighbors.json >/dev/null 2>&1; then
    echo "  PASS: batch mode t01"
    PASS=$((PASS+1))
else
    echo "  FAIL: batch mode t01"
    FAIL=$((FAIL+1))
fi

if "$ORCH" test/t18_repeater_neighbors_hot.json >/dev/null 2>&1; then
    echo "  PASS: batch mode t18"
    PASS=$((PASS+1))
else
    echo "  FAIL: batch mode t18"
    FAIL=$((FAIL+1))
fi

echo
echo "=== Results: $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]]
