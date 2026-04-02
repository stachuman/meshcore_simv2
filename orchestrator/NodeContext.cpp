// Include STL before Arduino.h (min/max macro safety)
#include <string>
#include <vector>
#include <memory>
#include <cstring>

#include "NodeContext.h"
#include "MeshWrapper.h"

static constexpr uint64_t FNV1A_OFFSET_BASIS = 0xcbf29ce484222325ULL;
static constexpr uint64_t FNV1A_PRIME        = 0x100000001b3ULL;

// Factory functions (defined in RepeaterNode.cpp / CompanionNode.cpp)
std::unique_ptr<MeshWrapper> createRepeaterMesh(NodeContext& ctx);
std::unique_ptr<MeshWrapper> createCompanionMesh(NodeContext& ctx);

NodeContext::NodeContext(const std::string& name, NodeRole role,
                         uint32_t epoch_base,
                         int sf, int bw, int cr)
    : name(name), role(role), own_clock(epoch_base),
      radio(own_clock, sf, bw, cr)
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
    _ctx_radio   = &radio;
    _ctx_rtc     = &own_clock;
    _ctx_serial  = &serial;
    _ctx_fs      = &filesystem;
    board._target   = &node_board;
    sensors._target = &sensors_obj;
    sim_clock_set_global(&own_clock);  // millis() returns this node's time
}

void NodeContext::initMesh(uint64_t global_seed) {
    activate();

    // Seed RNG deterministically from global seed XOR'd with node name hash.
    // Different global seeds produce different per-node sequences;
    // same-name nodes still differ from each other.
    uint64_t name_hash = FNV1A_OFFSET_BASIS;
    for (char c : name) {
        name_hash ^= static_cast<uint8_t>(c);
        name_hash *= FNV1A_PRIME;
    }
    rng.seed(name_hash ^ global_seed);
    radio.seed(name_hash ^ global_seed ^ 0xDEADBEEF);  // different sequence from SimRNG

    if (role == NodeRole::Companion) {
        mesh = createCompanionMesh(*this);
    } else {
        mesh = createRepeaterMesh(*this);
    }
}
