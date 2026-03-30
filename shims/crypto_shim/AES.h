#pragma once
// Drop-in replacement for the rweather Arduino Crypto AES128 class.
// Backed by OpenSSL EVP AES-128-ECB (no padding -- MeshCore manages full blocks).

#include <openssl/evp.h>
#include <stdint.h>
#include <stddef.h>

class AES128 {
    EVP_CIPHER_CTX* _enc_ctx;
    EVP_CIPHER_CTX* _dec_ctx;

public:
    AES128();
    ~AES128();

    // Set the 16-byte key; key_len must equal CIPHER_KEY_SIZE (16).
    void setKey(const uint8_t* key, size_t key_len);

    // Encrypt one 16-byte block (ECB, no padding).
    void encryptBlock(uint8_t* output, const uint8_t* input);

    // Decrypt one 16-byte block (ECB, no padding).
    void decryptBlock(uint8_t* output, const uint8_t* input);
};
