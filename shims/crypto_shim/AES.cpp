#include "AES.h"
#include <stdexcept>
#include <string.h>

AES128::AES128() : _enc_ctx(nullptr), _dec_ctx(nullptr) {
    _enc_ctx = EVP_CIPHER_CTX_new();
    _dec_ctx = EVP_CIPHER_CTX_new();
    if (!_enc_ctx || !_dec_ctx) throw std::runtime_error("EVP_CIPHER_CTX_new failed");
}

AES128::~AES128() {
    if (_enc_ctx) EVP_CIPHER_CTX_free(_enc_ctx);
    if (_dec_ctx) EVP_CIPHER_CTX_free(_dec_ctx);
}

void AES128::setKey(const uint8_t* key, size_t /*key_len*/) {
    // Initialise both contexts with the key; IV is unused (ECB mode).
    EVP_EncryptInit_ex(_enc_ctx, EVP_aes_128_ecb(), nullptr, key, nullptr);
    EVP_DecryptInit_ex(_dec_ctx, EVP_aes_128_ecb(), nullptr, key, nullptr);
    // Disable automatic PKCS#7 padding -- MeshCore always provides full blocks.
    EVP_CIPHER_CTX_set_padding(_enc_ctx, 0);
    EVP_CIPHER_CTX_set_padding(_dec_ctx, 0);
}

void AES128::encryptBlock(uint8_t* output, const uint8_t* input) {
    int out_len = 0;
    EVP_EncryptUpdate(_enc_ctx, output, &out_len, input, 16);
    // No EVP_EncryptFinal needed: padding is off and we always pass a full block.
}

void AES128::decryptBlock(uint8_t* output, const uint8_t* input) {
    int out_len = 0;
    EVP_DecryptUpdate(_dec_ctx, output, &out_len, input, 16);
}
