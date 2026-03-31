// CompanionNode.cpp — factory for companion mesh nodes.
// Only this TU includes CompanionMyMesh.h (sees CompanionMyMesh + companion NodePrefs).

#include <string>
#include <vector>
#include <memory>
#include <cstring>
#include <set>

#include "NodeContext.h"

// Include renamed companion mesh — re-defines macros from target.h
#include "CompanionMyMesh.h"

#undef radio_driver
#undef rtc_clock
#undef Serial
#undef LittleFS

#include "MeshWrapper.h"
#include "SimSerial.h"

namespace {

static int hex_to_bytes(uint8_t* out, const char* hex, size_t hex_len) {
    if (hex_len % 2 != 0) return 0;
    int n = (int)(hex_len / 2);
    for (int i = 0; i < n; i++) {
        auto nib = [](char c) -> uint8_t {
            if (c >= '0' && c <= '9') return c - '0';
            if (c >= 'a' && c <= 'f') return c - 'a' + 10;
            if (c >= 'A' && c <= 'F') return c - 'A' + 10;
            return 0;
        };
        out[i] = (nib(hex[i*2]) << 4) | nib(hex[i*2+1]);
    }
    return n;
}

// Thin subclass that intercepts receive callbacks for statistics.
class InstrumentedMesh : public CompanionMyMesh {
public:
    MsgStats* _stats = nullptr;   // set after construction
    std::set<uint32_t> _pending_acks;  // expected_ack CRCs we're waiting for

    using CompanionMyMesh::CompanionMyMesh;  // inherit constructors

    void onMessageRecv(const ContactInfo& from, mesh::Packet* pkt,
                       uint32_t sender_timestamp, const char* text) override {
        if (_stats) _stats->recv_direct[from.name]++;
        CompanionMyMesh::onMessageRecv(from, pkt, sender_timestamp, text);
    }

    void onChannelMessageRecv(const mesh::GroupChannel& channel, mesh::Packet* pkt,
                              uint32_t timestamp, const char* text) override {
        if (_stats) _stats->recv_group++;
        CompanionMyMesh::onChannelMessageRecv(channel, pkt, timestamp, text);
    }

    void onAckRecv(mesh::Packet* packet, uint32_t ack_crc) override {
        if (_stats && _pending_acks.erase(ack_crc) > 0) {
            _stats->acks_received++;
        }
        CompanionMyMesh::onAckRecv(packet, ack_crc);
    }

    // Flood messages embed ACK in PATH return — intercept here too.
    bool onContactPathRecv(ContactInfo& from, uint8_t* in_path, uint8_t in_path_len,
                           uint8_t* out_path, uint8_t out_path_len,
                           uint8_t extra_type, uint8_t* extra, uint8_t extra_len) override {
        if (_stats && extra_type == PAYLOAD_TYPE_ACK && extra_len >= 4) {
            uint32_t ack_crc;
            memcpy(&ack_crc, extra, 4);
            if (_pending_acks.erase(ack_crc) > 0) {
                _stats->acks_received++;
            }
        }
        return CompanionMyMesh::onContactPathRecv(from, in_path, in_path_len,
                                                   out_path, out_path_len,
                                                   extra_type, extra, extra_len);
    }

    void trackAck(uint32_t expected_ack) {
        _pending_acks.insert(expected_ack);
    }
};

class CompanionMeshWrapper : public MeshWrapper {
    DataStore _store;
    SimSerial _serial;
    InstrumentedMesh _mesh;
public:
    CompanionMeshWrapper(mesh::Radio& radio, mesh::RNG& rng,
                         mesh::RTCClock& rtc, SimpleMeshTables& tables,
                         fs::FS& filesystem)
        : _store(filesystem, rtc),
          _mesh(radio, rng, rtc, tables, _store, nullptr) {
        _mesh._stats = &msg_stats;
    }

    void init(mesh::RNG& rng, const std::string& node_name) {
        _mesh.self_id = mesh::LocalIdentity(&rng);
        _store.begin();
        _store.saveMainIdentity(_mesh.self_id);
        _mesh.begin(false);
        // Set node name from orchestrator config (overrides default hex pub key)
        NodePrefs* prefs = _mesh.getNodePrefs();
        strncpy(prefs->node_name, node_name.c_str(), sizeof(prefs->node_name) - 1);
        prefs->node_name[sizeof(prefs->node_name) - 1] = '\0';
        _mesh.startInterface(_serial);
    }

    void loop() override { _mesh.loop(); }
    const uint8_t* pubKey() override { return _mesh.self_id.pub_key; }

    std::vector<uint8_t> exportSelfAdvert() override {
        mesh::Packet* pkt = _mesh.createSelfAdvert(_mesh.getNodeName());
        if (!pkt) return {};
        uint8_t buf[256];
        uint8_t len = pkt->writeTo(buf);
        _mesh.releasePacket(pkt);
        return std::vector<uint8_t>(buf, buf + len);
    }

