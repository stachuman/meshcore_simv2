// simple_repeater/main.cpp
//
// JSON stdin/stdout wrapper for the MeshCore simple_repeater MyMesh.
// This compiles the real firmware repeater code against Linux shims.
//
// Usage:
//   simple_repeater [--name <str>] [--prv <64-byte-hex>] [--sf N] [--bw N] [--cr N]

#include "SimRadio.h"
#include "SimClock.h"
#include "SimRNG.h"
#include "SimBoard.h"

#include <helpers/SimpleMeshTables.h>
#include <helpers/SensorManager.h>
#include <helpers/IdentityStore.h>

#include "MyMesh.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <sys/select.h>
#include <sys/stat.h>
#include <unistd.h>
#include <errno.h>
#include <new>

// ---- Global storage for objects required by target.h externs ----
static SimBoard g_board;
static SimClock g_clock;
static SensorManager g_sensors;

// SimRadio needs a clock reference, so we use aligned storage + placement new.
alignas(SimRadio) static char g_radio_storage[sizeof(SimRadio)];
static SimRadio* g_radio_ptr = nullptr;

// target.h externs
mesh::MainBoard& board = g_board;
SimRadio& radio_driver = *reinterpret_cast<SimRadio*>(g_radio_storage);
mesh::RTCClock& rtc_clock = g_clock;
SensorManager& sensors = g_sensors;

// ---- Minimal JSON helpers ----
static const char* json_str_field(const char* json, const char* key, size_t* out_len) {
    char search[64];
    snprintf(search, sizeof(search), "\"%s\":", key);
    const char* p = strstr(json, search);
    if (!p) return nullptr;
    p += strlen(search);
    while (*p == ' ') p++;
    if (*p != '"') return nullptr;
    p++;
    const char* start = p;
    while (*p && *p != '"') {
        if (*p == '\\') p++;
        if (*p) p++;
    }
    if (out_len) *out_len = (size_t)(p - start);
    return start;
}

static double json_num_field(const char* json, const char* key, double def = 0.0) {
    char search[64];
    snprintf(search, sizeof(search), "\"%s\":", key);
    const char* p = strstr(json, search);
    if (!p) return def;
    p += strlen(search);
    while (*p == ' ') p++;
    return strtod(p, nullptr);
}

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

static void bytes_to_hex_main(char* out, const uint8_t* in, size_t len) {
    static const char H[] = "0123456789abcdef";
    for (size_t i = 0; i < len; i++) {
        out[i*2]     = H[in[i] >> 4];
        out[i*2 + 1] = H[in[i] & 0x0f];
    }
    out[len*2] = '\0';
}

// ---- Command dispatch ----
static void dispatch(const char* line, SimRadio& radio, SimClock& clock, MyMesh& mesh) {
    size_t type_len = 0;
    const char* type_val = json_str_field(line, "type", &type_len);
    if (!type_val) return;

    if (type_len == 2 && strncmp(type_val, "rx", 2) == 0) {
        size_t hex_len = 0;
        const char* hex = json_str_field(line, "hex", &hex_len);
        if (!hex || hex_len == 0) return;
        uint8_t buf[MAX_TRANS_UNIT + 1];
        int n = hex_to_bytes(buf, hex, hex_len);
        if (n <= 0) return;
        float snr  = (float)json_num_field(line, "snr",  6.0);
        float rssi = (float)json_num_field(line, "rssi", -90.0);
        radio.enqueue(buf, n, snr, rssi);

    } else if (type_len == 8 && strncmp(type_val, "rx_start", 8) == 0) {
        uint32_t dur = (uint32_t)json_num_field(line, "duration_ms");
        radio.notifyRxStart(dur);

    } else if (type_len == 4 && strncmp(type_val, "time", 4) == 0) {
        uint32_t epoch = (uint32_t)json_num_field(line, "epoch");
        if (epoch > 0) clock.setCurrentTime(epoch);

    } else if (type_len == 3 && strncmp(type_val, "cmd", 3) == 0) {
        size_t cmd_len = 0;
        const char* cmd = json_str_field(line, "command", &cmd_len);
        if (!cmd || cmd_len == 0) return;
        char cmd_buf[256];
        size_t copy_len = cmd_len < sizeof(cmd_buf) - 1 ? cmd_len : sizeof(cmd_buf) - 1;
        memcpy(cmd_buf, cmd, copy_len);
        cmd_buf[copy_len] = '\0';
        uint32_t ts = (uint32_t)json_num_field(line, "timestamp", 0);
        char reply[512];
        reply[0] = '\0';
        mesh.handleCommand(ts, cmd_buf, reply);
        // Escape reply for JSON output
        char escaped[1024];
        char* dp = escaped;
        for (const char* sp = reply; *sp && dp < escaped + sizeof(escaped) - 2; sp++) {
            if (*sp == '"' || *sp == '\\') *dp++ = '\\';
            else if (*sp == '\n') { *dp++ = '\\'; *dp++ = 'n'; continue; }
            else if (*sp == '\t') { *dp++ = '\\'; *dp++ = 't'; continue; }
            *dp++ = *sp;
        }
        *dp = '\0';
        fprintf(stdout, "{\"type\":\"cmd_reply\",\"reply\":\"%s\"}\n", escaped);
        fflush(stdout);

    } else if (type_len == 4 && strncmp(type_val, "quit", 4) == 0) {
        exit(0);
    }
}

