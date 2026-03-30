#pragma once
// Drop-in replacement for the rweather Arduino Crypto SHA256 class.
// Uses the OpenSSL 3.x EVP_MD / EVP_MAC API (no deprecated HMAC_CTX calls).

#include <openssl/evp.h>
#include <stdint.h>
#include <stddef.h>

// Forward-declare so we don't pull in the full openssl/mac.h here.
typedef struct evp_mac_st EVP_MAC;
typedef struct evp_mac_ctx_st EVP_MAC_CTX;

class SHA256 {
    EVP_MD_CTX*  _sha_ctx;   // used for plain SHA-256
    EVP_MAC*     _mac;       // algorithm handle (HMAC, shared / ref-counted)
    EVP_MAC_CTX* _mac_ctx;   // per-computation context for HMAC-SHA-256
    bool         _is_hmac;

public:
    SHA256();
    ~SHA256();

    // Feed data into the current hash/HMAC computation.
    void update(const void* data, size_t len);

    // Finalise and store up to hash_len bytes of the digest.
    void finalize(void* hash, size_t hash_len);

    // Begin an HMAC-SHA256 computation (key is consumed here).
    void resetHMAC(const void* key, size_t key_len);

    // Finalise the HMAC and store up to hash_len bytes of the tag.
    void finalizeHMAC(const void* key, size_t key_len,
                      void* hash, size_t hash_len);
};
