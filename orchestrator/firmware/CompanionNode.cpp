// CompanionNode.cpp — factory for companion mesh nodes.
// Only this TU includes CompanionMyMesh.h (sees CompanionMyMesh + companion NodePrefs).

#include <string>
#include <vector>
#include <memory>
#include <cstring>
#include <map>

#include "NodeContext.h"

// Include renamed companion mesh — re-defines macros from target.h
#include "CompanionMyMesh.h"

#undef radio_driver
#undef rtc_clock
#undef Serial
#undef LittleFS

#include "MeshWrapper.h"
#include "SimSerial.h"

// HAS_SCOPE_SUPPORT is defined by CMake when the MeshCore tree has
// NodePrefs::default_scope_name (present on default-scope branch,
// absent on main/1.13).

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
    std::map<uint32_t, bool> _pending_acks;  // expected_ack CRC → true if flood, false if direct

    using CompanionMyMesh::CompanionMyMesh;  // inherit constructors

    void onMessageRecv(const ContactInfo& from, mesh::Packet* pkt,
                       uint32_t sender_timestamp, const char* text) override {
        if (_stats) _stats->recv_direct[from.name]++;
        CompanionMyMesh::onMessageRecv(from, pkt, sender_timestamp, text);
    }

    void onChannelMessageRecv(const mesh::GroupChannel& channel, mesh::Packet* pkt,
                              uint32_t timestamp, const char* text) override {
        if (_stats) {
            _stats->recv_group++;
            // Parse sender name from "sender: msg" format (set by sendGroupMessage)
            const char* sep = strstr(text, ": ");
            if (sep) {
                std::string sender(text, sep - text);
                _stats->recv_group_by_sender[sender]++;
            }
        }
        CompanionMyMesh::onChannelMessageRecv(channel, pkt, timestamp, text);
    }

    void onAckRecv(mesh::Packet* packet, uint32_t ack_crc) override {
        if (_stats) {
            auto it = _pending_acks.find(ack_crc);
            if (it != _pending_acks.end()) {
                if (it->second) _stats->acks_flood_received++;
                else _stats->acks_direct_received++;
                _pending_acks.erase(it);
            }
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
            auto it = _pending_acks.find(ack_crc);
            if (it != _pending_acks.end()) {
                if (it->second) _stats->acks_flood_received++;
                else _stats->acks_direct_received++;
                _pending_acks.erase(it);
            }
        }
        return CompanionMyMesh::onContactPathRecv(from, in_path, in_path_len,
                                                   out_path, out_path_len,
                                                   extra_type, extra, extra_len);
    }

    void trackAck(uint32_t expected_ack, bool is_flood) {
        _pending_acks[expected_ack] = is_flood;
    }

    // sendFloodWithScope: scope-aware on default-scope branch, plain sendFlood otherwise.
#ifdef HAS_SCOPE_SUPPORT
    void sendFloodWithScope(const TransportKey& scope, mesh::Packet* pkt, uint32_t delay_millis) {
        sendFloodScoped(scope, pkt, delay_millis);
    }
#else
    void sendFloodWithScope(const TransportKey&, mesh::Packet* pkt, uint32_t delay_millis) {
        sendFlood(pkt, delay_millis);
    }
