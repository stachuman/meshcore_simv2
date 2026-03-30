#pragma once
#include <Utils.h>
#include <cstdint>

// Deterministic PRNG backed by xoshiro256** (Blackman & Vigna, public domain).
// Seeded from the node's --prv key bytes for reproducible simulation runs.
class SimRNG : public mesh::RNG {
    uint64_t _s[4];  // xoshiro256** state

    static uint64_t _splitmix64(uint64_t& state);

public:
    SimRNG();

    // Seed from arbitrary bytes (e.g. the 64-byte --prv key).
    void seed(const uint8_t* key, size_t key_len);

    // Seed from a single 64-bit integer.
    void seed(uint64_t s);

    // mesh::RNG interface
    void random(uint8_t* dest, size_t sz) override;
};
