#ifdef ENABLE_LUA

// Include STL and sol2 BEFORE anything that pulls in Arduino.h min/max macros
#include <string>
#include <vector>
#include <fstream>
#include <sstream>
#include <iostream>

#include "third_party/json.hpp"
#include "LuaEngine.h"
#include "SimController.h"

#undef min
#undef max

using json = nlohmann::json;

// ---------------------------------------------------------------------------
// Constructor
// ---------------------------------------------------------------------------

LuaEngine::LuaEngine(SimController& ctrl)
    : _ctrl(ctrl)
{
    // Note: os library exposes os.execute() which allows arbitrary shell commands.
    // Acceptable for local CLI use. Must be sandboxed if Lua scripts are ever
    // accepted from untrusted sources (e.g. webapp user uploads).
    _lua.open_libraries(
        sol::lib::base,
        sol::lib::string,
        sol::lib::table,
        sol::lib::math,
        sol::lib::os
    );
    registerBindings();
}

// ---------------------------------------------------------------------------
// JSON string → Lua table conversion
// ---------------------------------------------------------------------------

static sol::object jsonValueToLua(sol::state& lua, const json& j) {
    switch (j.type()) {
        case json::value_t::null:
            return sol::nil;
        case json::value_t::boolean:
            return sol::make_object(lua, j.get<bool>());
        case json::value_t::number_integer:
            return sol::make_object(lua, j.get<int64_t>());
        case json::value_t::number_unsigned:
            return sol::make_object(lua, j.get<uint64_t>());
        case json::value_t::number_float:
            return sol::make_object(lua, j.get<double>());
        case json::value_t::string:
            return sol::make_object(lua, j.get<std::string>());
        case json::value_t::array: {
            sol::table t = lua.create_table();
            for (size_t i = 0; i < j.size(); i++) {
                t[static_cast<int>(i + 1)] = jsonValueToLua(lua, j[i]);  // 1-indexed
            }
            return t;
        }
        case json::value_t::object: {
            sol::table t = lua.create_table();
            for (auto& [key, val] : j.items()) {
                t[key] = jsonValueToLua(lua, val);
            }
            return t;
        }
        default:
            return sol::nil;
    }
}

sol::object LuaEngine::jsonToLua(const std::string& json_str) {
    try {
        json j = json::parse(json_str);
        return jsonValueToLua(_lua, j);
    } catch (...) {
        return sol::nil;
    }
}

// ---------------------------------------------------------------------------
// Bindings: expose SimController API as `sim` global
// ---------------------------------------------------------------------------

