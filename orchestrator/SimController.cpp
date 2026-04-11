#include "SimController.h"
#include "Orchestrator.h"
#include "NodeContext.h"
#include "MeshWrapper.h"
#include "EventLog.h"

#include <sstream>
#include <algorithm>
#include <cstring>

using json = nlohmann::json;

// ---------------------------------------------------------------------------
// Constructor, init, event hook
// ---------------------------------------------------------------------------

SimController::SimController(Orchestrator& orch)
    : _orch(orch) {}

void SimController::onEvent(const std::string& line) {
    _event_buffer.push_back(line);
    if (_event_buffer.size() > MAX_EVENT_BUFFER) {
        _event_buffer.pop_front();
    }
}

void SimController::buildPubkeyMap() {
    _pubkey_to_name.clear();
    static const char HEX[] = "0123456789abcdef";
    for (size_t i = 0; i < _orch.nodeCount(); i++) {
        auto* node = _orch.nodeAt(i);
        const uint8_t* pk = node->mesh->pubKey();
        char hex[9];
        for (int j = 0; j < 4; j++) {
            hex[j*2]     = HEX[pk[j] >> 4];
            hex[j*2 + 1] = HEX[pk[j] & 0x0F];
        }
        hex[8] = '\0';
        _pubkey_to_name[hex] = node->name;
    }
}

void SimController::initialize() {
    EventLog::setEventHook([this](const std::string& line) {
        onEvent(line);
    });
    _current_ms = _orch.initSimulation();
    buildPubkeyMap();
    _initialized = true;
}

// ---------------------------------------------------------------------------
// step() and runToNextCommand()
// ---------------------------------------------------------------------------

bool SimController::isFinished() const {
    return _current_ms >= _orch.durationMs();
}

int SimController::stepMs() const {
    return _orch.stepMs();
}

StepResult SimController::step(unsigned long delta_ms) {
    StepResult result;
    result.start_ms = _current_ms;
    size_t events_before = _event_buffer.size();

    unsigned long target_ms = _current_ms + delta_ms;
    if (target_ms > _orch.durationMs())
        target_ms = _orch.durationMs();

    while (_current_ms < target_ms) {
        _current_ms = _orch.executeStep(_current_ms);
    }

    result.end_ms = _current_ms;
    result.events_generated = (int)(_event_buffer.size() - events_before);
    result.finished = isFinished();
    return result;
}

StepResult SimController::runToNextCommand() {
    StepResult result;
    result.start_ms = _current_ms;
    size_t events_before = _event_buffer.size();

    while (_current_ms < _orch.durationMs()) {
        _current_ms = _orch.executeStep(_current_ms);
        // Stop when a cmd_reply event appears (a scheduled command fired)
        if (_event_buffer.size() > events_before) {
            const std::string& last_event = _event_buffer.back();
            if (last_event.find("\"cmd_reply\"") != std::string::npos) {
                break;
            }
        }
    }

    result.end_ms = _current_ms;
    result.events_generated = (int)(_event_buffer.size() - events_before);
    result.finished = isFinished();
    return result;
}

// ---------------------------------------------------------------------------
// queryNodes()
// ---------------------------------------------------------------------------

json SimController::queryNodes() const {
    json nodes = json::array();
    static const char HEX[] = "0123456789abcdef";
    for (size_t i = 0; i < _orch.nodeCount(); i++) {
        auto* node = _orch.nodeAt(i);
        const uint8_t* pk = node->mesh->pubKey();
        char hex[65];
        for (int j = 0; j < 32; j++) {
            hex[j*2]     = HEX[pk[j] >> 4];
            hex[j*2 + 1] = HEX[pk[j] & 0x0F];
        }
        hex[64] = '\0';
        nodes.push_back({
            {"name", node->name},
            {"role", node->role == NodeRole::Repeater ? "repeater" : "companion"},
            {"pubkey", std::string(hex)}
        });
    }
    return nodes;
}

// ---------------------------------------------------------------------------
// parseRepeaterNeighbors() and queryNodeStatus()
// ---------------------------------------------------------------------------

