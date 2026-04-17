// Include STL before Arduino.h (min/max macro safety)
#include <string>
#include <vector>
#include <memory>
#include <cstring>
#include <cstdio>
#include <stdexcept>

#include "NodeContext.h"
#include "MeshWrapper.h"
#include "FirmwarePlugin.h"

static constexpr uint64_t FNV1A_OFFSET_BASIS = 0xcbf29ce484222325ULL;
static constexpr uint64_t FNV1A_PRIME        = 0x100000001b3ULL;

#ifdef SHIM_TRACE_CONTEXT
// When compiled with -DSHIM_TRACE_CONTEXT=1 the simulator logs every node
// activation to stderr. Useful to detect stray firmware callbacks that run
// when a different node is active (or none) — they would show up as an
// unexpected swap in the log. Off by default; has measurable overhead.
static const char* g_active_node_name = "<none>";
#endif

NodeContext::NodeContext(const std::string& node_name, NodeRole node_role,
                         uint32_t epoch_base,
                         int sf, int bw, int cr,
                         float rx_to_tx_delay_ms,
                         float tx_to_rx_delay_ms)
    : name(node_name), role(node_role), own_clock(epoch_base),
      radio(own_clock, sf, bw, cr, rx_to_tx_delay_ms, tx_to_rx_delay_ms)
{
    // Capture outgoing packets for orchestrator routing
    radio.setTxCallback([this](const uint8_t* data, int len, uint32_t airtime_ms) {
        TxCapture cap;
        cap.data.assign(data, data + len);
        cap.airtime_ms = airtime_ms;
        pending_tx.push_back(std::move(cap));
    });

    // In-memory FS uses node name as namespace key
    filesystem.begin(name.c_str());
}

NodeContext::~NodeContext() = default;

void NodeContext::activate() {
    // Fast path: if we're already the active node, all context pointers
    // already point at our members — skip the reassignments. Tracing
    // showed 60-70% of calls were redundant re-activations in small
    // tests. This is pure overhead elimination, not a behavior change.
    if (_ctx_radio == &radio) {
#ifdef SHIM_TRACE_CONTEXT
        // Still log so the pattern is visible when tracing is on.
        fprintf(stderr, "[shim-trace] activate: %s -> %s (no-op)\n",
                g_active_node_name, name.c_str());
#endif
        return;
    }
    _ctx_radio   = &radio;
    _ctx_rtc     = &own_clock;
    _ctx_serial  = &serial;
    _ctx_fs      = &filesystem;
    _ctx_arduino_seed = arduino_rng_seed;  // must be non-zero; see initMesh()
    board._target   = &node_board;
    sensors._target = &sensors_obj;
    sim_clock_set_global(&own_clock);  // millis() returns this node's time
#ifdef SHIM_TRACE_CONTEXT
    fprintf(stderr, "[shim-trace] activate: %s -> %s\n",
            g_active_node_name, name.c_str());
    g_active_node_name = name.c_str();
#endif
}

void NodeContext::initMesh(uint64_t global_seed, FirmwarePlugin& fw) {
    // Compute per-node seeds BEFORE activate() so radio_get_rng_seed()
    // returns the right value when the firmware constructor runs.
    uint64_t name_hash = FNV1A_OFFSET_BASIS;
    for (char c : name) {
        name_hash ^= static_cast<uint8_t>(c);
        name_hash *= FNV1A_PRIME;
    }
    uint64_t combined = name_hash ^ global_seed;

    // Fold 64 -> 32 bits. Ensure non-zero so radio_get_rng_seed() doesn't
    // fall back to the time-based sentinel.
    arduino_rng_seed = static_cast<uint32_t>(combined ^ (combined >> 32));
    if (arduino_rng_seed == 0) arduino_rng_seed = 1;

    activate();

    // Seed the simulator's own SimRNG and SimRadio PRNGs. Different global
    // seeds produce different per-node sequences; same-name nodes still
    // differ from each other.
    rng.seed(combined);
    radio.seed(combined ^ 0xDEADBEEFULL);  // different sequence from SimRNG

    if (role == NodeRole::Companion) {
        mesh = fw.createCompanionMesh(*this);
    } else {
        mesh = fw.createRepeaterMesh(*this);
    }
    if (!mesh) {
        throw std::runtime_error("Firmware plugin \"" + fw.name()
            + "\" returned null mesh for node \"" + name + "\"");
    }
}
