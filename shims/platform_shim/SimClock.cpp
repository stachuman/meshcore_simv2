#include "SimClock.h"
#include <ctime>
#include <thread>

static SimClock* g_sim_clock = nullptr;

void sim_clock_set_global(SimClock* clk) { g_sim_clock = clk; }
SimClock* sim_clock_get_global() { return g_sim_clock; }

// Arduino-compatible millis() and delay() free functions
unsigned long millis() {
    if (g_sim_clock) return g_sim_clock->getMillis();
    return 0;
}

void delay(unsigned long ms) {
#ifndef ORCHESTRATOR_BUILD
    std::this_thread::sleep_for(std::chrono::milliseconds(ms));
#else
    (void)ms; // no-op in orchestrator: virtual clock, no wall-time waiting
#endif
}

SimClock::SimClock()
    : _start(Clock::now()),
      _epoch_base((uint32_t)std::time(nullptr))
{}

unsigned long SimClock::getMillis() {
    auto elapsed = Clock::now() - _start;
    return (unsigned long)
        std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count();
}

uint32_t SimClock::getCurrentTime() {
    auto elapsed = Clock::now() - _start;
    uint32_t secs = (uint32_t)
        std::chrono::duration_cast<std::chrono::seconds>(elapsed).count();
    return _epoch_base + secs;
}

void SimClock::setCurrentTime(uint32_t epoch) {
    auto elapsed = Clock::now() - _start;
    uint32_t secs_elapsed = (uint32_t)
        std::chrono::duration_cast<std::chrono::seconds>(elapsed).count();
    _epoch_base = epoch - secs_elapsed;
}
