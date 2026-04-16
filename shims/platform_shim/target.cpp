// Stub implementations for target.h function declarations.
// These are normally provided by platform-specific code in MeshCore/arch/*.

#include <target.h>
#include "SimRNG.h"
#include <ctime>

#ifdef ORCHESTRATOR_BUILD
// Context pointers for orchestrator context switching
SimRadio*       _ctx_radio   = nullptr;
mesh::RTCClock* _ctx_rtc     = nullptr;

// Per-node Arduino/firmware seed. Zero until NodeContext::activate() runs
// for a real node; radio_get_rng_seed() falls back to a nonzero sentinel in
// that case (e.g. during standalone unit harnesses that never call activate).
uint32_t _ctx_arduino_seed = 0;

// Proxy objects for board and sensors
BoardProxy  board;
SensorProxy sensors;
#endif

bool radio_init() {
    return true;
}

uint32_t radio_get_rng_seed() {
#ifdef ORCHESTRATOR_BUILD
    // Use the per-node seed set by NodeContext::activate(). Different nodes
    // see different seeds, different simulation.seed values see different
    // sequences, and the value is deterministic for a given (global_seed,
    // node_name) pair so runs remain reproducible.
    if (_ctx_arduino_seed != 0) return _ctx_arduino_seed;
#endif
    // Standalone builds / not-yet-activated paths: fall back to a
    // time-derived seed so at least the firmware init isn't a constant.
    return static_cast<uint32_t>(time(nullptr));
}

void radio_set_params(float freq, float bw, uint8_t sf, uint8_t cr) {
    (void)freq; (void)bw; (void)sf; (void)cr;
}

void radio_set_tx_power(int8_t power_dbm) {
    (void)power_dbm;
}

mesh::LocalIdentity radio_new_identity() {
    // Use a temporary RNG seeded from current time
    SimRNG rng;
    rng.seed((uint64_t)time(nullptr));
    return mesh::LocalIdentity(&rng);
}
