#pragma once

#include <string>
#include <vector>
#include <map>
#include <cstdint>

struct MsgStats {
    int sent_flood = 0;       // direct messages sent via flood routing
    int sent_direct = 0;      // direct messages sent via direct routing
    int sent_group = 0;       // group/channel messages sent
    int acks_pending = 0;     // message acks we're waiting for
    int acks_received = 0;    // message acks received back
    // Per-destination send counts (key = destination name)
    std::map<std::string, int> sent_to;
    // Per-sender receive counts (key = sender name)
    std::map<std::string, int> recv_direct;   // direct messages received
    int recv_group = 0;       // group/channel messages received
    std::map<std::string, int> recv_group_by_sender; // channel messages by sender name

    int totalSent() const { return sent_flood + sent_direct + sent_group; }
    int totalRecvDirect() const {
        int n = 0;
        for (auto& kv : recv_direct) n += kv.second;
        return n;
    }
};

// Type-erased interface for mesh nodes in the orchestrator.
// Concrete implementations (RepeaterMeshWrapper, CompanionMeshWrapper)
// are created in RepeaterNode.cpp and CompanionNode.cpp respectively.
struct MeshWrapper {
    MsgStats msg_stats;

    virtual ~MeshWrapper() = default;
    virtual void loop() = 0;
    virtual const uint8_t* pubKey() = 0;
    virtual std::string handleCommand(uint32_t timestamp, const char* cmd) = 0;
    // Serialize self-advert packet bytes (for hot-start injection). Empty = not supported.
    virtual std::vector<uint8_t> exportSelfAdvert() { return {}; }
};