void LuaEngine::registerBindings() {
    // Create the `sim` table bound to SimController.
    // All methods accept an ignored first `sol::table` arg so that
    // both `sim:method()` (colon syntax) and `sim.method()` work.
    sol::table sim = _lua.create_named_table("sim");

    sim.set_function("initialize", [this](sol::table) {
        _ctrl.initialize();
    });

    sim.set_function("finalize", [this](sol::table) -> bool {
        return _ctrl.finalize();
    });

    sim.set_function("step", [this](sol::table, double delta_ms_d) -> sol::table {
        if (delta_ms_d < 0.0) delta_ms_d = 0.0;
        unsigned long delta_ms = static_cast<unsigned long>(delta_ms_d);
        auto result = _ctrl.step(delta_ms);
        collectAndDispatchEvents();
        sol::table t = _lua.create_table();
        t["start_ms"] = result.start_ms;
        t["end_ms"] = result.end_ms;
        t["events"] = result.events_generated;
        t["finished"] = result.finished;
        return t;
    });

    sim.set_function("run_to_next", [this](sol::table) -> sol::table {
        auto result = _ctrl.runToNextCommand();
        collectAndDispatchEvents();
        sol::table t = _lua.create_table();
        t["start_ms"] = result.start_ms;
        t["end_ms"] = result.end_ms;
        t["events"] = result.events_generated;
        t["finished"] = result.finished;
        return t;
    });

    sim.set_function("time", [this](sol::table) -> unsigned long {
        return _ctrl.currentTimeMs();
    });

    sim.set_function("finished", [this](sol::table) -> bool {
        return _ctrl.isFinished();
    });

    sim.set_function("step_ms", [this](sol::table) -> int {
        return _ctrl.stepMs();
    });

    sim.set_function("nodes", [this](sol::table) -> sol::object {
        json j = _ctrl.queryNodes();
        return jsonValueToLua(_lua, j);
    });

    sim.set_function("node_status", [this](sol::table, const std::string& name) -> sol::object {
        json j = _ctrl.queryNodeStatus(name);
        return jsonValueToLua(_lua, j);
    });

    sim.set_function("events", [this](sol::table, sol::optional<int> n) -> sol::object {
        json j = _ctrl.queryEvents(n.value_or(10));
        return jsonValueToLua(_lua, j);
    });

    sim.set_function("summary", [this](sol::table) -> sol::object {
        json j = _ctrl.querySummary();
        return jsonValueToLua(_lua, j);
    });

    sim.set_function("cmd", [this](sol::table, const std::string& node, const std::string& command) -> sol::object {
        json j = _ctrl.injectCommand(node, command);
        return jsonValueToLua(_lua, j);
    });

    sim.set_function("msg_stats", [this](sol::table, const std::string& name) -> sol::object {
        json j = _ctrl.queryMessageStats(name);
        return jsonValueToLua(_lua, j);
    });

    sim.set_function("on", [this](sol::table, const std::string& type, sol::protected_function fn) {
        registerEventCallback(type, fn);
    });

    // `vars` table for CLI variables (populated via setVar)
    _lua.create_named_table("vars");

    // `log` function → stderr
    _lua.set_function("log", [](const std::string& msg) {
        fprintf(stderr, "[lua] %s\n", msg.c_str());
    });

    // Load built-in convenience helpers (simlib)
    loadSimlib();
}

// ---------------------------------------------------------------------------
// Built-in convenience helpers (embedded simlib)
// ---------------------------------------------------------------------------

// Embedded simlib — keep in sync with orchestrator/lua/simlib.lua
static const char* SIMLIB_SOURCE = R"lua(
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
)lua";

void LuaEngine::loadSimlib() {
    auto result = _lua.safe_script(SIMLIB_SOURCE, sol::script_pass_on_error);
    if (!result.valid()) {
        sol::error err = result;
        fprintf(stderr, "[lua] Warning: simlib load error: %s\n", err.what());
    }
}

// ---------------------------------------------------------------------------
// Script loading, eval, variable setting
// ---------------------------------------------------------------------------

void LuaEngine::loadScript(const std::string& path) {
    auto result = _lua.safe_script_file(path, sol::script_pass_on_error);
    if (!result.valid()) {
        sol::error err = result;
        throw std::runtime_error(std::string("Lua script error: ") + err.what());
    }
}

static json luaToJson(const sol::object& obj) {
    switch (obj.get_type()) {
        case sol::type::nil:
        case sol::type::none:
            return nullptr;
        case sol::type::boolean:
            return obj.as<bool>();
        case sol::type::number:
            {
                double d = obj.as<double>();
                if (d == static_cast<double>(static_cast<long long>(d)))
                    return static_cast<long long>(d);
                return d;
            }
        case sol::type::string:
            return obj.as<std::string>();
        case sol::type::table:
            {
                sol::table t = obj.as<sol::table>();
                // Detect array vs object: check if sequential integer keys from 1
                bool is_array = true;
                int max_key = 0;
                t.for_each([&](const sol::object& key, const sol::object&) {
                    if (key.get_type() == sol::type::number) {
                        double k = key.as<double>();
                        if (k == static_cast<double>(static_cast<int>(k)) && k >= 1) {
                            max_key = std::max(max_key, static_cast<int>(k));
                            return;
                        }
                    }
                    is_array = false;
                });
                if (is_array && max_key > 0 && max_key == static_cast<int>(t.size())) {
                    json arr = json::array();
                    for (int i = 1; i <= max_key; i++)
                        arr.push_back(luaToJson(t[i]));
                    return arr;
                }
                json obj_out = json::object();
                t.for_each([&](const sol::object& key, const sol::object& val) {
                    std::string k;
                    if (key.get_type() == sol::type::string)
                        k = key.as<std::string>();
                    else if (key.get_type() == sol::type::number)
                        k = std::to_string(static_cast<long long>(key.as<double>()));
                    else
                        return;
                    obj_out[k] = luaToJson(val);
                });
                return obj_out;
            }
        default:
            return "(opaque)";
    }
}

