#pragma once
#include "SimClock.h"

// Orchestrator-controlled clock: getMillis()/getCurrentTime() return
// values driven by advanceMillis() rather than wall-time.
class VirtualClock : public SimClock {
    unsigned long _virtual_millis = 0;
    uint32_t _epoch_base;

public:
    explicit VirtualClock(uint32_t epoch_base = 1700000000)
        : _epoch_base(epoch_base) {}

    unsigned long getMillis() override { return _virtual_millis; }
    uint32_t getCurrentTime() override { return _epoch_base + (uint32_t)(_virtual_millis / 1000); }
    void setCurrentTime(uint32_t epoch) override { _epoch_base = epoch - (uint32_t)(_virtual_millis / 1000); }
    void tick() override {}

    void advanceMillis(unsigned long delta) { _virtual_millis += delta; }
};
