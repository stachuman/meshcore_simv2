#pragma once
// Drop-in replacement for the rweather Arduino Crypto Ed25519 class.
// Delegates to the local lib/ed25519 (orlp/ed25519) implementation.

#include <stdint.h>
#include <stddef.h>

extern "C" {
#include <ed_25519.h>
}

// Static-only class: just wraps ed25519_verify() with the rweather argument order.
class Ed25519 {
public:
    // verify(sig, public_key, message, message_len)
    static bool verify(const uint8_t* sig, const uint8_t* pub_key,
                       const uint8_t* message, size_t msg_len) {
        return ed25519_verify(sig, message, msg_len, pub_key) != 0;
    }
};
