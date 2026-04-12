#include "InteractiveRepl.h"

#ifdef ENABLE_LUA
#include "LuaEngine.h"
#endif

#include <iostream>
#include <string>
#include <cstdio>

#ifdef _WIN32
#include <io.h>
#define isatty _isatty
#define fileno _fileno
#else
#include <unistd.h>
#endif

using json = nlohmann::json;

InteractiveRepl::InteractiveRepl(SimController& ctrl)
    : _ctrl(ctrl), _tty(isatty(fileno(stdin))) {}

#ifdef ENABLE_LUA
InteractiveRepl::InteractiveRepl(SimController& ctrl, LuaEngine* lua)
    : _ctrl(ctrl), _tty(isatty(fileno(stdin))), _lua(lua)
{}
#endif

void InteractiveRepl::printPrompt() {
    if (_tty) {
        fprintf(stderr, "[%8.3fs] > ", _ctrl.currentTimeMs() / 1000.0);
        fflush(stderr);
    }
}

void InteractiveRepl::printResponse(const json& j) {
    printf("> %s\n", j.dump().c_str());
    fflush(stdout);
}

void InteractiveRepl::printResponse(const std::string& msg) {
    printf("> %s\n", msg.c_str());
    fflush(stdout);
}

void InteractiveRepl::handleStep(const std::string& args) {
    unsigned long delta_ms = 1000;  // default: 1 second
    if (!args.empty()) {
        try { delta_ms = std::stoul(args); }
        catch (...) {
            printResponse(std::string("ERROR: invalid step size: ") + args);
            return;
        }
    }
    if (_ctrl.isFinished()) {
        printResponse(std::string("Simulation already finished"));
        return;
    }
    auto result = _ctrl.step(delta_ms);
#ifdef ENABLE_LUA
    if (_lua) _lua->collectAndDispatchEvents();
#endif
    printResponse(json{
        {"stepped_to_ms", result.end_ms},
        {"events", result.events_generated},
        {"finished", result.finished}
    });
}

void InteractiveRepl::handleNext() {
    if (_ctrl.isFinished()) {
        printResponse(std::string("Simulation already finished"));
        return;
    }
    auto result = _ctrl.runToNextCommand();
#ifdef ENABLE_LUA
    if (_lua) _lua->collectAndDispatchEvents();
#endif
    printResponse(json{
        {"stepped_to_ms", result.end_ms},
        {"events", result.events_generated},
        {"finished", result.finished}
    });
}

void InteractiveRepl::handleTime() {
    printResponse(json{
        {"time_ms", _ctrl.currentTimeMs()},
        {"finished", _ctrl.isFinished()}
    });
}

void InteractiveRepl::handleNodes() {
    printResponse(_ctrl.queryNodes());
}

void InteractiveRepl::handleStatus(const std::string& args) {
    if (args.empty()) {
        printResponse(std::string("ERROR: usage: status <node_name>"));
        return;
    }
    printResponse(_ctrl.queryNodeStatus(args));
}

void InteractiveRepl::handleCmd(const std::string& args) {
    size_t space = args.find(' ');
    if (space == std::string::npos) {
        printResponse(std::string("ERROR: usage: cmd <node> <command...>"));
        return;
    }
    std::string node = args.substr(0, space);
    std::string command = args.substr(space + 1);
    printResponse(_ctrl.injectCommand(node, command));
}

void InteractiveRepl::handleEvents(const std::string& args) {
    int n = 10;
    if (!args.empty()) {
        try { n = std::stoi(args); }
        catch (...) {
            printResponse(std::string("ERROR: invalid count: ") + args);
            return;
        }
    }
    printResponse(_ctrl.queryEvents(n));
}

void InteractiveRepl::handleSummary() {
    printResponse(_ctrl.querySummary());
}

#ifdef ENABLE_LUA
void InteractiveRepl::handleLua(const std::string& args) {
    if (!_lua) {
        printResponse(std::string("ERROR: Lua not available (rebuild with -DENABLE_LUA=ON)"));
        return;
    }
    if (args.empty()) {
        printResponse(std::string("ERROR: usage: lua <code>"));
        return;
    }
    std::string result = _lua->eval(args);
    if (!result.empty()) {
        printResponse(result);
    }
}
#endif

