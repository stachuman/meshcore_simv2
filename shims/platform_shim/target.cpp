// Stub implementations for target.h function declarations.
// These are normally provided by platform-specific code in MeshCore/arch/*.

#include <target.h>
#include "SimRNG.h"
#include <ctime>

#ifdef ORCHESTRATOR_BUILD
// Context pointers for orchestrator context switching
SimRadio*       _ctx_radio   = nullptr;
mesh::RTCClock* _ctx_rtc     = nullptr;

// Proxy objects for board and sensors
BoardProxy  board;
SensorProxy sensors;
#endif

bool radio_init() {
    return true;
}

uint32_t radio_get_rng_seed() {
    return 42;
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
