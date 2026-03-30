#pragma once
// Minimal RTClib.h shim providing DateTime class.
// Used by CommonCLI for log formatting.

#include <stdint.h>
#include <time.h>
#include <stdio.h>

class DateTime {
    uint32_t _unix;
public:
    DateTime(uint32_t unix_time = 0) : _unix(unix_time) {}

    uint16_t year() const {
        struct tm t;
        time_t tt = (time_t)_unix;
        gmtime_r(&tt, &t);
        return (uint16_t)(t.tm_year + 1900);
    }
    uint8_t month() const {
        struct tm t;
        time_t tt = (time_t)_unix;
        gmtime_r(&tt, &t);
        return (uint8_t)(t.tm_mon + 1);
    }
    uint8_t day() const {
        struct tm t;
        time_t tt = (time_t)_unix;
        gmtime_r(&tt, &t);
        return (uint8_t)t.tm_mday;
    }
    uint8_t hour() const {
        struct tm t;
        time_t tt = (time_t)_unix;
        gmtime_r(&tt, &t);
        return (uint8_t)t.tm_hour;
    }
    uint8_t minute() const {
        struct tm t;
        time_t tt = (time_t)_unix;
        gmtime_r(&tt, &t);
        return (uint8_t)t.tm_min;
    }
    uint8_t second() const {
        struct tm t;
        time_t tt = (time_t)_unix;
        gmtime_r(&tt, &t);
        return (uint8_t)t.tm_sec;
    }
    uint32_t unixtime() const { return _unix; }
};
