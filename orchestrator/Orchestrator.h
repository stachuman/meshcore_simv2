#pragma once

#include <string>
#include <vector>
#include <memory>
#include <random>
#include <map>
#include <set>
#include <unordered_map>
#include <unordered_set>

#include "VirtualClock.h"
#include "NodeContext.h"
#include "LinkModel.h"
#include "EventLog.h"

struct OrchestratorConfig {
    unsigned long duration_ms = 300000;
    int step_ms = 4;
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
    uint64_t _seed = 42;
    // Separate RNG streams per stochastic process (seeded from cfg.seed in configure())
    std::mt19937 _rng_fading;      // SNR fading (i.i.d. + O-U)
    std::mt19937 _rng_loss;        // stochastic link loss
    std::mt19937 _rng_cad;         // CAD miss probability
    std::mt19937 _rng_stagger;     // clock stagger
    std::mt19937 _rng_adversarial; // adversarial mode rolls

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
    };
    std::vector<MessageFate> _message_fates;
    std::unordered_map<uint32_t, int> _hash_to_fate;  // pkt_hash → fate index
    std::unordered_map<int, int> _pending_msg_fates;   // node_idx → fate index (awaiting initial TX)
    std::vector<std::set<int>> _step_rx_fates;          // [node_idx] → fate indices delivered this step

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
