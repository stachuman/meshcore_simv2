#include "EventLog.h"
#include <cstring>

static const char HEX[] = "0123456789abcdef";
static void to_hex(char* out, const uint8_t* in, int len) {
    for (int i = 0; i < len; i++) {
        out[i*2]     = HEX[in[i] >> 4];
        out[i*2 + 1] = HEX[in[i] & 0x0F];
    }
    out[len*2] = '\0';
}

static void json_escape(char* out, size_t out_sz, const char* in) {
    char* dp = out;
    char* end = out + out_sz - 1;
    for (const char* sp = in; *sp && dp < end; sp++) {
        if (*sp == '"' || *sp == '\\') {
            if (dp + 1 >= end) break;
            *dp++ = '\\';
            *dp++ = *sp;
        } else if (*sp == '\n') {
            if (dp + 1 >= end) break;
            *dp++ = '\\'; *dp++ = 'n';
        } else if (*sp == '\t') {
            if (dp + 1 >= end) break;
            *dp++ = '\\'; *dp++ = 't';
        } else {
            *dp++ = *sp;
        }
    }
    *dp = '\0';
}

// Decode MeshCore packet header byte into short type label + route label
// Header byte: bits 1-0 = route type, bits 5-2 = payload type, bits 7-6 = version
static const char* decodePayloadType(uint8_t header) {
    uint8_t ptype = (header >> 2) & 0x0F;
    switch (ptype) {
        case 0x00: return "req";
        case 0x01: return "resp";
        case 0x02: return "msg";
        case 0x03: return "ack";
        case 0x04: return "advert";
        case 0x05: return "grp_msg";
        case 0x06: return "grp_data";
        case 0x07: return "anon_req";
        case 0x08: return "path";
        case 0x09: return "trace";
        case 0x0A: return "multipart";
        case 0x0B: return "control";
        case 0x0F: return "raw";
        default:   return "?";
    }
}

static const char* decodeRouteType(uint8_t header) {
    uint8_t rtype = header & 0x03;
    switch (rtype) {
        case 0x00: return "t_flood";
        case 0x01: return "flood";
        case 0x02: return "direct";
        case 0x03: return "t_direct";
        default:   return "?";
    }
}

