-- test_basic.lua: Initialize, step, query nodes, finalize
-- Usage: orchestrator --lua test/lua/test_basic.lua test/t01_hot_start_neighbors.json

sim:initialize()

-- Check nodes are present
local nodes = sim:nodes()
assert(#nodes >= 2, "Expected at least 2 nodes, got " .. #nodes)
log("Found " .. #nodes .. " nodes")

-- Step forward
local result = sim:step(5000)
assert(result.end_ms > 0, "Expected positive end time")
log("Stepped to " .. result.end_ms .. "ms")

-- Check time
local t = sim:time()
assert(t > 0, "Expected positive time")

-- Query summary
local s = sim:summary()
assert(s.node_count == #nodes, "Node count mismatch")
log("Summary: " .. s.node_count .. " nodes, duration=" .. s.duration_ms .. "ms")

-- Step to finish
while not sim:finished() do
    sim:step(1000)
end

local ok = sim:finalize()
log("Finalize result: " .. tostring(ok))
if not ok then os.exit(1) end
log("PASS: test_basic")
