#pragma once
#include <MeshCore.h>
#include <stdlib.h>

// Stub MainBoard for simulation: fixed battery, NAN temp.
class SimBoard : public mesh::MainBoard {
public:
    uint16_t getBattMilliVolts() override { return 4200; }
    float getMCUTemperature() override { return NAN; }
    const char* getManufacturerName() const override { return "Simulator"; }
#ifdef ORCHESTRATOR_BUILD
    void reboot() override {}  // no-op: don't kill the multi-node process
#else
    void reboot() override { exit(0); }
#endif
    uint8_t getStartupReason() const override { return BD_STARTUP_NORMAL; }
};
