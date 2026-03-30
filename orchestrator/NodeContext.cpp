// Include STL before Arduino.h (min/max macro safety)
#include <string>
#include <vector>
#include <memory>
#include <cstring>

#include "NodeContext.h"
#include "MeshWrapper.h"

// Factory functions (defined in RepeaterNode.cpp / CompanionNode.cpp)
std::unique_ptr<MeshWrapper> createRepeaterMesh(NodeContext& ctx);
std::unique_ptr<MeshWrapper> createCompanionMesh(NodeContext& ctx);

NodeContext::NodeContext(const std::string& name, NodeRole role,
                         VirtualClock& clk,
                         int sf, int bw, int cr)
    : name(name), role(role), radio(clk, sf, bw, cr), clock(&clk)
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
    _ctx_rtc     = clock;
    _ctx_serial  = &serial;
    _ctx_fs      = &filesystem;
    board._target   = &node_board;
    sensors._target = &sensors_obj;
}

void NodeContext::initMesh() {
    activate();

    // Seed RNG deterministically from node name (shared by both roles)
    rng.seed((const uint8_t*)name.c_str(), name.size());

    if (role == NodeRole::Companion) {
        mesh = createCompanionMesh(*this);
    } else {
        mesh = createRepeaterMesh(*this);
    }
}