void InteractiveRepl::handleHelp() {
    std::string help =
        "Commands:\n"
        "  step [N]            Advance N ms (default: 1000)\n"
        "  next                Run to next scheduled command\n"
        "  time                Show current simulation time\n"
        "  nodes               List all nodes\n"
        "  status <node>       Show node neighbors/contacts\n"
        "  cmd <node> <cmd>    Inject CLI command (see below)\n"
        "  events [N]          Show last N events (default 10)\n"
        "  summary             Show simulation summary\n"
#ifdef ENABLE_LUA
        "  lua <code>          Evaluate Lua expression\n"
#endif
        "  help                Show this help\n"
        "  quit / exit         End simulation\n"
        "\n"
        "Repeater CLI (passed directly to MeshCore CommonCLI — all commands supported):\n"
        "  neighbors           List neighbor table\n"
        "  neighbor.remove <pk> Remove neighbor by pubkey hex\n"
        "  clock               Show node clock\n"
        "  ver                 Show firmware version\n"
        "  advert              Send flood advertisement\n"
        "  advert.zerohop      Send zero-hop advertisement\n"
        "  stats-radio         Radio stats (rssi, snr, airtime)\n"
        "  stats-packets       Packet counters (sent/recv/flood/direct)\n"
        "  stats-core          Core stats (battery, uptime, errors)\n"
        "  clear stats         Reset statistics\n"
        "  get <param>         Get config (txdelay, rxdelay, af, name,\n"
        "                      radio, flood.max, direct.txdelay, tx,\n"
        "                      freq, repeat, lat, lon, public.key, ...)\n"
        "  set <param> <val>   Set config parameter\n"
        "\n"
        "Companion CLI (custom wrapper — only listed commands supported):\n"
        "  list [N]            List contacts (last N)\n"
        "  msg <contact> <txt> Send text message (flood)\n"
        "  msga <contact> <txt> Send with ACK tracking\n"
        "  msgc <text>         Send channel/group message\n"
        "  path <contact>      Show routing path to contact\n"
        "  disc_path <contact> Discover path via flood request\n"
        "  reset_path <contact> Reset path (force flood)\n"
        "  stats               Message send/recv statistics\n"
        "  neighbors           List all contacts (compact)\n"
        "  clock               Show node clock\n"
        "  ver                 Show version\n"
        "  advert              Send flood advertisement\n"
        "  advert.zerohop      Send zero-hop advertisement";
    printResponse(help);
}

int InteractiveRepl::run() {
    _ctrl.initialize();
    printPrompt();

    std::string line;
    while (std::getline(std::cin, line)) {
        // Trim whitespace
        size_t start = line.find_first_not_of(" \t\r\n");
        if (start == std::string::npos) { printPrompt(); continue; }
        size_t end = line.find_last_not_of(" \t\r\n");
        line = line.substr(start, end - start + 1);
        if (line.empty()) { printPrompt(); continue; }

        // Split into command and args
        size_t space = line.find(' ');
        std::string cmd = (space != std::string::npos) ? line.substr(0, space) : line;
        std::string args;
        if (space != std::string::npos) {
            size_t astart = line.find_first_not_of(" \t", space + 1);
            if (astart != std::string::npos)
                args = line.substr(astart);
        }

        if (cmd == "quit" || cmd == "exit") break;
        else if (cmd == "step") handleStep(args);
        else if (cmd == "next") handleNext();
        else if (cmd == "time") handleTime();
        else if (cmd == "nodes") handleNodes();
        else if (cmd == "status") handleStatus(args);
        else if (cmd == "cmd") handleCmd(args);
        else if (cmd == "events") handleEvents(args);
        else if (cmd == "summary") handleSummary();
        else if (cmd == "help") handleHelp();
#ifdef ENABLE_LUA
        else if (cmd == "lua") handleLua(args);
#endif
        else printResponse(std::string("ERROR: unknown command: ") + cmd + ". Type 'help' for usage.");

        printPrompt();
    }

    bool ok = _ctrl.finalize();
    return ok ? 0 : 1;
}
