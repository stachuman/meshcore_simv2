#pragma once

#include "SimController.h"

#ifdef ENABLE_LUA
class LuaEngine;
#endif

class InteractiveRepl {
    SimController& _ctrl;
    bool _tty;  // true if stdin is a terminal

#ifdef ENABLE_LUA
    LuaEngine* _lua = nullptr;
    void handleLua(const std::string& args);
#endif

    void printPrompt();
    void printResponse(const nlohmann::json& j);
    void printResponse(const std::string& msg);
    void handleStep(const std::string& args);
    void handleNext();
    void handleNextEvent();
    void handleTime();
    void handleNodes();
    void handleStatus(const std::string& args);
    void handleCmd(const std::string& args);
    void handleEvents(const std::string& args);
    void handleSummary();
    void handleHelp();

public:
    explicit InteractiveRepl(SimController& ctrl);
#ifdef ENABLE_LUA
    InteractiveRepl(SimController& ctrl, LuaEngine* lua);
#endif
    int run();  // Returns 0 = ok, 1 = assertion failure
};
