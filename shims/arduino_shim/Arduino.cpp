#include "Arduino.h"

#ifdef ORCHESTRATOR_BUILD
HardwareSerial* _ctx_serial = nullptr;
#else
HardwareSerial Serial;
#endif

void randomSeed(unsigned long seed) {
    srand((unsigned int)seed);
}

long random(long min_val, long max_val) {
    if (min_val >= max_val) return min_val;
    return min_val + (rand() % (max_val - min_val));
}

long random(long max_val) {
    if (max_val <= 0) return 0;
    return rand() % max_val;
}