namespace EventLog {

void packetHashHex(char out[9], const uint8_t* data, int len) {
    // FNV-1a 32-bit hash
    uint32_t h = 0x811c9dc5u;
    for (int i = 0; i < len; i++) {
        h ^= data[i];
        h *= 0x01000193u;
    }
    for (int i = 7; i >= 0; i--) {
        out[i] = HEX[h & 0x0F];
        h >>= 4;
    }
    out[8] = '\0';
}

void simStart(unsigned long time_ms, int n_nodes, int step_ms,
              unsigned long warmup_ms, bool hot_start) {
    fprintf(stdout, "{\"type\":\"sim_start\",\"time_ms\":%lu,\"n_nodes\":%d,\"step_ms\":%d,\"warmup_ms\":%lu,\"hot_start\":%s}\n",
            time_ms, n_nodes, step_ms, warmup_ms, hot_start ? "true" : "false");
}

void simEnd(unsigned long time_ms) {
    fprintf(stdout, "{\"type\":\"sim_end\",\"time_ms\":%lu}\n", time_ms);
}

void nodeReady(unsigned long time_ms, const char* node, const uint8_t* pub_key, int key_len,
               bool has_location, double lat, double lon) {
    char hex[128];
    to_hex(hex, pub_key, key_len);
    if (has_location) {
        fprintf(stdout, "{\"type\":\"node_ready\",\"time_ms\":%lu,\"node\":\"%s\",\"pub\":\"%s\",\"lat\":%.6f,\"lon\":%.6f}\n",
                time_ms, node, hex, lat, lon);
    } else {
        fprintf(stdout, "{\"type\":\"node_ready\",\"time_ms\":%lu,\"node\":\"%s\",\"pub\":\"%s\"}\n",
                time_ms, node, hex);
    }
}

void tx(unsigned long time_ms, const char* node, const uint8_t* data, int len, uint32_t airtime_ms) {
    char hex[512 * 2 + 1];
    char pkt[9];
    if (len > 512) len = 512;
    to_hex(hex, data, len);
    packetHashHex(pkt, data, len);
    const char* pt = len > 0 ? decodePayloadType(data[0]) : "?";
    const char* rt = len > 0 ? decodeRouteType(data[0]) : "?";
    fprintf(stdout, "{\"type\":\"tx\",\"time_ms\":%lu,\"node\":\"%s\",\"pkt\":\"%s\",\"pkt_type\":\"%s\",\"route\":\"%s\",\"hex\":\"%s\",\"airtime_ms\":%u}\n",
            time_ms, node, pkt, pt, rt, hex, (unsigned)airtime_ms);
}

void rx(unsigned long time_ms, const char* from, const char* to, float snr, float rssi,
        const uint8_t* data, int len, uint32_t airtime_ms) {
    char pkt[9];
    packetHashHex(pkt, data, len);
    const char* pt = len > 0 ? decodePayloadType(data[0]) : "?";
    const char* rt = len > 0 ? decodeRouteType(data[0]) : "?";
    if (airtime_ms > 0) {
        fprintf(stdout, "{\"type\":\"rx\",\"time_ms\":%lu,\"from\":\"%s\",\"to\":\"%s\",\"snr\":%.1f,\"rssi\":%.1f,\"pkt\":\"%s\",\"pkt_type\":\"%s\",\"route\":\"%s\",\"airtime_ms\":%u}\n",
                time_ms, from, to, snr, rssi, pkt, pt, rt, (unsigned)airtime_ms);
    } else {
        fprintf(stdout, "{\"type\":\"rx\",\"time_ms\":%lu,\"from\":\"%s\",\"to\":\"%s\",\"snr\":%.1f,\"rssi\":%.1f,\"pkt\":\"%s\",\"pkt_type\":\"%s\",\"route\":\"%s\"}\n",
                time_ms, from, to, snr, rssi, pkt, pt, rt);
    }
}

void cmdReply(unsigned long time_ms, const char* node, const char* command, const char* reply) {
    char esc_cmd[512], esc_reply[1024];
    json_escape(esc_cmd, sizeof(esc_cmd), command);
    json_escape(esc_reply, sizeof(esc_reply), reply);
    fprintf(stdout, "{\"type\":\"cmd_reply\",\"time_ms\":%lu,\"node\":\"%s\",\"command\":\"%s\",\"reply\":\"%s\"}\n",
            time_ms, node, esc_cmd, esc_reply);
}

void collision(unsigned long time_ms, const char* from, const char* to, float snr, float rssi,
               const uint8_t* data, int len,
               const char* interferer, float interferer_snr, float snr_margin) {
    char pkt[9];
    packetHashHex(pkt, data, len);
    const char* pt = len > 0 ? decodePayloadType(data[0]) : "?";
    const char* rt = len > 0 ? decodeRouteType(data[0]) : "?";
    if (interferer) {
        fprintf(stdout, "{\"type\":\"collision\",\"time_ms\":%lu,\"from\":\"%s\",\"to\":\"%s\",\"snr\":%.1f,\"rssi\":%.1f,\"pkt\":\"%s\",\"pkt_type\":\"%s\",\"route\":\"%s\",\"interferer\":\"%s\",\"interferer_snr\":%.1f,\"snr_margin\":%.1f}\n",
                time_ms, from, to, snr, rssi, pkt, pt, rt, interferer, interferer_snr, snr_margin);
    } else {
        fprintf(stdout, "{\"type\":\"collision\",\"time_ms\":%lu,\"from\":\"%s\",\"to\":\"%s\",\"snr\":%.1f,\"rssi\":%.1f,\"pkt\":\"%s\",\"pkt_type\":\"%s\",\"route\":\"%s\"}\n",
                time_ms, from, to, snr, rssi, pkt, pt, rt);
    }
}

void dropHalfDuplex(unsigned long time_ms, const char* from, const char* to,
                    const uint8_t* data, int len, uint32_t airtime_ms) {
    char pkt[9];
    packetHashHex(pkt, data, len);
    const char* pt = len > 0 ? decodePayloadType(data[0]) : "?";
    const char* rt = len > 0 ? decodeRouteType(data[0]) : "?";
    if (airtime_ms > 0) {
        fprintf(stdout, "{\"type\":\"drop_halfduplex\",\"time_ms\":%lu,\"from\":\"%s\",\"to\":\"%s\",\"pkt\":\"%s\",\"pkt_type\":\"%s\",\"route\":\"%s\",\"airtime_ms\":%u}\n",
                time_ms, from, to, pkt, pt, rt, (unsigned)airtime_ms);
    } else {
        fprintf(stdout, "{\"type\":\"drop_halfduplex\",\"time_ms\":%lu,\"from\":\"%s\",\"to\":\"%s\",\"pkt\":\"%s\",\"pkt_type\":\"%s\",\"route\":\"%s\"}\n",
                time_ms, from, to, pkt, pt, rt);
    }
}

void dropWeak(unsigned long time_ms, const char* from, const char* to, float snr, float threshold,
              const uint8_t* data, int len) {
    char pkt[9];
    packetHashHex(pkt, data, len);
    const char* pt = len > 0 ? decodePayloadType(data[0]) : "?";
    const char* rt = len > 0 ? decodeRouteType(data[0]) : "?";
    fprintf(stdout, "{\"type\":\"drop_weak\",\"time_ms\":%lu,\"from\":\"%s\",\"to\":\"%s\",\"snr\":%.1f,\"threshold\":%.1f,\"pkt\":\"%s\",\"pkt_type\":\"%s\",\"route\":\"%s\"}\n",
            time_ms, from, to, snr, threshold, pkt, pt, rt);
}

void dropLoss(unsigned long time_ms, const char* from, const char* to, float loss_prob,
              const uint8_t* data, int len) {
    char pkt[9];
    packetHashHex(pkt, data, len);
    const char* pt = len > 0 ? decodePayloadType(data[0]) : "?";
    const char* rt = len > 0 ? decodeRouteType(data[0]) : "?";
    fprintf(stdout, "{\"type\":\"drop_loss\",\"time_ms\":%lu,\"from\":\"%s\",\"to\":\"%s\",\"loss\":%.3f,\"pkt\":\"%s\",\"pkt_type\":\"%s\",\"route\":\"%s\"}\n",
            time_ms, from, to, loss_prob, pkt, pt, rt);
}

void txFail(unsigned long time_ms, const char* node, uint32_t count) {
    fprintf(stdout, "{\"type\":\"tx_fail\",\"time_ms\":%lu,\"node\":\"%s\",\"count\":%u}\n",
            time_ms, node, (unsigned)count);
}

void adversarialDrop(unsigned long time_ms, const char* node, const uint8_t* data, int len) {
    char pkt[9];
    packetHashHex(pkt, data, len);
    const char* pt = len > 0 ? decodePayloadType(data[0]) : "?";
    const char* rt = len > 0 ? decodeRouteType(data[0]) : "?";
    fprintf(stdout, "{\"type\":\"adversarial_drop\",\"time_ms\":%lu,\"node\":\"%s\",\"pkt\":\"%s\",\"pkt_type\":\"%s\",\"route\":\"%s\"}\n",
            time_ms, node, pkt, pt, rt);
}

void adversarialCorrupt(unsigned long time_ms, const char* node, const uint8_t* data, int len,
                        int bits_flipped) {
    char pkt[9];
    packetHashHex(pkt, data, len);
    const char* pt = len > 0 ? decodePayloadType(data[0]) : "?";
    const char* rt = len > 0 ? decodeRouteType(data[0]) : "?";
    fprintf(stdout, "{\"type\":\"adversarial_corrupt\",\"time_ms\":%lu,\"node\":\"%s\",\"pkt\":\"%s\",\"pkt_type\":\"%s\",\"route\":\"%s\",\"bits_flipped\":%d}\n",
            time_ms, node, pkt, pt, rt, bits_flipped);
}

void adversarialReplay(unsigned long time_ms, const char* node, const uint8_t* data, int len,
                       unsigned long delay_ms) {
    char pkt[9];
    packetHashHex(pkt, data, len);
    const char* pt = len > 0 ? decodePayloadType(data[0]) : "?";
    const char* rt = len > 0 ? decodeRouteType(data[0]) : "?";
    fprintf(stdout, "{\"type\":\"adversarial_replay\",\"time_ms\":%lu,\"node\":\"%s\",\"pkt\":\"%s\",\"pkt_type\":\"%s\",\"route\":\"%s\",\"delay_ms\":%lu}\n",
            time_ms, node, pkt, pt, rt, delay_ms);
}

} // namespace EventLog
