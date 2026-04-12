-- test_cmd.lua: Inject commands and check replies
-- Usage: orchestrator --lua test/lua/test_cmd.lua test/t01_hot_start_neighbors.json

sim:initialize()

-- Step past hot-start and warmup
sim:step(6000)

-- Query neighbors on relay1
local result = sim:cmd("relay1", "neighbors")
assert(result.node == "relay1", "Expected node='relay1'")
assert(result.reply ~= nil, "Expected non-nil reply")
log("relay1 neighbors: " .. result.reply)

-- Query node status
local status = sim:node_status("relay1")
assert(status.node == "relay1", "Expected node='relay1' in status")
assert(status.role == "repeater", "Expected role='repeater'")
log("relay1 neighbor_count: " .. tostring(status.neighbor_count))

-- Check events are returned
local events = sim:events(5)
assert(type(events) == "table", "Expected events table")
log("Last 5 events: " .. #events .. " returned")

sim:finalize()
log("PASS: test_cmd")