json SimController::parseRepeaterNeighbors(const std::string& raw) const {
    json neighbors = json::array();
    if (raw == "-none-" || raw.empty()) return neighbors;

    std::istringstream ss(raw);
    std::string line;
    while (std::getline(ss, line)) {
        if (line.empty()) continue;
        // Format: PUBKEY_HEX:SECS_AGO:SNR
        size_t colon1 = line.find(':');
        if (colon1 == std::string::npos) continue;
        size_t colon2 = line.find(':', colon1 + 1);
        if (colon2 == std::string::npos) continue;

        std::string pubkey = line.substr(0, colon1);
        int secs_ago = 0;
        int snr_raw = 0;
        try {
            secs_ago = std::stoi(line.substr(colon1 + 1, colon2 - colon1 - 1));
            snr_raw = std::stoi(line.substr(colon2 + 1));
        } catch (...) { continue; }

        json entry = {
            {"pubkey", pubkey},
            {"last_seen_s", secs_ago},
            {"snr_db", snr_raw / 4.0}
        };
        // MeshCore outputs uppercase hex; our map uses lowercase — normalize
        std::string pk_lower = pubkey;
        std::transform(pk_lower.begin(), pk_lower.end(), pk_lower.begin(), ::tolower);
        auto it = _pubkey_to_name.find(pk_lower);
        if (it != _pubkey_to_name.end()) {
            entry["name"] = it->second;
        }
        neighbors.push_back(entry);
    }
    return neighbors;
}

json SimController::queryNodeStatus(const std::string& node_name) const {
    int idx = _orch.findNodeByName(node_name);
    if (idx < 0) {
        return {{"error", "node not found: " + node_name}};
    }

    auto* node = _orch.nodeAt(idx);
    json result = {
        {"node", node->name},
        {"role", node->role == NodeRole::Repeater ? "repeater" : "companion"},
        {"time_ms", _current_ms}
    };

    if (node->role == NodeRole::Repeater) {
        node->activate();
        std::string reply = node->mesh->handleCommand(0, "neighbors");
        json neighbors = parseRepeaterNeighbors(reply);
        result["neighbor_count"] = (int)neighbors.size();
        result["neighbors"] = neighbors;
    } else {
        // Companion: use structured contact access
        int count = node->mesh->getContactCount();
        json contacts = json::array();
        if (count > 0) {
            static const char HEX[] = "0123456789abcdef";
            for (int i = 0; i < count; i++) {
                MeshWrapper::ContactData cd;
                if (!node->mesh->getContact(i, cd)) continue;

                const char* type_str = "unknown";
                if (cd.type == 0) type_str = "repeater";
                else if (cd.type == 1) type_str = "companion";
                else if (cd.type == 2) type_str = "room_server";

                char hex[9];
                for (int j = 0; j < 4; j++) {
                    hex[j*2]     = HEX[cd.pubkey[j] >> 4];
                    hex[j*2 + 1] = HEX[cd.pubkey[j] & 0x0F];
                }
                hex[8] = '\0';

                json entry = {
                    {"name", std::string(cd.name)},
                    {"type", type_str},
                    {"pubkey", std::string(hex)},
                    {"path_len", cd.out_path_len == 0xFF ? -1 : (int)cd.out_path_len},
                    {"last_seen_s", (int)cd.last_seen_s}
                };
                contacts.push_back(entry);
            }
        }
        result["contact_count"] = (int)contacts.size();
        result["contacts"] = contacts;
    }

    return result;
}

// ---------------------------------------------------------------------------
// queryEvents(), querySummary(), injectCommand(), finalize()
// ---------------------------------------------------------------------------

json SimController::queryEvents(int last_n) const {
    json events = json::array();
    int start = (int)_event_buffer.size() - last_n;
    if (start < 0) start = 0;
    for (int i = start; i < (int)_event_buffer.size(); i++) {
        try {
            std::string line = _event_buffer[i];
            while (!line.empty() && (line.back() == '\n' || line.back() == '\r'))
                line.pop_back();
            if (!line.empty())
                events.push_back(json::parse(line));
        } catch (...) {}
    }
    return events;
}

json SimController::querySummary() const {
    json s = {
        {"time_ms", _current_ms},
        {"finished", isFinished()},
        {"node_count", (int)_orch.nodeCount()},
        {"duration_ms", _orch.durationMs()},
        {"step_ms", _orch.stepMs()}
    };
    return s;
}

json SimController::injectCommand(const std::string& node_name, const std::string& command) {
    int idx = _orch.findNodeByName(node_name);
    if (idx < 0) {
        return {{"error", "node not found: " + node_name}};
    }

    auto* node = _orch.nodeAt(idx);
    node->activate();
    // Pass timestamp=0 to indicate local/serial origin — MeshCore gates
    // some commands (stats-radio, stats-packets, stats-core) on sender_timestamp==0
    std::string reply = node->mesh->handleCommand(0, command.c_str());

    EventLog::cmdReply(_current_ms, node->name.c_str(),
                       command.c_str(), reply.c_str());

    return {
        {"node", node_name},
        {"command", command},
        {"reply", reply},
        {"time_ms", _current_ms}
    };
}

bool SimController::finalize() {
    _orch.emitSummary(_current_ms);
    EventLog::setEventHook(nullptr);
    return _orch.checkAssertions();
}
