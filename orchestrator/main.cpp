// Orchestrator main entry point.
// Usage: orchestrator [-v] <config.json>
//    or: orchestrator [-v] --json '<json string>'
//    or: cat config.json | orchestrator [-v] -

// Include STL headers and json-dependent headers BEFORE anything that pulls in Arduino.h min/max macros
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cerrno>
#include <string>
#include <fstream>
#include <sstream>
#include <iostream>
#include <stdexcept>
#include <vector>
#include <utility>
#include <climits>
#include <unistd.h>
#include <dirent.h>
#include <algorithm>

#include "SimController.h"
#include "InteractiveRepl.h"

#ifdef ENABLE_LUA
#include "LuaEngine.h"
#endif

#include "Orchestrator.h"
#include "JsonConfig.h"

#undef min
#undef max

static void usage(const char* prog) {
    fprintf(stderr, "Usage: %s [-v|--verbose] [-i|--interactive] [--seed N] [--lua <script.lua>] [--lua-var key=value] <config.json>\n", prog);
    fprintf(stderr, "   or: %s [-v|--verbose] --json '<json string>'\n", prog);
    fprintf(stderr, "   or: cat config.json | %s [-v|--verbose] -\n", prog);
    fprintf(stderr, "\nOptions:\n");
    fprintf(stderr, "  -v, --verbose       Human-readable progress output to stderr\n");
    fprintf(stderr, "  -i, --interactive   Step-on-demand REPL mode\n");
    fprintf(stderr, "  --seed N            Override simulation.seed from config (uint64; default 42)\n");
#ifdef ENABLE_LUA
    fprintf(stderr, "  -l, --lua <script>  Load and run Lua script (script-driven or with -i)\n");
    fprintf(stderr, "  --lua-var key=val   Set Lua variable (accessible as vars.key)\n");
#endif
}

int main(int argc, char* argv[]) {
    setvbuf(stdout, NULL, _IOLBF, 0);

    bool verbose = false;
    bool interactive = false;
    std::string lua_script;
    std::vector<std::pair<std::string, std::string>> lua_vars;
    bool seed_override = false;
    uint64_t seed_override_value = 0;

    // Collect non-flag arguments
    std::vector<const char*> args;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--verbose") == 0) {
            verbose = true;
        } else if (strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interactive") == 0) {
            interactive = true;
        } else if (strcmp(argv[i], "--seed") == 0 && i + 1 < argc) {
            const char* sval = argv[++i];
            char* end = nullptr;
            errno = 0;
            unsigned long long parsed = strtoull(sval, &end, 0);
            if (errno != 0 || end == sval || *end != '\0') {
                fprintf(stderr, "Error: --seed requires a non-negative integer: %s\n", sval);
                return 1;
            }
            seed_override_value = static_cast<uint64_t>(parsed);
            seed_override = true;
        } else if ((strcmp(argv[i], "--lua") == 0 || strcmp(argv[i], "-l") == 0) && i + 1 < argc) {
            lua_script = argv[++i];
        } else if (strcmp(argv[i], "--lua-var") == 0 && i + 1 < argc) {
            std::string kv = argv[++i];
            size_t eq = kv.find('=');
            if (eq == std::string::npos) {
                fprintf(stderr, "Error: --lua-var requires key=value format: %s\n", kv.c_str());
                return 1;
            }
            lua_vars.emplace_back(kv.substr(0, eq), kv.substr(eq + 1));
        } else {
            args.push_back(argv[i]);
        }
    }

    if (args.empty()) {
        usage(argv[0]);
        return 1;
    }

#ifndef ENABLE_LUA
    if (!lua_script.empty() || !lua_vars.empty()) {
        fprintf(stderr, "Error: Lua support not compiled. Rebuild with -DENABLE_LUA=ON\n");
        return 1;
    }
#endif

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

    // Apply CLI seed override (takes precedence over simulation.seed in JSON).
    const char* seed_source = "default";
    if (seed_override) {
        cfg.seed = seed_override_value;
        seed_source = "cli";
    } else {
        // Distinguish "came from JSON" from "struct default" by reparsing intent;
        // simplest heuristic: if cfg.seed != 42 the config changed it.
        if (cfg.seed != 42) seed_source = "config";
    }
    // Always log the effective seed so every run is reproducible from its log.
    fprintf(stderr, "rng seed: %llu (%s)\n",
            static_cast<unsigned long long>(cfg.seed), seed_source);

    Orchestrator orch;

    // Load firmware plugins
    try {
        // Discover exe directory for auto-loading plugins
        std::string exe_dir;
        {
            char buf[PATH_MAX];
            ssize_t len = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
            if (len > 0) {
                buf[len] = '\0';
                exe_dir = buf;
                size_t slash = exe_dir.rfind('/');
                if (slash != std::string::npos) exe_dir = exe_dir.substr(0, slash);
            }
        }

        // Auto-discover all fw_*.so plugins next to the orchestrator binary
        if (!exe_dir.empty()) {
            DIR* dir = opendir(exe_dir.c_str());
            if (dir) {
                std::vector<std::string> plugin_files;
                struct dirent* entry;
                while ((entry = readdir(dir)) != nullptr) {
                    std::string name(entry->d_name);
                    if (name.size() > 6 && name.substr(0, 3) == "fw_" &&
                        name.substr(name.size() - 3) == ".so") {
                        plugin_files.push_back(name);
                    }
                }
                closedir(dir);
                std::sort(plugin_files.begin(), plugin_files.end());
                for (auto& fname : plugin_files) {
                    std::string plugin_name = fname.substr(0, fname.size() - 3);
                    orch.firmwareRegistry().load(plugin_name, exe_dir + "/" + fname);
                }
            }
        }

        // Load additional plugins from config (explicit paths)
        for (auto& [name, path] : cfg.firmware.plugins) {
            if (!orch.firmwareRegistry().get(name))
                orch.firmwareRegistry().load(name, path);
        }
    } catch (const std::exception& e) {
        fprintf(stderr, "Error: %s\n", e.what());
        return 1;
    }

    orch.configure(cfg);

#ifdef ENABLE_LUA
    if (!lua_script.empty() || interactive) {
        SimController ctrl(orch);
        LuaEngine lua(ctrl);
        for (auto& [k, v] : lua_vars) lua.setVar(k, v);
        orch.setLuaCallback([&](const std::string& fn) { return lua.callFunction(fn); });

        if (!lua_script.empty()) {
            try {
                lua.loadScript(lua_script);
            } catch (const std::exception& e) {
                fprintf(stderr, "Error: %s\n", e.what());
                return 1;
            }
        }

        if (interactive) {
            InteractiveRepl repl(ctrl, &lua);
            return repl.run();
        } else {
            // Pure Lua: script IS the main loop.
            // Lua assert()/error() → loadScript throws → return 1 above.
            // Scripts should call sim:finalize() and use os.exit(1) on failure,
            // or use sim_assert() which raises a Lua error on failure.
            return 0;
        }
    }
#endif

    if (interactive) {
        SimController ctrl(orch);
        InteractiveRepl repl(ctrl);
        return repl.run();
    }

    bool ok = orch.run();
    return ok ? 0 : 1;
}
