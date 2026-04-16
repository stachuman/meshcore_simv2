#pragma once

#include <string>
#include <vector>
#include <memory>
#include <random>
#include <functional>
#include <map>
#include <set>
#include <unordered_map>
#include <unordered_set>

#include "VirtualClock.h"
#include "NodeContext.h"
#include "LinkModel.h"
#include "EventLog.h"
#include "FirmwarePlugin.h"

struct OrchestratorConfig {
    unsigned long duration_ms = 300000;
    int step_ms = 1;
    uint32_t epoch_start = 1700000000;
    unsigned long warmup_ms = 0;
    bool hot_start = false;
    bool verbose = false;
    uint64_t seed = 42;

    // Global radio defaults (overridable per-node)
    int sf = 8;
    int bw = 62500;
    int cr = 1;

    // Radio physics tuning
    float capture_locked_db = 3.0f;    // capture threshold when primary locked
    float capture_unlocked_db = 6.0f;  // capture threshold when preambles overlap
    float cad_miss_prob = 0.05f;       // CAD false-negative probability [0,1]
    float cad_reliable_snr = 0.0f;    // above this SNR: cad_miss_prob applies
    float cad_marginal_snr = -15.0f;  // below this SNR: always miss. Between: interpolate.
    float snr_coherence_ms = 0.0f;     // fading coherence time (0 = i.i.d.)

    // Hardware turnaround delays (SX1262 defaults)
    float rx_to_tx_delay_ms = 1.0f;    // RX→TX turnaround time
    float tx_to_rx_delay_ms = 5.0f;    // TX→RX turnaround time

    // Runtime delay tuning (used only with DELAY_TUNING_RUNTIME builds)
    struct DelayTuningParams {
        bool enabled = false;
        float tx_base = 1.0f, tx_slope = 0.0f;
        float dtx_base = 0.4f, dtx_slope = 0.0f;
        float rx_base = 0.4f, rx_slope = 0.0f;
        float clamp_min = 0.0f, clamp_max = 5.0f;
    };
    DelayTuningParams delay_tuning;

    // Firmware plugin config
    struct FirmwareConfig {
        std::string default_firmware = "fw_default";
        std::map<std::string, std::string> plugins;  // name -> path
    };
    FirmwareConfig firmware;

    struct NodeDef {
        std::string name;
        NodeRole role = NodeRole::Repeater;
        int sf = -1;   // -1 = use global default
        int bw = -1;
        int cr = -1;
        double lat = 0.0;
        double lon = 0.0;
        bool has_location = false;
        AdversarialConfig adversarial;
        float tx_fail_prob = 0.0f;
        std::string firmware;  // empty = use default
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
        std::string node;      // empty for lua-only commands
        std::string command;   // empty for lua-only commands
        std::string lua_fn;    // non-empty = call named Lua function instead
    };
    std::vector<CmdDef> commands;

    struct Assertion {
        std::string type;       // "cmd_reply_contains", "cmd_reply_not_contains", "event_count_min", "tx_airtime_between", "event_count"
        std::string node;       // node name (for cmd_reply / tx_airtime / event_count assertions)
        std::string command;    // command prefix to match
        std::string value;      // substring to check / event type for count
        std::string event_type; // NDJSON event type string for event_count
        int count = 0;          // for event_count_min
        int min = -1;           // for tx_airtime_between / event_count (-1 = no lower bound)
        int max = -1;           // for tx_airtime_between / event_count (-1 = no upper bound)
    };
    std::vector<Assertion> assertions;
};

class Orchestrator {
public:
    // Event hook type: called for every NDJSON event string emitted.
    using EventHook = std::function<void(const std::string&)>;

private:
    struct ScheduledCommand {
        unsigned long at_ms;
        int node_index;        // -1 for lua-only commands
        std::string command;
        std::string lua_fn;    // non-empty = call Lua function
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
    uint64_t _seed = 42;
    // Separate RNG streams per stochastic process (seeded from cfg.seed in configure())
    std::mt19937 _rng_fading;      // SNR fading (i.i.d. + O-U)
    std::mt19937 _rng_loss;        // stochastic link loss
    std::mt19937 _rng_cad;         // CAD miss probability
    std::mt19937 _rng_stagger;     // clock stagger
    std::mt19937 _rng_adversarial; // adversarial mode rolls
    EventHook _event_hook;

    // Lua callback hook: called when a scheduled {"lua": "fn_name"} fires
    using LuaCallbackHook = std::function<bool(const std::string&)>;
    LuaCallbackHook _lua_callback;

