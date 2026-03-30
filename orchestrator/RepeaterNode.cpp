// RepeaterNode.cpp — factory for repeater mesh nodes.
// Only this TU includes RepeaterMyMesh.h (sees RepeaterMyMesh + repeater NodePrefs).

#include <string>
#include <vector>
#include <memory>
#include <cstring>

#include "NodeContext.h"

// Include renamed repeater mesh — re-defines macros from target.h
#include "RepeaterMyMesh.h"

#undef radio_driver
#undef rtc_clock
#undef Serial
#undef LittleFS

#include "MeshWrapper.h"

namespace {

class RepeaterMeshWrapper : public MeshWrapper {
    RepeaterMyMesh _mesh;
public:
    RepeaterMeshWrapper(mesh::MainBoard& board, mesh::Radio& radio,
                        mesh::MillisecondClock& ms, mesh::RNG& rng,
                        mesh::RTCClock& rtc, mesh::MeshTables& tables)
        : _mesh(board, radio, ms, rng, rtc, tables) {}

    void init(mesh::RNG& rng, fs::FS& filesystem, const std::string& node_name) {
        IdentityStore id_store(filesystem, "/identity");
        id_store.begin();
        if (!id_store.load("node", _mesh.self_id)) {
            _mesh.self_id = mesh::LocalIdentity(&rng);
            id_store.save("node", _mesh.self_id);
        }
        _mesh.begin(&filesystem);
        NodePrefs* prefs = _mesh.getNodePrefs();
        strncpy(prefs->node_name, node_name.c_str(), sizeof(prefs->node_name) - 1);
        prefs->node_name[sizeof(prefs->node_name) - 1] = '\0';
    }

    void loop() override { _mesh.loop(); }
    const uint8_t* pubKey() override { return _mesh.self_id.pub_key; }

    std::vector<uint8_t> exportSelfAdvert() override {
        NodePrefs* prefs = _mesh.getNodePrefs();
        AdvertDataBuilder builder(ADV_TYPE_REPEATER, prefs->node_name);
        uint8_t app_data[MAX_ADVERT_DATA_SIZE];
        uint8_t app_data_len = builder.encodeTo(app_data);
        mesh::Packet* pkt = _mesh.createAdvert(_mesh.self_id, app_data, app_data_len);
        if (!pkt) return {};
        uint8_t buf[256];
        uint8_t len = pkt->writeTo(buf);
        _mesh.releasePacket(pkt);
        return std::vector<uint8_t>(buf, buf + len);
    }

    std::string handleCommand(uint32_t timestamp, const char* cmd) override {
        char cmd_buf[256];
        size_t len = strlen(cmd);
        if (len >= sizeof(cmd_buf)) len = sizeof(cmd_buf) - 1;
        memcpy(cmd_buf, cmd, len);
        cmd_buf[len] = '\0';

        char reply[512];
        reply[0] = '\0';
        _mesh.handleCommand(timestamp, cmd_buf, reply);
        return std::string(reply);
    }
};

} // anonymous namespace

std::unique_ptr<MeshWrapper> createRepeaterMesh(NodeContext& ctx) {
    auto w = std::make_unique<RepeaterMeshWrapper>(
        board, ctx.radio, *ctx.clock, ctx.rng, *ctx.clock, ctx.tables);
    w->init(ctx.rng, ctx.filesystem, ctx.name);
    return w;
}
