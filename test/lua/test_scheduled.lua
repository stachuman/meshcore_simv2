-- test_scheduled.lua: Verify config-scheduled Lua function calls
-- Usage: orchestrator --lua test/lua/test_scheduled.lua test/lua/test_scheduled.json

local call_log = {}

function on_early()
    local t = sim:time()
    log("on_early called at " .. t .. "ms")
    table.insert(call_log, {name = "on_early", time = t})
end

function on_midpoint()
    local t = sim:time()
    log("on_midpoint called at " .. t .. "ms")
    table.insert(call_log, {name = "on_midpoint", time = t})
    -- Inject a command at midpoint
    local result = sim:cmd("relay1", "neighbors")
    log("relay1 neighbors at midpoint: " .. result.reply)
end

function on_late()
    local t = sim:time()
    log("on_late called at " .. t .. "ms")
    table.insert(call_log, {name = "on_late", time = t})
end

sim:initialize()

-- Run the entire simulation (scheduled lua calls fire via orchestrator)
while not sim:finished() do
    sim:step(1000)
end

-- Verify all callbacks were called
assert(#call_log == 3, "Expected 3 callbacks, got " .. #call_log)
assert(call_log[1].name == "on_early", "First callback should be on_early")
assert(call_log[2].name == "on_midpoint", "Second callback should be on_midpoint")
assert(call_log[3].name == "on_late", "Third callback should be on_late")

-- Verify timing (callbacks fire at or after their scheduled time)
assert(call_log[1].time >= 6000, "on_early should fire at >= 6000ms")
assert(call_log[2].time >= 10000, "on_midpoint should fire at >= 10000ms")
assert(call_log[3].time >= 14000, "on_late should fire at >= 14000ms")

local ok = sim:finalize()
if not ok then os.exit(1) end
log("PASS: test_scheduled")