#endif
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

    int getContactCount() const override {
        return const_cast<InstrumentedMesh&>(_mesh).getNumContacts();
    }

    bool getContact(int idx, ContactData& out) const override {
        ContactInfo ci;
        if (!const_cast<InstrumentedMesh&>(_mesh).getContactByIdx((uint32_t)idx, ci))
            return false;
        memcpy(out.name, ci.name, sizeof(out.name));
        out.type = ci.type;
        memcpy(out.pubkey, ci.id.pub_key, sizeof(out.pubkey));
        out.out_path_len = ci.out_path_len;
        uint32_t now = const_cast<InstrumentedMesh&>(_mesh).getRTCClock()->getCurrentTime();
        out.last_seen_s = (ci.lastmod > 0 && now >= ci.lastmod) ? (now - ci.lastmod) : 0;
        return true;
    }

    std::string handleCommand(uint32_t timestamp, const char* cmd) override {
#ifdef HAS_SCOPE_SUPPORT
        if (strncmp(cmd, "scope ", 6) == 0) {
            const char* name = cmd + 6;
            NodePrefs* prefs = _mesh.getNodePrefs();
            if (strlen(name) == 0 || strcmp(name, "none") == 0) {
                memset(prefs->default_scope_name, 0, sizeof(prefs->default_scope_name));
                memset(prefs->default_scope_key, 0, sizeof(prefs->default_scope_key));
                return "default scope cleared";
            }
            strncpy(prefs->default_scope_name, name, sizeof(prefs->default_scope_name) - 1);
            prefs->default_scope_name[sizeof(prefs->default_scope_name) - 1] = '\0';
            TransportKeyStore temp;
            TransportKey key;
            std::string hash_name = std::string("#") + name;
            temp.getAutoKeyFor(0, hash_name.c_str(), key);
            memcpy(prefs->default_scope_key, key.key, sizeof(key.key));
            return "default scope set to " + std::string(name);
        }
#endif
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
            if (!pkt) return "ERROR: createSelfAdvert failed";
#ifdef HAS_SCOPE_SUPPORT
            NodePrefs* prefs = _mesh.getNodePrefs();
            TransportKey scope;
            memcpy(&scope.key, prefs->default_scope_key, sizeof(scope.key));
            _mesh.sendFloodWithScope(scope, pkt, (uint32_t)0);
            return scope.isNull()
                ? "advert sent (flood)"
                : "advert sent (flood, scoped: " + std::string(prefs->default_scope_name) + ")";
#else
            _mesh.sendFlood(pkt, (uint32_t)0);
            return "advert sent (flood)";
#endif
        }
        if (strncmp(cmd, "msgc ", 5) == 0) {
            const char* text = cmd + 5;
            ChannelDetails ch;
            if (!_mesh.getChannel(0, ch))
                return "ERROR: no public channel configured";
            const char* sender_name = _mesh.getNodeName();
            bool ok = _mesh.sendGroupMessage(timestamp, ch.channel,
                                             sender_name, text, strlen(text));
            if (ok) {
                msg_stats.sent_group++;
                return "channel msg sent (flood)";
            }
            return "ERROR: sendGroupMessage failed";
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
                msg_stats.sent_flood_to[contact->name]++;
                _mesh.trackAck(expected_ack, true);
                msg_stats.acks_flood_pending++;
                return "msg sent to " + std::string(contact->name) + " (flood, ack tracked)";
            } else if (rc == MSG_SEND_SENT_DIRECT) {
                msg_stats.sent_direct++;
                msg_stats.sent_direct_to[contact->name]++;
                _mesh.trackAck(expected_ack, false);
                msg_stats.acks_direct_pending++;
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
                msg_stats.sent_flood_to[contact->name]++;
                return "msg sent to " + std::string(contact->name) + " (flood)";
            } else if (rc == MSG_SEND_SENT_DIRECT) {
                msg_stats.sent_direct++;
                msg_stats.sent_direct_to[contact->name]++;
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
        if (strncmp(cmd, "list", 4) == 0) {
            int last_n = 0;
            if (cmd[4] == ' ') last_n = atoi(&cmd[5]);
            int count = _mesh.getNumContacts();
            if (count == 0) return "no contacts";
            // Use scanRecentContacts for sorted output
            struct Visitor : public ContactVisitor {
                std::string result;
                uint32_t now;
                void onContactVisit(const ContactInfo& c) override {
                    if (!result.empty()) result += "\n";
                    result += c.name;
                    result += " - ";
                    int32_t secs = (int32_t)c.last_advert_timestamp - (int32_t)now;
                    char tmp[40];
                    AdvertTimeHelper::formatRelativeTimeDiff(tmp, secs, false);
                    result += tmp;
                }
            } visitor;
            visitor.now = _mesh.getRTCClock()->getCurrentTime();
            _mesh.scanRecentContacts(last_n, &visitor);
            return visitor.result;
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
            if (msg_stats.acks_flood_pending > 0) {
                r += "; flood_acks: " + std::to_string(msg_stats.acks_flood_received)
                   + "/" + std::to_string(msg_stats.acks_flood_pending);
            }
            if (msg_stats.acks_direct_pending > 0) {
                r += "; path_acks: " + std::to_string(msg_stats.acks_direct_received)
                   + "/" + std::to_string(msg_stats.acks_direct_pending);
            }
            return r;
        }
        if (strncmp(cmd, "reset_path ", 11) == 0 ||
            strncmp(cmd, "reset path ", 11) == 0) {
            const char* prefix = cmd + 11;
            ContactInfo* contact = _mesh.searchContactsByPrefix(prefix);
            if (!contact) return "ERROR: contact not found: " + std::string(prefix);
            _mesh.resetPathTo(*contact);
            return "path reset for " + std::string(contact->name);
        }
        if (strncmp(cmd, "path ", 5) == 0) {
            const char* prefix = cmd + 5;
            ContactInfo* contact = _mesh.searchContactsByPrefix(prefix);
            if (!contact) return "ERROR: contact not found: " + std::string(prefix);
            std::string r = std::string(contact->name) + ": ";
            if (contact->out_path_len == OUT_PATH_UNKNOWN) {
                r += "flood (no direct path)";
            } else if (contact->out_path_len == 0) {
                r += "direct (0 hops)";
            } else {
                r += "direct (" + std::to_string(contact->out_path_len) + " hops, path:";
                for (int i = 0; i < contact->out_path_len; i++) {
                    char hex[3];
                    snprintf(hex, sizeof(hex), "%02X", contact->out_path[i]);
                    r += " ";
                    r += hex;
                }
                r += ")";
            }
            return r;
        }
        if (strncmp(cmd, "disc_path ", 10) == 0) {
            const char* prefix = cmd + 10;
            ContactInfo* contact = _mesh.searchContactsByPrefix(prefix);
            if (!contact) return "ERROR: contact not found: " + std::string(prefix);
            // Path discovery: send a telemetry request as flood
            uint8_t req_data[9];
            req_data[0] = 0x03;  // REQ_TYPE_GET_TELEMETRY_DATA
            req_data[1] = ~(0x01);  // inverse permissions mask (BASE only)
            memset(&req_data[2], 0, 3);
            _mesh.getRNG()->random(&req_data[5], 4);
            auto save = contact->out_path_len;
            contact->out_path_len = OUT_PATH_UNKNOWN;  // force flood
            uint32_t tag, est_timeout;
            int result = _mesh.sendRequest(*contact, req_data, sizeof(req_data), tag, est_timeout);
            contact->out_path_len = save;
            if (result == MSG_SEND_FAILED)
                return "ERROR: send failed (table full)";
            return "path discovery sent to " + std::string(contact->name)
                   + " (" + (result == MSG_SEND_SENT_FLOOD ? "flood" : "direct") + ")";
        }
        if (strncmp(cmd, "clock", 5) == 0) {
            uint32_t now = _mesh.getRTCClock()->getCurrentTime();
            DateTime dt = DateTime(now);
            char buf[64];
            snprintf(buf, sizeof(buf), "%02d:%02d - %d/%d/%d UTC (epoch: %u)",
                     dt.hour(), dt.minute(), dt.day(), dt.month(), dt.year(), now);
            return buf;
        }
        if (strncmp(cmd, "ver", 3) == 0) {
            return "sim-companion v1.0 (MeshCore simulator)";
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
