-- helpers.lua: Minimal helper for interactive mode testing
-- Just defines some utility functions

function check_time()
    local t = sim:time()
    log("Current time: " .. t .. "ms")
    return t
end
