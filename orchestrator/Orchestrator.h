#pragma once

#include <string>
#include <vector>
#include <memory>
#include <random>

#include "VirtualClock.h"
#include "NodeContext.h"
#include "LinkModel.h"
#include "EventLog.h"

struct OrchestratorConfig {
    unsigned long duration_ms = 300000;
    int step_ms = 5;
    uint32_t epoch_start = 1700000000;
    unsigned long warmup_ms = 0;
    bool hot_start = false;
    unsigned long hot_start_settle_ms = 10000;
    bool verbose = false;

    struct NodeDef {
        std::string name;
        NodeRole role = NodeRole::Repeater;
        int sf = 8;
        int bw = 62500;
        int cr = 4;
        double lat = 0.0;
        double lon = 0.0;
        bool has_location = false;
        AdversarialConfig adversarial;
    };
    std::vector<NodeDef> nodes;

    struct LinkDef {
        std::string from;
        std::string to;
        float snr  = 8.0f;
        float rssi = -80.0f;
        float snr_std_dev = 0.0f;
        float loss = 0.0f;
        bool bidir = true;
    };
    std::vector<LinkDef> links;

    struct CmdDef {
        unsigned long at_ms;
        std::string node;
        std::string command;
    };
    std::vector<CmdDef> commands;

    struct Assertion {
        std::string type;       // "cmd_reply_contains", "cmd_reply_not_contains", "event_count_min"
        std::string node;       // node name (for cmd_reply assertions)
        std::string command;    // command prefix to match
        std::string value;      // substring to check / event type for count
        int count = 0;          // for event_count_min
    };
    std::vector<Assertion> assertions;
};

class Orchestrator {
    struct ScheduledCommand {
        unsigned long at_ms;
        int node_index;
        std::string command;
    };

    VirtualClock _clock;
    std::vector<std::unique_ptr<NodeContext>> _nodes;
    std::unique_ptr<MatrixLinkModel> _link_model;
    std::vector<ScheduledCommand> _commands;
    unsigned long _duration_ms = 0;
    unsigned long _warmup_ms = 0;
    int _step_ms = 0;
    size_t _next_cmd = 0;
    bool _verbose = false;
    bool _hot_start = false;
    unsigned long _hot_start_settle_ms = 10000;
    std::mt19937 _rng{42};  // deterministic seed for reproducibility

    struct PendingReplay {
        unsigned long emit_ms;
        int sender_idx;
        std::vector<uint8_t> data;
        uint32_t airtime_ms;
    };
    std::vector<PendingReplay> _pending_replays;

    struct CmdReplyRecord {
        std::string node;
        std::string command;
        std::string reply;
    };
    std::vector<CmdReplyRecord> _reply_log;
    std::vector<OrchestratorConfig::Assertion> _assertions;
    int _tx_count = 0;
    int _rx_count = 0;

    int findNode(const std::string& name) const;
    void routePackets(unsigned long current_ms);
    void registerTransmissions(unsigned long current_ms);
    void deliverReceptions(unsigned long current_ms);
    void processCommands(unsigned long current_ms);
    void injectReplays(unsigned long current_ms);
    void hotStart();
    bool checkAssertions();

public:
    void configure(const OrchestratorConfig& cfg);
    bool run();  // returns true if all assertions pass (or none defined)
};
