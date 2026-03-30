#include "SHA256.h"
#include <openssl/params.h>
#include <openssl/core_names.h>
#include <string.h>
#include <stdexcept>

SHA256::SHA256()
    : _sha_ctx(nullptr), _mac(nullptr), _mac_ctx(nullptr), _is_hmac(false)
{
    _sha_ctx = EVP_MD_CTX_new();
    if (!_sha_ctx) throw std::runtime_error("EVP_MD_CTX_new failed");
    EVP_DigestInit_ex(_sha_ctx, EVP_sha256(), nullptr);

    // Fetch the HMAC algorithm object once; it's ref-counted and cheap to keep.
    _mac = EVP_MAC_fetch(nullptr, "HMAC", nullptr);
    if (!_mac) throw std::runtime_error("EVP_MAC_fetch(HMAC) failed");
}

SHA256::~SHA256() {
    if (_mac_ctx) EVP_MAC_CTX_free(_mac_ctx);
    if (_mac)     EVP_MAC_free(_mac);
    if (_sha_ctx) EVP_MD_CTX_free(_sha_ctx);
}

void SHA256::update(const void* data, size_t len) {
    if (_is_hmac) {
        EVP_MAC_update(_mac_ctx, (const uint8_t*)data, len);
    } else {
        EVP_DigestUpdate(_sha_ctx, data, len);
    }
}

void SHA256::finalize(void* hash, size_t hash_len) {
    uint8_t tmp[32];
    unsigned int out_len = 32;
    EVP_DigestFinal_ex(_sha_ctx, tmp, &out_len);
    size_t copy = hash_len < 32 ? hash_len : 32;
    memcpy((uint8_t*)hash, tmp, copy);
}

void SHA256::resetHMAC(const void* key, size_t key_len) {
    _is_hmac = true;

    // Recreate the MAC context for each computation (cheap).
    if (_mac_ctx) { EVP_MAC_CTX_free(_mac_ctx); _mac_ctx = nullptr; }
    _mac_ctx = EVP_MAC_CTX_new(_mac);
    if (!_mac_ctx) throw std::runtime_error("EVP_MAC_CTX_new failed");

    // Tell the HMAC which digest to use.
    char digest_name[] = "SHA256";
    OSSL_PARAM params[] = {
        OSSL_PARAM_construct_utf8_string(OSSL_MAC_PARAM_DIGEST, digest_name, 0),
        OSSL_PARAM_construct_end()
    };
    if (!EVP_MAC_init(_mac_ctx, (const uint8_t*)key, key_len, params))
        throw std::runtime_error("EVP_MAC_init failed");
}

void SHA256::finalizeHMAC(const void* /*key*/, size_t /*key_len*/,
                           void* hash, size_t hash_len) {
    uint8_t tmp[32];
    size_t  out_len = 32;
    EVP_MAC_final(_mac_ctx, tmp, &out_len, sizeof(tmp));
    size_t copy = hash_len < 32 ? hash_len : 32;
    memcpy((uint8_t*)hash, tmp, copy);

    _is_hmac = false;
    // Re-initialise the plain SHA context so the object is reusable.
    EVP_DigestInit_ex(_sha_ctx, EVP_sha256(), nullptr);
}
