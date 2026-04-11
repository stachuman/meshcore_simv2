#pragma once

#include <string>
#include <vector>
#include <map>
#include <cstdint>

struct MsgStats {
    int sent_flood = 0;       // direct messages sent via flood routing
    int sent_direct = 0;      // direct messages sent via direct/path routing
    int sent_group = 0;       // group/channel messages sent
    // ACK tracking split by routing type
    int acks_flood_pending = 0;
    int acks_flood_received = 0;
    int acks_direct_pending = 0;
    int acks_direct_received = 0;
    // Per-destination send counts split by routing type (key = destination name)
    std::map<std::string, int> sent_flood_to;
    std::map<std::string, int> sent_direct_to;
    // Per-sender receive counts (key = sender name)
    std::map<std::string, int> recv_direct;   // direct messages received (any routing)
    int recv_group = 0;       // group/channel messages received
    std::map<std::string, int> recv_group_by_sender; // channel messages by sender name

    int totalSent() const { return sent_flood + sent_direct + sent_group; }
    int totalRecvDirect() const {
        int n = 0;
        for (auto& kv : recv_direct) n += kv.second;
        return n;
    }
    int acksPending() const { return acks_flood_pending + acks_direct_pending; }
    int acksReceived() const { return acks_flood_received + acks_direct_received; }
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

    // Structured contact access for companions (returns -1 / false for repeaters)
    virtual int getContactCount() const { return -1; }
    struct ContactData {
        char name[32];
        uint8_t type;          // ADV_TYPE_*
        uint8_t pubkey[32];
        uint8_t out_path_len;  // 0xFF = unknown
        uint32_t last_seen_s;  // seconds since last advert (by our clock)
    };
    virtual bool getContact(int idx, ContactData& out) const { return false; }
};