    // Radio physics parameters
    float _capture_locked_db = 3.0f;
    float _capture_unlocked_db = 6.0f;
    float _cad_miss_prob = 0.05f;
    float _cad_reliable_snr = 0.0f;
    float _cad_marginal_snr = -15.0f;
    float _snr_coherence_ms = 0.0f;

    // Per-directed-link fading state (Ornstein-Uhlenbeck)
    struct LinkFadingState {
        float offset = 0.0f;         // current fading offset from mean SNR
        unsigned long last_ms = 0;   // timestamp of last update
    };
    std::vector<LinkFadingState> _fading_state;  // n*(n-1)/2, symmetric (reciprocal fading)

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
    struct TxRecord {
        std::string node;
        uint32_t airtime_ms;
    };
    std::vector<CmdReplyRecord> _reply_log;
    std::vector<TxRecord> _tx_log;
    std::vector<OrchestratorConfig::Assertion> _assertions;
    int _tx_count = 0;
    int _rx_count = 0;
    int _ackpath_tx = 0;
    int _ackpath_rx = 0;
    int _ackpath_collision = 0;
    int _ackpath_drop = 0;  // halfduplex + loss (no weak — weak = never viable)
    std::map<std::string, int> _event_counts;  // "event_type" or "event_type:node" → count

    // Pre-built per-node event keys to avoid string alloc on hot path
    struct NodeEventKeys {
        std::string tx, rx, collision, drop_halfduplex, drop_weak, drop_loss, tx_fail;
    };
    std::vector<NodeEventKeys> _node_event_keys;

    // Per-message fate tracking: follows each scheduled message through relay chain
    struct MessageFate {
        int from_idx;
        int to_idx;                 // destination companion node index
        unsigned long send_time_ms;
        std::unordered_set<uint32_t> pkt_hashes;  // all hashes in this message's relay tree
        int tx_count = 0;
        int rx_count = 0;
        int collisions = 0;
        int drops = 0;              // weak + halfduplex + loss combined
        bool delivered = false;     // tracked hash reached to_idx as successful RX
        bool sent_as_flood = true;  // routing type at send time (flood vs direct/path)
        int ackpath_rx_at_sender = 0;  // how many ACK/PATH copies reached from_idx
    };
    std::vector<MessageFate> _message_fates;
    std::unordered_map<uint32_t, int> _hash_to_fate;  // pkt_hash → fate index
    std::unordered_map<int, int> _pending_msg_fates;   // node_idx → fate index (awaiting initial TX)
    std::vector<std::set<int>> _step_rx_fates;          // [node_idx] → fate indices delivered this step

    // Per-message ACK/PATH tracking: follows ack/path packets back to original sender
    std::unordered_map<uint32_t, int> _ackpath_hash_to_fate;  // ackpath pkt hash → fate index
    std::vector<std::set<int>> _pending_ackpath_fates;   // [node_idx] → fates awaiting initial ack/path TX
    std::vector<std::set<int>> _ackpath_relay_fates;     // [node_idx] → fates for ack/path relay linking

    int findNode(const std::string& name) const;
    void routePackets(unsigned long current_ms);
    void registerTransmissions(unsigned long current_ms);
    void deliverReceptions(unsigned long current_ms);
    void processCommands(unsigned long current_ms);
    void injectReplays(unsigned long current_ms);
    void hotStart();

public:
    void configure(const OrchestratorConfig& cfg);
    bool run();  // returns true if all assertions pass (or none defined)

    // --- Interactive mode support ---
    unsigned long initSimulation();
    unsigned long executeStep(unsigned long current_ms);
    void emitSummary(unsigned long current_ms);
    bool checkAssertions();  // moved from private to public

    // Accessors for SimController
    unsigned long durationMs() const { return _duration_ms; }
    int stepMs() const { return _step_ms; }
    unsigned long warmupMs() const { return _warmup_ms; }
    size_t nodeCount() const { return _nodes.size(); }
    NodeContext* nodeAt(size_t i) { return _nodes[i].get(); }
    int findNodeByName(const std::string& name) const { return findNode(name); }

    void setEventHook(EventHook hook) { _event_hook = std::move(hook); }
    void setLuaCallback(LuaCallbackHook hook) { _lua_callback = std::move(hook); }

    // Firmware plugin registry — must be populated before configure()
    FirmwareRegistry& firmwareRegistry() { return _firmware_registry; }

private:
    FirmwareRegistry _firmware_registry;
};
