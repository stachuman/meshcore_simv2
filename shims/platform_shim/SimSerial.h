#pragma once
#include <helpers/BaseSerialInterface.h>

// Stub serial interface: always disconnected, all frames discarded.
class SimSerial : public BaseSerialInterface {
    bool _enabled = false;
public:
    void enable() override { _enabled = true; }
    void disable() override { _enabled = false; }
    bool isEnabled() const override { return _enabled; }
    bool isConnected() const override { return false; }
    bool isWriteBusy() const override { return false; }
    size_t writeFrame(const uint8_t[], size_t) override { return 0; }
    size_t checkRecvFrame(uint8_t[]) override { return 0; }
};
