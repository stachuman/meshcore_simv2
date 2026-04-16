#include "Arduino.h"

#ifdef ORCHESTRATOR_BUILD
HardwareSerial* _ctx_serial = nullptr;
#else
HardwareSerial Serial;
#endif

void randomSeed(unsigned long seed) {
    srand((unsigned int)seed);
}

// Unbiased uniform draw in [0, n) using rejection sampling on top of rand().
// Plain `rand() % n` is biased unless n divides (RAND_MAX + 1) evenly; for
// typical n (e.g. 10, 100, 1000) the bias is small but measurable and can
// skew backoff-jitter / advert-timing decisions in long simulations.
static long rand_uniform(long n) {
    // rand() returns [0, RAND_MAX]. Largest multiple of n not exceeding
    // RAND_MAX+1, then accept only values below that multiple.
    const unsigned long span = static_cast<unsigned long>(RAND_MAX) + 1UL;
    const unsigned long un = static_cast<unsigned long>(n);
    const unsigned long limit = span - (span % un);  // exclusive upper bound
    unsigned long r;
    do {
        r = static_cast<unsigned long>(rand());
    } while (r >= limit);
    return static_cast<long>(r % un);
}

long random(long min_val, long max_val) {
    if (min_val >= max_val) return min_val;
    return min_val + rand_uniform(max_val - min_val);
}

long random(long max_val) {
    if (max_val <= 0) return 0;
    return rand_uniform(max_val);
}