    std::string handleCommand(uint32_t timestamp, const char* cmd) override {
        if (strncmp(cmd, "advert.zerohop", 14) == 0) {
            mesh::Packet* pkt = _mesh.createSelfAdvert(_mesh.getNodeName());
            if (pkt) {
                _mesh.sendZeroHop(pkt);
                return "advert sent (zero-hop)";
            }
            return "ERROR: createSelfAdvert failed";
        }
        if (strncmp(cmd, "advert", 6) == 0) {
            mesh::Packet* pkt = _mesh.createSelfAdvert(_mesh.getNodeName());
            if (pkt) {
                _mesh.sendFlood(pkt);
                return "advert sent (flood)";
            }
            return "ERROR: createSelfAdvert failed";
        }
        if (strncmp(cmd, "msga ", 5) == 0) {
            const char* rest = cmd + 5;
            // Parse: msga <name_prefix> <text>  — message with ack tracking
            const char* space = strchr(rest, ' ');
            if (!space) return "ERROR: usage: msga <name> <text>";
            std::string prefix(rest, space - rest);
            const char* text = space + 1;
            ContactInfo* contact = _mesh.searchContactsByPrefix(prefix.c_str());
            if (!contact) return "ERROR: contact not found: " + prefix;
            uint32_t expected_ack = 0, est_timeout = 0;
            int rc = _mesh.sendMessage(*contact, timestamp, 0, text, expected_ack, est_timeout);
            if (rc == MSG_SEND_SENT_FLOOD) {
                msg_stats.sent_flood++;
                msg_stats.sent_to[contact->name]++;
                _mesh.trackAck(expected_ack);
                msg_stats.acks_pending++;
                return "msg sent to " + std::string(contact->name) + " (flood, ack tracked)";
            } else if (rc == MSG_SEND_SENT_DIRECT) {
                msg_stats.sent_direct++;
                msg_stats.sent_to[contact->name]++;
                _mesh.trackAck(expected_ack);
                msg_stats.acks_pending++;
                return "msg sent to " + std::string(contact->name) + " (direct, ack tracked)";
            }
            return "ERROR: sendMessage failed (rc=" + std::to_string(rc) + ")";
        }
        if (strncmp(cmd, "msg ", 4) == 0) {
            const char* rest = cmd + 4;
            // Parse: msg <name_prefix> <text>
            const char* space = strchr(rest, ' ');
            if (!space) return "ERROR: usage: msg <name> <text>";
            std::string prefix(rest, space - rest);
            const char* text = space + 1;
            ContactInfo* contact = _mesh.searchContactsByPrefix(prefix.c_str());
            if (!contact) return "ERROR: contact not found: " + prefix;
            uint32_t expected_ack = 0, est_timeout = 0;
            int rc = _mesh.sendMessage(*contact, timestamp, 0, text, expected_ack, est_timeout);
            if (rc == MSG_SEND_SENT_FLOOD) {
                msg_stats.sent_flood++;
                msg_stats.sent_to[contact->name]++;
                return "msg sent to " + std::string(contact->name) + " (flood)";
            } else if (rc == MSG_SEND_SENT_DIRECT) {
                msg_stats.sent_direct++;
                msg_stats.sent_to[contact->name]++;
                return "msg sent to " + std::string(contact->name) + " (direct)";
            }
            return "ERROR: sendMessage failed (rc=" + std::to_string(rc) + ")";
        }
        if (strncmp(cmd, "import ", 7) == 0) {
            const char* hex = cmd + 7;
            size_t hex_len = strlen(hex);
            uint8_t buf[256];
            int n = hex_to_bytes(buf, hex, hex_len);
            if (n <= 0) return "ERROR: invalid hex";
            bool ok = _mesh.importContact(buf, (uint8_t)n);
            return ok ? "contact imported" : "ERROR: importContact failed";
        }
        if (strncmp(cmd, "neighbors", 9) == 0) {
            int count = _mesh.getNumContacts();
            if (count == 0) return "no contacts";
            std::string result;
            for (int i = 0; i < count; i++) {
                ContactInfo ci;
                if (_mesh.getContactByIdx(i, ci)) {
                    if (!result.empty()) result += ", ";
                    result += ci.name;
                }
            }
            return std::to_string(count) + " contacts: " + result;
        }
        if (strncmp(cmd, "stats", 5) == 0) {
            std::string r = "sent: " + std::to_string(msg_stats.sent_flood) + " flood, "
                          + std::to_string(msg_stats.sent_direct) + " direct, "
                          + std::to_string(msg_stats.sent_group) + " group; "
                          + "recv: " + std::to_string(msg_stats.totalRecvDirect()) + " direct";
            if (!msg_stats.recv_direct.empty()) {
                r += " (";
                bool first = true;
                for (auto& kv : msg_stats.recv_direct) {
                    if (!first) r += ", ";
                    r += kv.first + ":" + std::to_string(kv.second);
                    first = false;
                }
                r += ")";
            }
            r += ", " + std::to_string(msg_stats.recv_group) + " group";
            if (msg_stats.acks_pending > 0) {
                r += "; acks: " + std::to_string(msg_stats.acks_received)
                   + "/" + std::to_string(msg_stats.acks_pending);
            }
            return r;
        }
        return "ERROR: unknown command: " + std::string(cmd);
    }
};

} // anonymous namespace

std::unique_ptr<MeshWrapper> createCompanionMesh(NodeContext& ctx) {
    auto w = std::make_unique<CompanionMeshWrapper>(
        ctx.radio, ctx.rng, ctx.own_clock, ctx.tables, ctx.filesystem);
    w->init(ctx.rng, ctx.name);
    return w;
}
