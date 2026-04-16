#pragma once
// Extended Arduino.h shim for compiling MeshCore firmware examples on POSIX.
// Provides millis/delay, Serial, GPIO stubs, random, constrain, etc.

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <assert.h>

// Include ALL C++ STL headers that might be used BEFORE defining min/max macros.
// This prevents the macros from breaking template code in standard headers.
#include <algorithm>
#include <vector>
#include <queue>
#include <string>
#include <chrono>
#include <functional>
#include <type_traits>

// Forward declarations for global clock functions (implemented in SimClock.cpp)
unsigned long millis();
void delay(unsigned long ms);

// Arduino random functions
void randomSeed(unsigned long seed);
long random(long min_val, long max_val);
long random(long max_val);

// GPIO stubs
#define INPUT       0
#define OUTPUT      1
#define INPUT_PULLUP 2
#define LOW         0
#define HIGH        1
#define LED_BUILTIN 13

inline void pinMode(uint8_t, uint8_t) {}
inline void digitalWrite(uint8_t, uint8_t) {}
inline int  digitalRead(uint8_t) { return LOW; }
inline int  analogRead(uint8_t) { return 0; }

// constrain macro
#ifndef constrain
#define constrain(amt,low,high) ((amt)<(low)?(low):((amt)>(high)?(high):(amt)))
#endif

// Type aliases
typedef bool boolean;
typedef uint8_t byte;

// String conversion
inline char* ltoa(long value, char* str, int base) {
    if (base == 10) {
        sprintf(str, "%ld", value);
    } else if (base == 16) {
        sprintf(str, "%lx", value);
    } else {
        str[0] = '\0';
    }
    return str;
}

// yield() no-op
inline void yield() {}

#include "Stream.h"

// HardwareSerial class (outputs to stderr for debugging)
class HardwareSerial : public Stream {
public:
    void begin(unsigned long) {}
    void end() {}
    size_t write(uint8_t c) override {
        fputc(c, stderr);
        return 1;
    }
    size_t write(const uint8_t* buf, size_t n) override {
        return fwrite(buf, 1, n, stderr);
    }
    int read() override { return -1; }
    int available() override { return 0; }
    int peek() override { return -1; }
    operator bool() const { return true; }
};

#ifdef ORCHESTRATOR_BUILD
  extern HardwareSerial* _ctx_serial;
  // Assert-before-deref; see shims/lib_shim/target.h for the pattern rationale.
  #define Serial \
      ((void)(assert(_ctx_serial != nullptr && "Serial used with no active node")), *_ctx_serial)
#else
  extern HardwareSerial Serial;
#endif

// String class stub (minimal - only what MeshCore needs).
//
// On malloc failure the string ends up in a "not-OK" state (_buf==nullptr,
// _len==0, `operator bool()` returns false) — matches Arduino's convention
// so firmware that checks `if (str)` still works. In a simulator running
// on a GB-scale host malloc shouldn't fail in practice; assert() surfaces
// it loudly in Debug/ASan builds if it ever does.
class String {
    char* _buf;
    size_t _len;
public:
    String() : _buf(nullptr), _len(0) {}
    String(const char* s) : _buf(nullptr), _len(0) {
        if (s) {
            _len = strlen(s);
            _buf = (char*)malloc(_len+1);
            if (_buf) {
                memcpy(_buf, s, _len+1);
            } else {
                assert(false && "String(const char*): malloc failed");
                _len = 0;  // NDEBUG fallback: become a valid-but-empty String
            }
        }
    }
    String(const String& o) : _buf(nullptr), _len(0) {
        if (o._buf) {
            _len = o._len;
            _buf = (char*)malloc(_len+1);
            if (_buf) {
                memcpy(_buf, o._buf, _len+1);
            } else {
                assert(false && "String(const String&): malloc failed");
                _len = 0;
            }
        }
    }
    ~String() { free(_buf); }
    String& operator=(const String& o) {
        if (this != &o) {
            free(_buf); _buf = nullptr; _len = 0;
            if (o._buf) {
                _buf = (char*)malloc(o._len + 1);
                if (_buf) {
                    _len = o._len;
                    memcpy(_buf, o._buf, _len + 1);
                } else {
                    // BUGFIX: previously memcpy ran unconditionally; with
                    // malloc returning nullptr that was a null-deref.
                    assert(false && "String::operator=: malloc failed");
                    _len = 0;
                }
            }
        }
        return *this;
    }
    const char* c_str() const { return _buf ? _buf : ""; }
    size_t length() const { return _len; }
    operator bool() const { return _buf != nullptr && _len > 0; }
    bool operator==(const char* s) const { return strcmp(c_str(), s ? s : "") == 0; }
};

// Arduino-compatible min/max macros. Defined AFTER all STL includes to prevent
// breaking template code in standard library headers.
#undef min
#undef max
#define min(a,b) ((a)<(b)?(a):(b))
#define max(a,b) ((a)>(b)?(a):(b))
