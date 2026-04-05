#pragma once

// IMPORTANT: Include STL headers BEFORE Arduino.h to avoid min/max macro conflicts
#include <string>
#include <vector>
#include <memory>

#include "SimBoard.h"
#include "SimRadio.h"
#include "SimRNG.h"
#include "SimClock.h"
#include "VirtualClock.h"

#include <Arduino.h>
#include <target.h>
#include <FS.h>
#include <helpers/SensorManager.h>
#include <helpers/SimpleMeshTables.h>
#include <helpers/IdentityStore.h>

// Single undef block AFTER all macro-defining headers
#undef radio_driver
#undef rtc_clock
#undef Serial
#undef LittleFS

struct MeshWrapper;

enum class NodeRole { Repeater, Companion };

enum class AdversarialMode { None, Drop, Corrupt, Replay };

struct AdversarialConfig {
    AdversarialMode mode = AdversarialMode::None;
    float probability = 1.0f;
    int corrupt_bits = 1;
    unsigned long replay_delay_ms = 5000;
};

struct PendingRx {
    int sender_idx;
    unsigned long rx_start_ms;
    unsigned long rx_end_ms;
    std::vector<uint8_t> data;
    float snr;
    float rssi;
    bool collided = false;
    bool link_loss = false;
    bool halfduplex_abort = false;
    int interferer_idx = -1;       // sender index of strongest interferer
    float interferer_snr = 0.0f;   // interferer's SNR at this receiver
    float snr_margin = 0.0f;       // how much stronger victim needed to survive
};

// NullSerial: discards all output (Serial is unused in orchestrator)
class NullSerial : public HardwareSerial {
public:
    size_t write(uint8_t) override { return 1; }
    size_t write(const uint8_t*, size_t n) override { return n; }
};

struct NodeContext {
    std::string name;
    NodeRole role;
    double lat = 0.0;
    double lon = 0.0;
    bool has_location = false;

    VirtualClock own_clock;   // Must precede radio (radio holds clock ref)
    SimBoard node_board;
    SimRadio radio;
    SensorManager sensors_obj;
    fs::FS filesystem;
    NullSerial serial;
    SimRNG rng;
    SimpleMeshTables tables;
    std::unique_ptr<MeshWrapper> mesh;
    std::vector<TxCapture> pending_tx;
    std::vector<PendingRx> active_rx;
    unsigned long tx_busy_until = 0;
    unsigned long tx_busy_from = 0;
    AdversarialConfig adversarial;

    NodeContext(const std::string& name, NodeRole role,
                uint32_t epoch_base,
                int sf = 8, int bw = 62500, int cr = 4);
    ~NodeContext();

    // Swap all 6 global context pointers/proxies to point at this node's
    // objects. Must be called before any MeshCore code runs for this node.
    void activate();

    // Construct mesh (repeater or companion based on role), load identity, begin()
    void initMesh(uint64_t global_seed);
};
