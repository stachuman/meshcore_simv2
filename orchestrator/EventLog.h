#pragma once

#include <cstdio>
#include <cstdint>
#include <string>
#include <functional>

// NDJSON event logger — writes one JSON object per line to stdout.
namespace EventLog {

// Compute 8-char hex packet fingerprint (FNV-1a hash)
void packetHashHex(char out[9], const uint8_t* data, int len);
// Compute raw uint32_t FNV-1a hash (for programmatic use)
uint32_t packetHash(const uint8_t* data, int len);

void simStart(unsigned long time_ms, int n_nodes, int step_ms,
              unsigned long warmup_ms = 0, bool hot_start = false);
void simEnd(unsigned long time_ms);
void nodeReady(unsigned long time_ms, const char* node, const char* role,
               const uint8_t* pub_key, int key_len,
               bool has_location = false, double lat = 0.0, double lon = 0.0,
               const char* firmware = nullptr);
void tx(unsigned long time_ms, const char* node, const uint8_t* data, int len, uint32_t airtime_ms);
void rx(unsigned long time_ms, const char* from, const char* to, float snr, float rssi,
        const uint8_t* data, int len, uint32_t airtime_ms = 0);
void cmdReply(unsigned long time_ms, const char* node, const char* command, const char* reply);
void collision(unsigned long time_ms, const char* from, const char* to, float snr, float rssi,
               const uint8_t* data, int len,
               const char* interferer = nullptr, float interferer_snr = 0.0f,
               float snr_margin = 0.0f);
void dropHalfDuplex(unsigned long time_ms, const char* from, const char* to,
                    const uint8_t* data, int len, uint32_t airtime_ms = 0);
void dropWeak(unsigned long time_ms, const char* from, const char* to, float snr, float threshold,
              const uint8_t* data, int len);
void dropLoss(unsigned long time_ms, const char* from, const char* to, float loss_prob,
              const uint8_t* data, int len);

// TX failure events
void txFail(unsigned long time_ms, const char* node, uint32_t count);

// Per-node stats (post-simulation)
void nodeStats(unsigned long time_ms, const char* node, const char* stats_type, const char* json_data);

// Adversarial events
void adversarialDrop(unsigned long time_ms, const char* node, const uint8_t* data, int len);
void adversarialCorrupt(unsigned long time_ms, const char* node, const uint8_t* data, int len,
                        int bits_flipped);
void adversarialReplay(unsigned long time_ms, const char* node, const uint8_t* data, int len,
                       unsigned long delay_ms);

// Lua callback event
void luaCallback(unsigned long time_ms, const char* fn_name);

// Event hook: if set, called with the raw NDJSON line for every event.
using EventHook = std::function<void(const std::string&)>;
void setEventHook(EventHook hook);

} // namespace EventLog
