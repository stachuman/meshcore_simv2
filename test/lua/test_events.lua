-- test_events.lua: Register event callbacks and verify they fire
-- Usage: orchestrator --lua test/lua/test_events.lua test/t02_hot_start_msg.json

local tx_count = 0
local rx_count = 0
local all_count = 0

sim:on('tx', function(evt)
    tx_count = tx_count + 1
end)

sim:on('rx', function(evt)
    rx_count = rx_count + 1
end)

sim:on('*', function(evt)
    all_count = all_count + 1
end)

sim:initialize()

-- Run the full simulation
while not sim:finished() do
    sim:step(1000)
end

log("TX callbacks fired: " .. tx_count)
log("RX callbacks fired: " .. rx_count)
log("All callbacks fired: " .. all_count)

assert(tx_count > 0, "Expected at least one TX event callback")
assert(rx_count > 0, "Expected at least one RX event callback")
assert(all_count >= tx_count + rx_count, "Wildcard should see at least TX+RX events")

local ok = sim:finalize()
if not ok then os.exit(1) end
log("PASS: test_events")