std::string LuaEngine::eval(const std::string& code) {
    auto result = _lua.safe_script(code, sol::script_pass_on_error);
    if (!result.valid()) {
        sol::error err = result;
        return std::string("ERROR: ") + err.what();
    }
    sol::object obj = result;
    if (obj.get_type() == sol::type::nil || obj.get_type() == sol::type::none) {
        return "";
    }
    json j = luaToJson(obj);
    return j.dump();
}

void LuaEngine::setVar(const std::string& key, const std::string& value) {
    sol::table vars = _lua["vars"];
    // Try to interpret as number, fall back to string
    try {
        size_t pos;
        double d = std::stod(value, &pos);
        if (pos == value.size()) {
            vars[key] = d;
            return;
        }
    } catch (...) {}
    vars[key] = value;
}

// ---------------------------------------------------------------------------
// Lua function calls (for config-scheduled {"lua": "fn_name"})
// ---------------------------------------------------------------------------

bool LuaEngine::callFunction(const std::string& name) {
    sol::protected_function fn = _lua[name];
    if (!fn.valid()) {
        fprintf(stderr, "[lua] Warning: function '%s' not found\n", name.c_str());
        return false;
    }
    auto result = fn();
    if (!result.valid()) {
        sol::error err = result;
        fprintf(stderr, "[lua] Error in %s(): %s\n", name.c_str(), err.what());
        return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// Event callbacks: sim:on('type', fn) and dispatch
// ---------------------------------------------------------------------------

void LuaEngine::registerEventCallback(const std::string& type, sol::protected_function fn) {
    _callbacks[type].push_back(fn);
}

void LuaEngine::collectAndDispatchEvents() {
    auto events = _ctrl.drainNewEvents();
    if (events.empty()) return;
    if (_callbacks.empty()) return;

    for (const auto& line : events) {
        // Parse the NDJSON line to extract event type
        std::string clean = line;
        while (!clean.empty() && (clean.back() == '\n' || clean.back() == '\r'))
            clean.pop_back();
        if (clean.empty()) continue;

        json j;
        try {
            j = json::parse(clean);
        } catch (...) {
            continue;
        }

        std::string type = j.value("type", "");
        if (type.empty()) continue;

        // Convert to Lua table once
        sol::object lua_event = jsonValueToLua(_lua, j);

        // Dispatch to type-specific callbacks
        auto it = _callbacks.find(type);
        if (it != _callbacks.end()) {
            for (auto& fn : it->second) {
                auto result = fn(lua_event);
                if (!result.valid()) {
                    sol::error err = result;
                    fprintf(stderr, "[lua] Error in '%s' callback: %s\n",
                            type.c_str(), err.what());
                }
            }
        }

        // Dispatch to wildcard callbacks
        auto wildcard = _callbacks.find("*");
        if (wildcard != _callbacks.end()) {
            for (auto& fn : wildcard->second) {
                auto result = fn(lua_event);
                if (!result.valid()) {
                    sol::error err = result;
                    fprintf(stderr, "[lua] Error in '*' callback: %s\n", err.what());
                }
            }
        }
    }
}

#endif // ENABLE_LUA
