#include "CayenneLPP.h"

// Helper to add bytes if space permits
static bool lpp_add(uint8_t* buf, size_t& sz, size_t maxsz, uint8_t channel, uint8_t type, const uint8_t* data, size_t data_len) {
    if (sz + 2 + data_len > maxsz) return false;
    buf[sz++] = channel;
    buf[sz++] = type;
    for (size_t i = 0; i < data_len; i++) buf[sz++] = data[i];
    return true;
}

void CayenneLPP::addDigitalInput(uint8_t channel, uint8_t value) {
    lpp_add(_buf, _size, _maxsize, channel, LPP_DIGITAL_INPUT, &value, 1);
}

void CayenneLPP::addAnalogInput(uint8_t channel, float value) {
    int16_t val = (int16_t)(value * 100);
    uint8_t data[2] = { (uint8_t)(val >> 8), (uint8_t)(val & 0xFF) };
    lpp_add(_buf, _size, _maxsize, channel, LPP_ANALOG_INPUT, data, 2);
}

void CayenneLPP::addTemperature(uint8_t channel, float celsius) {
    int16_t val = (int16_t)(celsius * 10);
    uint8_t data[2] = { (uint8_t)(val >> 8), (uint8_t)(val & 0xFF) };
    lpp_add(_buf, _size, _maxsize, channel, LPP_TEMPERATURE, data, 2);
}

void CayenneLPP::addRelativeHumidity(uint8_t channel, float rh) {
    uint8_t val = (uint8_t)(rh * 2);
    lpp_add(_buf, _size, _maxsize, channel, LPP_RELATIVE_HUMIDITY, &val, 1);
}

void CayenneLPP::addBarometricPressure(uint8_t channel, float hpa) {
    int16_t val = (int16_t)(hpa * 10);
    uint8_t data[2] = { (uint8_t)(val >> 8), (uint8_t)(val & 0xFF) };
    lpp_add(_buf, _size, _maxsize, channel, LPP_BAROMETRIC_PRESSURE, data, 2);
}

void CayenneLPP::addLuminosity(uint8_t channel, uint16_t lux) {
    uint8_t data[2] = { (uint8_t)(lux >> 8), (uint8_t)(lux & 0xFF) };
    lpp_add(_buf, _size, _maxsize, channel, LPP_LUMINOSITY, data, 2);
}

void CayenneLPP::addVoltage(uint8_t channel, float voltage) {
    // Use analog input type for voltage (value in V * 100)
    addAnalogInput(channel, voltage);
}

void CayenneLPP::addGPS(uint8_t channel, float latitude, float longitude, float altitude) {
    int32_t lat = (int32_t)(latitude * 10000);
    int32_t lon = (int32_t)(longitude * 10000);
    int32_t alt = (int32_t)(altitude * 100);
    uint8_t data[9] = {
        (uint8_t)(lat >> 16), (uint8_t)(lat >> 8), (uint8_t)(lat),
        (uint8_t)(lon >> 16), (uint8_t)(lon >> 8), (uint8_t)(lon),
        (uint8_t)(alt >> 16), (uint8_t)(alt >> 8), (uint8_t)(alt)
    };
    lpp_add(_buf, _size, _maxsize, channel, LPP_GPS, data, 9);
}
