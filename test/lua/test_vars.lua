-- test_vars.lua: Check CLI variables are accessible
-- Usage: orchestrator --lua test/lua/test_vars.lua --lua-var delay=500 --lua-var mode=test test/t01_hot_start_neighbors.json

assert(vars.delay == 500, "Expected vars.delay=500, got " .. tostring(vars.delay))
assert(vars.mode == "test", "Expected vars.mode='test', got " .. tostring(vars.mode))
log("vars.delay = " .. vars.delay)
log("vars.mode = " .. vars.mode)

sim:initialize()
sim:step(1000)
sim:finalize()
log("PASS: test_vars")
