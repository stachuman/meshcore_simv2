#pragma once

#include "SimController.h"

class InteractiveRepl {
    SimController& _ctrl;
    bool _tty;  // true if stdin is a terminal

    void printPrompt();
    void printResponse(const nlohmann::json& j);
    void printResponse(const std::string& msg);
    void handleStep(const std::string& args);
    void handleNext();
    void handleTime();
    void handleNodes();
    void handleStatus(const std::string& args);
    void handleCmd(const std::string& args);
    void handleEvents(const std::string& args);
    void handleSummary();
    void handleHelp();

public:
    explicit InteractiveRepl(SimController& ctrl);
    int run();  // Returns 0 = ok, 1 = assertion failure
};
