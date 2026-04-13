#pragma once

#include <string>
#include <vector>
#include <deque>
#include <unordered_map>
#include <cstdint>

#include "third_party/json.hpp"

class Orchestrator;
struct NodeContext;

struct StepResult {
    unsigned long start_ms;
    unsigned long end_ms;
    int events_generated;
    bool finished;
};

class SimController {
    Orchestrator& _orch;
    unsigned long _current_ms = 0;
    bool _initialized = false;

    // Ring buffer of recent NDJSON event lines
    std::deque<std::string> _event_buffer;
    static constexpr size_t MAX_EVENT_BUFFER = 1000;

    // Incremental event drain tracking
    size_t _total_events_received = 0;
    size_t _drain_offset = 0;

    // Pubkey-to-name lookup (built during init from node pubkeys)
    std::unordered_map<std::string, std::string> _pubkey_to_name;

    void onEvent(const std::string& line);
    void buildPubkeyMap();

    // Parsing helpers
    nlohmann::json parseRepeaterNeighbors(const std::string& raw) const;

public:
    explicit SimController(Orchestrator& orch);

    // Must be called once before stepping. Runs init + hot-start.
    void initialize();

    // --- Time control ---
    StepResult step(unsigned long delta_ms);
    StepResult runToNextCommand();
    StepResult runToNextEvent();
    unsigned long currentTimeMs() const { return _current_ms; }
    bool isFinished() const;
    int stepMs() const;

    // --- State queries ---
    nlohmann::json queryNodes() const;
    nlohmann::json queryNodeStatus(const std::string& node_name) const;
    nlohmann::json queryEvents(int last_n = 10) const;
    nlohmann::json querySummary() const;

    // --- Command injection ---
    nlohmann::json injectCommand(const std::string& node_name, const std::string& command);

    // --- Message statistics ---
    nlohmann::json queryMessageStats(const std::string& node_name) const;

    // --- Event drain (for Lua scripting) ---
    std::vector<std::string> drainNewEvents();

    // --- Finalization ---
    bool finalize();
};
