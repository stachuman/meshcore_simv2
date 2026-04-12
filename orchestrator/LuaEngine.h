#pragma once

#ifdef ENABLE_LUA

#include <string>
#include <vector>
#include <unordered_map>

// Sol2 + Lua headers BEFORE any Arduino.h chain
#include "third_party/sol/sol.hpp"

class SimController;

class LuaEngine {
    SimController& _ctrl;
    sol::state _lua;
    std::unordered_map<std::string, std::vector<sol::protected_function>> _callbacks;

    void registerBindings();
    void loadSimlib();
    sol::object jsonToLua(const std::string& json_str);

public:
    explicit LuaEngine(SimController& ctrl);

    void loadScript(const std::string& path);
    std::string eval(const std::string& code);
    void setVar(const std::string& key, const std::string& value);
    bool callFunction(const std::string& name);
    void registerEventCallback(const std::string& type, sol::protected_function fn);
    void collectAndDispatchEvents();
};

#endif // ENABLE_LUA
