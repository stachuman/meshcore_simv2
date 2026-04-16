#pragma once
// Extended Arduino Stream/Print shim for POSIX compilation.
// Provides the full interface needed by MeshCore firmware examples.

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>

class Print {
public:
    virtual size_t write(uint8_t c) = 0;
    virtual size_t write(const uint8_t* buf, size_t n) {
        size_t written = 0;
        while (n--) written += write(*buf++);
        return written;
    }
    size_t write(const char* str) {
        if (!str) return 0;
        return write((const uint8_t*)str, strlen(str));
    }
    size_t print(const char* s)   { return write((const uint8_t*)s, strlen(s)); }
    size_t print(char c)          { return write((uint8_t)c); }
    size_t print(int val)         { char buf[16]; snprintf(buf, sizeof(buf), "%d", val); return print(buf); }
    size_t print(unsigned int val){ char buf[16]; snprintf(buf, sizeof(buf), "%u", val); return print(buf); }
    size_t print(long val)        { char buf[24]; snprintf(buf, sizeof(buf), "%ld", val); return print(buf); }
    size_t print(unsigned long val){ char buf[24]; snprintf(buf, sizeof(buf), "%lu", val); return print(buf); }
    size_t print(double val, int decimals = 2) {
        char buf[32]; snprintf(buf, sizeof(buf), "%.*f", decimals, val);
        return print(buf);
    }
    size_t println()              { return write((uint8_t)'\n'); }
    size_t println(const char* s) { size_t n = print(s); return n + println(); }
    size_t println(int val)       { size_t n = print(val); return n + println(); }
    size_t println(unsigned long val) { size_t n = print(val); return n + println(); }

    size_t printf(const char* fmt, ...) __attribute__((format(printf, 2, 3)));

    virtual ~Print() = default;
};

class Stream : public Print {
public:
    virtual int read() = 0;
    virtual int available() = 0;
    virtual int peek() = 0;
    virtual void flush() {}

    virtual size_t readBytes(uint8_t* buf, size_t n) {
        size_t count = 0;
        while (count < n) {
            int c = read();
            if (c < 0) break;
            buf[count++] = (uint8_t)c;
        }
        return count;
    }
    size_t readBytes(char* buf, size_t n) {
        return readBytes((uint8_t*)buf, n);
    }
};

// Buffer-backed stream: read from a const byte slice, write into a growable buffer.
class BufferStream : public Stream {
    const uint8_t* _rbuf;
    size_t         _rlen, _rpos;
    uint8_t*       _wbuf;
    size_t         _wmax, _wpos;
public:
    // Expose the base-class multi-byte write() overloads that would
    // otherwise be hidden by the single-byte override below.
    using Print::write;

    BufferStream(const uint8_t* rbuf, size_t rlen, uint8_t* wbuf, size_t wmax)
        : _rbuf(rbuf), _rlen(rlen), _rpos(0),
          _wbuf(wbuf), _wmax(wmax), _wpos(0) {}

    int    read()              override { return _rpos < _rlen ? _rbuf[_rpos++] : -1; }
    int    available()         override { return (int)(_rlen > _rpos ? _rlen - _rpos : 0); }
    int    peek()              override { return _rpos < _rlen ? _rbuf[_rpos] : -1; }
    size_t write(uint8_t c)    override { if (_wpos < _wmax) { _wbuf[_wpos++] = c; return 1; } return 0; }
    size_t bytesWritten() const { return _wpos; }
};