// ---- main ----
int main(int argc, char* argv[]) {
    const char* node_name = "repeater";
    const char* prv_hex   = nullptr;
    int radio_sf  = 8;
    int radio_bw  = 62500;
    int radio_cr  = 4;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--name") == 0 && i + 1 < argc) {
            node_name = argv[++i];
        } else if (strcmp(argv[i], "--prv") == 0 && i + 1 < argc) {
            prv_hex = argv[++i];
        } else if (strcmp(argv[i], "--sf") == 0 && i + 1 < argc) {
            radio_sf = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--bw") == 0 && i + 1 < argc) {
            radio_bw = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--cr") == 0 && i + 1 < argc) {
            radio_cr = atoi(argv[++i]);
        }
    }

    // Set up global clock
    sim_clock_set_global(&g_clock);

    // Construct radio in-place (must happen before anything uses radio_driver)
    g_radio_ptr = new (g_radio_storage) SimRadio(g_clock, radio_sf, radio_bw, radio_cr);

    // Initialize filesystem with per-node root
    char fs_root[256];
    snprintf(fs_root, sizeof(fs_root), "/tmp/meshsim_%s", node_name);
    LittleFS.begin(fs_root);

    // RNG + identity
    SimRNG rng;
    if (prv_hex && strlen(prv_hex) == PRV_KEY_SIZE * 2) {
        uint8_t prv[PRV_KEY_SIZE];
        hex_to_bytes(prv, prv_hex, PRV_KEY_SIZE * 2);
        rng.seed(prv, PRV_KEY_SIZE);
    } else {
        rng.seed((const uint8_t*)node_name, strlen(node_name));
    }

    // Create mesh tables
    SimpleMeshTables tables;

    // Create the repeater MyMesh
    MyMesh mesh(g_board, *g_radio_ptr, g_clock, rng, g_clock, tables);

    // Load or generate identity
    IdentityStore id_store(LittleFS, "/identity");
    id_store.begin();
    if (!id_store.load("node", mesh.self_id)) {
        if (prv_hex && strlen(prv_hex) == PRV_KEY_SIZE * 2) {
            uint8_t prv[PRV_KEY_SIZE];
            hex_to_bytes(prv, prv_hex, PRV_KEY_SIZE * 2);
            mesh.self_id.readFrom(prv, PRV_KEY_SIZE);
        } else {
            mesh.self_id = mesh::LocalIdentity(&rng);
        }
        id_store.save("node", mesh.self_id);
    }

    // Begin mesh
    mesh.begin(&LittleFS);

    // Emit ready
    char pub_hex[PUB_KEY_SIZE * 2 + 1];
    bytes_to_hex_main(pub_hex, mesh.self_id.pub_key, PUB_KEY_SIZE);
    fprintf(stdout,
            "{\"type\":\"ready\",\"pub\":\"%s\",\"role\":\"repeater\",\"name\":\"%s\"}\n",
            pub_hex, node_name);
    fflush(stdout);

    // Main loop
    char line_buf[4096];
    int  line_pos = 0;

    while (true) {
        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(STDIN_FILENO, &rfds);
        struct timeval tv { .tv_sec = 0, .tv_usec = 1000 };

        int ret = select(STDIN_FILENO + 1, &rfds, nullptr, nullptr, &tv);
        if (ret < 0 && errno != EINTR) break;

        if (ret > 0 && FD_ISSET(STDIN_FILENO, &rfds)) {
            char c;
            ssize_t n;
            while ((n = read(STDIN_FILENO, &c, 1)) == 1) {
                if (c == '\n') {
                    if (line_pos > 0) {
                        line_buf[line_pos] = '\0';
                        dispatch(line_buf, *g_radio_ptr, g_clock, mesh);
                        line_pos = 0;
                    }
                } else if (line_pos < (int)sizeof(line_buf) - 1) {
                    line_buf[line_pos++] = c;
                }
                fd_set probe;
                FD_ZERO(&probe);
                FD_SET(STDIN_FILENO, &probe);
                struct timeval zero { 0, 0 };
                if (select(STDIN_FILENO + 1, &probe, nullptr, nullptr, &zero) <= 0)
                    break;
            }
            if (n == 0) break; // EOF
        }

        g_clock.tick();
        mesh.loop();
    }

    return 0;
}
