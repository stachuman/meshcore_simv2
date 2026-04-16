#include "SimClock.h"
#include <cassert>
#include <ctime>
#include <thread>

static SimClock* g_sim_clock = nullptr;

void sim_clock_set_global(SimClock* clk) { g_sim_clock = clk; }
SimClock* sim_clock_get_global() { return g_sim_clock; }

// Arduino-compatible millis() and delay() free functions
unsigned long millis() {
#ifdef ORCHESTRATOR_BUILD
    // Under the orchestrator, millis() without an activated node is a bug:
    // it means firmware code ran outside any NodeContext::activate() scope
    // (stray callback, leaked thread, etc). Assertion makes this loud in
    // debug/ASan builds; production still returns 0 for safety.
    assert(g_sim_clock != nullptr && "millis() called with no node activated — context leak?");
#endif
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
