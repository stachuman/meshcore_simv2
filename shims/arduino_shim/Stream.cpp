#include "Stream.h"
#include <stdio.h>
#include <stdarg.h>

size_t Print::printf(const char* fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n <= 0) return 0;
    if ((size_t)n >= sizeof(buf)) n = sizeof(buf) - 1;
    return write((const uint8_t*)buf, (size_t)n);
}
