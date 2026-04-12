-- simlib.lua: Convenience helpers for orchestrator Lua scripting
-- Preloaded automatically when Lua engine is active.
-- NOTE: This is a reference copy. The authoritative version is embedded in
-- orchestrator/LuaEngine.cpp (SIMLIB_SOURCE). Keep both in sync.

--- Return array of nodes filtered by role ("repeater" or "companion")
function sim:nodes_by_role(role)
    local result = {}
    for _, n in ipairs(self:nodes()) do
        if n.role == role then
            result[#result + 1] = n
        end
    end
    return result
end

--- Return array of repeater nodes
function sim:repeaters()
    return self:nodes_by_role("repeater")
end

--- Return array of companion nodes
function sim:companions()
    return self:nodes_by_role("companion")
end

--- Get neighbor count for a repeater (returns number)
function sim:neighbor_count(name)
    local s = self:node_status(name)
    return s.neighbor_count or 0
end

--- Get contact count for a companion (returns number)
function sim:contact_count(name)
    local s = self:node_status(name)
    return s.contact_count or 0
end

--- Iterate over all repeaters calling fn(name, status)
function sim:for_each_repeater(fn)
    for _, n in ipairs(self:repeaters()) do
        local s = self:node_status(n.name)
        fn(n.name, s)
    end
end

--- Iterate over all companions calling fn(name, status)
function sim:for_each_companion(fn)
    for _, n in ipairs(self:companions()) do
        local s = self:node_status(n.name)
        fn(n.name, s)
    end
end

--- Iterate over all nodes calling fn(name, status)
function sim:for_each_node(fn)
    for _, n in ipairs(self:nodes()) do
        local s = self:node_status(n.name)
        fn(n.name, s)
    end
end

--- Run simulation to completion, stepping by chunk_ms (default 1000)
function sim:run_all(chunk_ms)
    chunk_ms = chunk_ms or 1000
    while not self:finished() do
        self:step(chunk_ms)
    end
end

--- Assert helper: fail with message if condition is false
function sim_assert(cond, msg)
    if not cond then
        error("ASSERT FAILED: " .. (msg or "unknown"), 2)
    end
end
