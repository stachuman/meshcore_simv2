// Orchestrator main entry point.
// Usage: orchestrator [-v] <config.json>
//    or: orchestrator [-v] --json '<json string>'
//    or: cat config.json | orchestrator [-v] -

// Include STL headers and json-dependent headers BEFORE anything that pulls in Arduino.h min/max macros
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <fstream>
#include <sstream>
#include <iostream>
#include <stdexcept>

#include "SimController.h"
#include "InteractiveRepl.h"

#include "Orchestrator.h"
#include "JsonConfig.h"

#undef min
#undef max

static void usage(const char* prog) {
    fprintf(stderr, "Usage: %s [-v|--verbose] [-i|--interactive] <config.json>\n", prog);
    fprintf(stderr, "   or: %s [-v|--verbose] --json '<json string>'\n", prog);
    fprintf(stderr, "   or: cat config.json | %s [-v|--verbose] -\n", prog);
    fprintf(stderr, "\nOptions:\n");
    fprintf(stderr, "  -v, --verbose       Human-readable progress output to stderr\n");
    fprintf(stderr, "  -i, --interactive   Step-on-demand REPL mode\n");
}

int main(int argc, char* argv[]) {
    setvbuf(stdout, NULL, _IOLBF, 0);

    bool verbose = false;
    bool interactive = false;

    // Collect non-flag arguments
    std::vector<const char*> args;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--verbose") == 0) {
            verbose = true;
        } else if (strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interactive") == 0) {
            interactive = true;
        } else {
            args.push_back(argv[i]);
        }
    }

    if (args.empty()) {
        usage(argv[0]);
        return 1;
    }

    OrchestratorConfig cfg;
    try {
        if (strcmp(args[0], "--json") == 0 && args.size() >= 2) {
            cfg = parseConfigString(args[1]);
        } else if (strcmp(args[0], "-") == 0) {
            std::ostringstream ss;
            ss << std::cin.rdbuf();
            cfg = parseConfigString(ss.str());
        } else {
            cfg = parseConfigFile(args[0]);
        }
    } catch (const std::exception& e) {
        fprintf(stderr, "Error: %s\n", e.what());
        return 1;
    }

    if (cfg.nodes.empty()) {
        fprintf(stderr, "Error: no nodes defined in config\n");
        return 1;
    }

    cfg.verbose = verbose;

    Orchestrator orch;
    orch.configure(cfg);

    if (interactive) {
        SimController ctrl(orch);
        InteractiveRepl repl(ctrl);
        return repl.run();
    }

    bool ok = orch.run();
    return ok ? 0 : 1;
}
