#pragma once
// SimClock: provides both mesh::MillisecondClock and mesh::RTCClock.
// Extended with tick() for VolatileRTCClock-compatible behavior
// and global pointer for millis()/delay() free functions.

#include <Dispatcher.h>   // MillisecondClock
#include <MeshCore.h>     // RTCClock
#include <chrono>

class SimClock : public mesh::MillisecondClock, public mesh::RTCClock {
    using Clock    = std::chrono::steady_clock;
    using TimePoint = Clock::time_point;

    TimePoint _start;
    uint32_t  _epoch_base;  // Unix epoch at startup (overrideable)

public:
    SimClock();

    // mesh::MillisecondClock
    unsigned long getMillis() override;

    // mesh::RTCClock
    uint32_t getCurrentTime() override;
    void     setCurrentTime(uint32_t epoch) override;

    // tick() for compatibility with VolatileRTCClock (no-op here since we use wall clock)
    void tick() override {}
};

// Set/get the global SimClock pointer for millis()/delay() free functions.
void sim_clock_set_global(SimClock* clk);
SimClock* sim_clock_get_global();
