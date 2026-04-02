#include "SimRNG.h"
#include <cstring>

static constexpr uint64_t FNV1A_OFFSET_BASIS = 0xcbf29ce484222325ULL;
static constexpr uint64_t FNV1A_PRIME        = 0x100000001b3ULL;

// SplitMix64 -- used to expand a short seed into xoshiro256** state.
uint64_t SimRNG::_splitmix64(uint64_t& state) {
    uint64_t z = (state += 0x9e3779b97f4a7c15ULL);
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}

// xoshiro256** core
static inline uint64_t rotl(const uint64_t x, int k) {
    return (x << k) | (x >> (64 - k));
}

static uint64_t xoshiro256ss(uint64_t s[4]) {
    const uint64_t result = rotl(s[1] * 5, 7) * 9;
    const uint64_t t = s[1] << 17;

    s[2] ^= s[0];
    s[3] ^= s[1];
    s[1] ^= s[2];
    s[0] ^= s[3];
    s[2] ^= t;
    s[3] = rotl(s[3], 45);

    return result;
}

SimRNG::SimRNG() {
    seed(uint64_t(0));
}

void SimRNG::seed(uint64_t s) {
    uint64_t sm = s;
    _s[0] = _splitmix64(sm);
    _s[1] = _splitmix64(sm);
    _s[2] = _splitmix64(sm);
    _s[3] = _splitmix64(sm);
}

void SimRNG::seed(const uint8_t* key, size_t key_len) {
    uint64_t h = FNV1A_OFFSET_BASIS;
    for (size_t i = 0; i < key_len; i++) {
        h ^= key[i];
        h *= FNV1A_PRIME;
    }
    seed(h);
}

void SimRNG::random(uint8_t* dest, size_t sz) {
    size_t i = 0;
    while (i < sz) {
        uint64_t val = xoshiro256ss(_s);
        size_t remaining = sz - i;
        size_t chunk = remaining < 8 ? remaining : 8;
        memcpy(dest + i, &val, chunk);
        i += chunk;
    }
}
