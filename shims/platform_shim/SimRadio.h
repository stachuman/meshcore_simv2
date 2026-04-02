#pragma once
#include <Dispatcher.h>
#include <queue>
#include <vector>
#include <cstdint>
#include <functional>

struct IncomingPacket {
    std::vector<uint8_t> data;
    float snr;
    float rssi;
};

#ifdef ORCHESTRATOR_BUILD
struct TxCapture {
    std::vector<uint8_t> data;
    uint32_t airtime_ms;
    bool is_replay = false;
};
using TxCallback = std::function<void(const uint8_t* data, int len, uint32_t airtime_ms)>;
#endif

// Extended SimRadio with packet counters (for StatsFormatHelper)
// and RxBoostedGainMode (for CommonCLI).
class SimRadio : public mesh::Radio {
    std::queue<IncomingPacket> _rx_queue;
    float _last_snr   = 0.0f;
    float _last_rssi  = -100.0f;

    // Radio state machine (matches RadioLib STATE_IDLE/STATE_RX/STATE_TX_WAIT)
    enum class RadioState : uint8_t {
        IDLE,      // Standby — not listening, not transmitting
        RX,        // Continuous receive mode
        TX_WAIT,   // Transmitting, waiting for completion
    };
    RadioState _state = RadioState::RX;  // Radio starts in receive mode

    unsigned long _rx_active_until = 0;

    // LBT: channel activity windows (preamble detection delay)
    struct LbtWindow {
        unsigned long from_ms;
        unsigned long until_ms;
    };
    std::vector<LbtWindow> _lbt_windows;

    int _sf;
    int _bw_hz;
    int _cr;

    mesh::MillisecondClock& _ms;
    unsigned long _tx_done_at = 0;

    // Packet counters for StatsFormatHelper
    uint32_t _packets_recv = 0;
    uint32_t _packets_sent = 0;
    uint32_t _packets_recv_errors = 0;

    // TX failure probability (models SPI/hardware errors per RadioLib error path)
    float _tx_fail_prob = 0.0f;
    uint32_t _rng_state = 1;  // xorshift32 state (avoids <random> / min/max macro clash)
    uint32_t _tx_fail_count_stat = 0;

    // Rx boosted gain mode (no-op in simulator)
    bool _rx_boosted_gain = false;

public:
    SimRadio(mesh::MillisecondClock& ms,
             int sf = 8, int bw_hz = 62500, int cr = 4);

    void enqueue(const uint8_t* data, int len, float snr, float rssi);
    void notifyRxStart(uint32_t duration_ms);
    void notifyChannelBusy(unsigned long from_ms, unsigned long until_ms);
    uint32_t getPreambleDetectMs() const;

    // ---- mesh::Radio interface ----
    int      recvRaw(uint8_t* bytes, int sz)            override;
    uint32_t getEstAirtimeFor(int len_bytes)             override;
    float    packetScore(float snr, int packet_len)      override;
    bool     startSendRaw(const uint8_t* bytes, int len) override;
    bool     isSendComplete()                            override;
    void     onSendFinished()                            override;
    bool     isInRecvMode() const                        override;
    bool     isReceiving()                               override;
    float    getLastSNR()  const                         override { return _last_snr;  }
    float    getLastRSSI() const                         override { return _last_rssi; }

    // Packet counters (used by StatsFormatHelper)
    uint32_t getPacketsRecv() const { return _packets_recv; }
    uint32_t getPacketsSent() const { return _packets_sent; }
    uint32_t getPacketsRecvErrors() const { return _packets_recv_errors; }
    void resetStats() { _packets_recv = _packets_sent = _packets_recv_errors = 0; }

    int getSF() const { return _sf; }
    int getCR() const { return _cr; }
    double getSymbolMs() const { return (double)(1 << _sf) / (_bw_hz / 1000.0); }
    int getPreambleSymbols() const { return 8; }
    double getPreambleMs() const { return (getPreambleSymbols() + 4.25) * getSymbolMs(); }
    float getSnrThreshold() const;

    // Rx boosted gain mode (no-op in simulator)
    void setRxBoostedGainMode(bool enable) { _rx_boosted_gain = enable; }
    bool getRxBoostedGainMode() const { return _rx_boosted_gain; }

    // TX failure simulation
    void setTxFailProb(float p) { _tx_fail_prob = p; }
    void seed(uint64_t s) { _rng_state = static_cast<uint32_t>(s) | 1u; } // ensure non-zero
    uint32_t getTxFailCount() const { return _tx_fail_count_stat; }

#ifdef ORCHESTRATOR_BUILD
    void setTxCallback(TxCallback cb) { _tx_callback = std::move(cb); }
private:
    TxCallback _tx_callback;
#endif
};
