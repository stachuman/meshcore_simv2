#pragma once
// Minimal CayenneLPP encoder for MeshCore telemetry.

#include <stdint.h>
#include <stddef.h>
#include <string.h>

#define LPP_DIGITAL_INPUT       0
#define LPP_DIGITAL_OUTPUT      1
#define LPP_ANALOG_INPUT        2
#define LPP_ANALOG_OUTPUT       3
#define LPP_LUMINOSITY         101
#define LPP_TEMPERATURE        103
#define LPP_RELATIVE_HUMIDITY  104
#define LPP_BAROMETRIC_PRESSURE 115
#define LPP_GPS                136

class CayenneLPP {
    uint8_t _buf[64];
    size_t _size;
    size_t _maxsize;

public:
    CayenneLPP(size_t maxsize = 64) : _size(0), _maxsize(maxsize < sizeof(_buf) ? maxsize : sizeof(_buf)) {}

    void reset() { _size = 0; }
    size_t getSize() const { return _size; }
    const uint8_t* getBuffer() const { return _buf; }
    uint8_t* getBuffer() { return _buf; }

    void addDigitalInput(uint8_t channel, uint8_t value);
    void addAnalogInput(uint8_t channel, float value);
    void addTemperature(uint8_t channel, float celsius);
    void addRelativeHumidity(uint8_t channel, float rh);
    void addBarometricPressure(uint8_t channel, float hpa);
    void addLuminosity(uint8_t channel, uint16_t lux);
    void addGPS(uint8_t channel, float latitude, float longitude, float altitude);
    void addVoltage(uint8_t channel, float voltage);
};
