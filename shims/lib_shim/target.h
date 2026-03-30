#pragma once
// Global externs and function declarations normally provided by platform-specific targets.
// In the simulator, these are backed by Sim* classes created in main.cpp.

#include <MeshCore.h>
#include <Identity.h>
#include <helpers/SensorManager.h>
#include "SimRadio.h"

#ifdef ORCHESTRATOR_BUILD
#include "SimBoard.h"

// For radio_driver and rtc_clock: names don't collide with MeshCore parameter
// names, so we can safely use macros over swappable pointers.
extern SimRadio*       _ctx_radio;
extern mesh::RTCClock* _ctx_rtc;
#define radio_driver  (*_ctx_radio)
#define rtc_clock     (*_ctx_rtc)

// For board and sensors: names collide with parameter names in MeshCore headers
// (CommonCLI.h, StatsFormatHelper.h, MyMesh.h), so we use proxy objects that
// delegate to a swappable target pointer.
class BoardProxy : public mesh::MainBoard {
public:
    SimBoard* _target = nullptr;
    uint16_t getBattMilliVolts() override { return _target->getBattMilliVolts(); }
    float getMCUTemperature() override { return _target->getMCUTemperature(); }
    const char* getManufacturerName() const override { return _target->getManufacturerName(); }
    void reboot() override { _target->reboot(); }
    uint8_t getStartupReason() const override { return _target->getStartupReason(); }
};

class SensorProxy : public SensorManager {
public:
    SensorManager* _target = nullptr;
    bool begin() override { return _target ? _target->begin() : false; }
    bool querySensors(uint8_t p, CayenneLPP& t) override { return _target ? _target->querySensors(p, t) : false; }
    void loop() override { if (_target) _target->loop(); }
    int getNumSettings() const override { return _target ? _target->getNumSettings() : 0; }
    const char* getSettingName(int i) const override { return _target ? _target->getSettingName(i) : nullptr; }
    const char* getSettingValue(int i) const override { return _target ? _target->getSettingValue(i) : nullptr; }
    bool setSettingValue(const char* name, const char* value) override { return _target ? _target->setSettingValue(name, value) : false; }
    LocationProvider* getLocationProvider() override { return _target ? _target->getLocationProvider() : nullptr; }
};

extern BoardProxy  board;
extern SensorProxy sensors;

#else
  extern mesh::MainBoard& board;
  extern SimRadio& radio_driver;
  extern mesh::RTCClock& rtc_clock;
  extern SensorManager& sensors;
#endif

// Radio control functions (stubs in target.cpp)
bool radio_init();
uint32_t radio_get_rng_seed();
void radio_set_params(float freq, float bw, uint8_t sf, uint8_t cr);
void radio_set_tx_power(int8_t power_dbm);
mesh::LocalIdentity radio_new_identity();
