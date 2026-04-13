// STL before Arduino.h
#include <string>
#include <vector>
#include <memory>
#include <algorithm>
#include <cmath>
#include <set>
#include <cstdio>

#include "Orchestrator.h"
#include "MeshWrapper.h"
#include "SimClock.h"

#ifdef DELAY_TUNING_RUNTIME
#include <helpers/DelayTuning.h>
#endif

// SplitMix64 — hash a 64-bit seed into a well-distributed 64-bit value.
// Used to derive independent mt19937 stream seeds from a single config seed.
// Without this, XORing the seed with small constants (0x1..0x5) produces
// near-identical seeds that cause correlated early mt19937 output.
static uint64_t splitmix64(uint64_t z) {
    z += 0x9e3779b97f4a7c15ULL;
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}

// Symbols needed for receiver preamble lock (Semtech AN1200.22, Bor 2016).
// Capture thresholds (locked/unlocked) are configurable via OrchestratorConfig.
static constexpr int PREAMBLE_LOCK_SYMBOLS = 6;

// Symmetric (undirected) link index for n nodes.
// Maps ordered pair (a,b) to the same slot regardless of direction.
static int symmetricLinkIndex(int a, int b, int n) {
    int lo = (a < b) ? a : b;
    int hi = (a < b) ? b : a;
    return lo * n - lo * (lo + 1) / 2 + (hi - lo - 1);
}

int Orchestrator::findNode(const std::string& name) const {
    for (size_t i = 0; i < _nodes.size(); i++) {
        if (_nodes[i]->name == name) return (int)i;
    }
    return -1;
}

void Orchestrator::configure(const OrchestratorConfig& cfg) {
    // Clear any previous state
    _nodes.clear();
    _commands.clear();
    _link_model.reset();
    _next_cmd = 0;

    _duration_ms = cfg.duration_ms;
    _step_ms = cfg.step_ms;

    // Validate step_ms against hardware delay granularity (only when delays are active)
    if (cfg.rx_to_tx_delay_ms > 0.0f || cfg.tx_to_rx_delay_ms > 0.0f) {
        const int MAX_STEP_MS = 1;
        if (_step_ms > MAX_STEP_MS) {
            std::fprintf(stderr, "Warning: step_ms=%d exceeds hardware delay granularity (%dms). "
                        "Clamping to step_ms=%d.\n", _step_ms, MAX_STEP_MS, MAX_STEP_MS);
            _step_ms = MAX_STEP_MS;
        }
    }

    _warmup_ms = cfg.warmup_ms;
    _verbose = cfg.verbose;
    _seed = cfg.seed;
    // Derive independent mt19937 seeds via splitmix64 to avoid correlated streams.
    // Each stream gets a well-mixed 32-bit seed even for nearby config seeds.
    _rng_fading.seed(static_cast<std::mt19937::result_type>(splitmix64(cfg.seed ^ 0x1)));
    _rng_loss.seed(static_cast<std::mt19937::result_type>(splitmix64(cfg.seed ^ 0x2)));
    _rng_cad.seed(static_cast<std::mt19937::result_type>(splitmix64(cfg.seed ^ 0x3)));
    _rng_stagger.seed(static_cast<std::mt19937::result_type>(splitmix64(cfg.seed ^ 0x4)));
    _rng_adversarial.seed(static_cast<std::mt19937::result_type>(splitmix64(cfg.seed ^ 0x5)));
    _clock = VirtualClock(cfg.epoch_start);

    _capture_locked_db = cfg.capture_locked_db;
    _capture_unlocked_db = cfg.capture_unlocked_db;
    _cad_miss_prob = cfg.cad_miss_prob;
    _cad_reliable_snr = cfg.cad_reliable_snr;
    _cad_marginal_snr = cfg.cad_marginal_snr;
    _snr_coherence_ms = cfg.snr_coherence_ms;

    _pending_replays.clear();
    _reply_log.clear();
    _assertions = cfg.assertions;
    _tx_count = 0;
    _rx_count = 0;
    _ackpath_tx = 0;
    _ackpath_rx = 0;
    _ackpath_collision = 0;
    _ackpath_drop = 0;
    _event_counts.clear();
    _message_fates.clear();
    _hash_to_fate.clear();
    _pending_msg_fates.clear();
    _ackpath_hash_to_fate.clear();

    // Create nodes (each owns its own VirtualClock for per-node time stagger)
    for (const auto& nd : cfg.nodes) {
        auto ctx = std::make_unique<NodeContext>(nd.name, nd.role,
                                                 cfg.epoch_start,
                                                 nd.sf, nd.bw, nd.cr,
                                                 cfg.rx_to_tx_delay_ms,
                                                 cfg.tx_to_rx_delay_ms);
        ctx->adversarial = nd.adversarial;
        ctx->lat = nd.lat;
        ctx->lon = nd.lon;
        ctx->has_location = nd.has_location;
        ctx->radio.setTxFailProb(nd.tx_fail_prob);
        _nodes.push_back(std::move(ctx));
    }

    // Pre-build per-node event keys to avoid string concatenation on hot path
    _node_event_keys.resize(_nodes.size());
    for (size_t i = 0; i < _nodes.size(); i++) {
        const std::string& nm = _nodes[i]->name;
        _node_event_keys[i] = {
            "tx:" + nm, "rx:" + nm, "collision:" + nm,
            "drop_halfduplex:" + nm, "drop_weak:" + nm, "drop_loss:" + nm,
            "tx_fail:" + nm
        };
    }

    // Initialize per-node step tracking for message fate relay detection
    _step_rx_fates.resize(_nodes.size());
    _pending_ackpath_fates.resize(_nodes.size());
    _ackpath_relay_fates.resize(_nodes.size());

    // Build link model
    int n = (int)_nodes.size();
    _link_model = std::make_unique<MatrixLinkModel>(n);
    for (const auto& lk : cfg.links) {
        int from = findNode(lk.from);
        int to   = findNode(lk.to);
        if (from < 0 || to < 0) {
            fprintf(stderr, "Warning: link references unknown node: %s -> %s\n",
                    lk.from.c_str(), lk.to.c_str());
            continue;
        }
        if (lk.bidir) {
            _link_model->setBidirectional(from, to, lk.snr, lk.rssi, lk.snr_std_dev, lk.loss);
        } else {
            _link_model->setLink(from, to, lk.snr, lk.rssi, lk.snr_std_dev, lk.loss);
        }
    }

    // Initialize per-undirected-link fading state for O-U process (reciprocal)
    _fading_state.clear();
    _fading_state.resize(n * (n - 1) / 2);

    _hot_start = cfg.hot_start;

#ifdef DELAY_TUNING_RUNTIME
    if (cfg.delay_tuning.enabled) {
        setDelayTuningLinear(cfg.delay_tuning.tx_base, cfg.delay_tuning.tx_slope,
                             cfg.delay_tuning.dtx_base, cfg.delay_tuning.dtx_slope,
                             cfg.delay_tuning.rx_base, cfg.delay_tuning.rx_slope,
                             cfg.delay_tuning.clamp_min, cfg.delay_tuning.clamp_max);
        if (_verbose) {
            fprintf(stderr, "Delay tuning: tx=%.3f+%.4f*n  dtx=%.3f+%.4f*n  rx=%.3f+%.4f*n  clamp=[%.1f,%.1f]\n",
                    cfg.delay_tuning.tx_base, cfg.delay_tuning.tx_slope,
                    cfg.delay_tuning.dtx_base, cfg.delay_tuning.dtx_slope,
                    cfg.delay_tuning.rx_base, cfg.delay_tuning.rx_slope,
                    cfg.delay_tuning.clamp_min, cfg.delay_tuning.clamp_max);
        }
    }
#endif

    // Build scheduled commands (sorted by time)
    for (const auto& cd : cfg.commands) {
        if (!cd.lua_fn.empty()) {
            // Lua-only command: no node lookup needed
            _commands.push_back({cd.at_ms, -1, "", cd.lua_fn});
            continue;
        }
        int idx = findNode(cd.node);
        if (idx < 0) {
            fprintf(stderr, "Warning: command references unknown node: %s\n", cd.node.c_str());
            continue;
        }
        _commands.push_back({cd.at_ms, idx, cd.command, ""});
    }
    std::sort(_commands.begin(), _commands.end(),
              [](const ScheduledCommand& a, const ScheduledCommand& b) {
                  return a.at_ms < b.at_ms;
              });
}

// Instant delivery — used during warmup phase (no collisions, no half-duplex, no SNR filter)
void Orchestrator::routePackets(unsigned long current_ms) {
    int n = (int)_nodes.size();
    for (int sender = 0; sender < n; sender++) {
        auto& tx_list = _nodes[sender]->pending_tx;
        for (auto& cap : tx_list) {
            _tx_count++;
            _tx_log.push_back({_nodes[sender]->name, cap.airtime_ms});
            EventLog::tx(current_ms, _nodes[sender]->name.c_str(),
                         cap.data.data(), (int)cap.data.size(), cap.airtime_ms);
            if (_verbose) {
                fprintf(stderr, "[%8.3fs] TX %s %dB airtime=%ums (warmup: instant)\n",
                        current_ms / 1000.0, _nodes[sender]->name.c_str(),
                        (int)cap.data.size(), (unsigned)cap.airtime_ms);
            }

            for (int receiver = 0; receiver < n; receiver++) {
                if (receiver == sender) continue;
                LinkParams lp;
                if (_link_model->getLink(sender, receiver, lp)) {
                    _rx_count++;
                    _nodes[receiver]->activate();
                    _nodes[receiver]->radio.enqueue(
                        cap.data.data(), (int)cap.data.size(), lp.snr, lp.rssi);
                    EventLog::rx(current_ms, _nodes[sender]->name.c_str(),
                                 _nodes[receiver]->name.c_str(), lp.snr, lp.rssi,
                                 cap.data.data(), (int)cap.data.size(), cap.airtime_ms);
                }
            }
        }
        tx_list.clear();
    }
}

// Check if 'primary' packet survives interference from 'interferer'.
// 3-stage survival:
//   (1) Timing-dependent capture (first-arrival advantage)
//   (2) Preamble grace period
//   (3) FEC overlap tolerance
// Returns true if primary is destroyed by interferer.
static bool isDestroyedBy(const PendingRx& primary, const PendingRx& interferer,
                          double preamble_grace_ms, double fec_tolerance_ms,
                          double t_preamble_ms, double t_sym,
                          float capture_locked_db, float capture_unlocked_db) {
    // No temporal overlap → no interference
    if (interferer.rx_end_ms <= primary.rx_start_ms ||
        primary.rx_end_ms <= interferer.rx_start_ms)
        return false;

    // Stage 1: Timing-dependent capture effect.
    // If the primary's preamble was fully locked (5 symbols received cleanly)
    // before the interferer arrived, the receiver is synchronized and the
    // locked threshold applies. If preambles overlap, the unlocked threshold
    // applies (classic power-dominance model).
    double lock_time_ms = primary.rx_start_ms + PREAMBLE_LOCK_SYMBOLS * t_sym;
    float capture_threshold = ((double)interferer.rx_start_ms >= lock_time_ms)
                              ? capture_locked_db : capture_unlocked_db;

    if (primary.snr >= interferer.snr + capture_threshold)
        return false;

    // Stage 2: Preamble grace — primary survives if interferer only
    // hits the non-critical part of primary's preamble (first 3 of 8 symbols)
    unsigned long critical = primary.rx_start_ms + (unsigned long)std::lround(preamble_grace_ms);
    if (interferer.rx_end_ms <= critical)
        return false;

    // Stage 3: FEC overlap tolerance — if overlap is small and within payload,
    // forward error correction can recover.
    if (fec_tolerance_ms > 0.0) {
        unsigned long overlap_start = (primary.rx_start_ms > interferer.rx_start_ms)
                                      ? primary.rx_start_ms : interferer.rx_start_ms;
        unsigned long overlap_end = (primary.rx_end_ms < interferer.rx_end_ms)
                                    ? primary.rx_end_ms : interferer.rx_end_ms;
        double overlap_ms = (double)(overlap_end - overlap_start);
        if (overlap_ms <= fec_tolerance_ms) {
            unsigned long payload_start = primary.rx_start_ms + (unsigned long)std::lround(t_preamble_ms);
            if (overlap_start >= payload_start)
                return false;
        }
    }

    return true;  // primary is destroyed
}

// Helper: check if packet header indicates "msg" payload type (bits 5-2 == 0x02)
static bool isMsgPacketType(const uint8_t* data, int len) {
    if (len < 1) return false;
    return ((data[0] >> 2) & 0x0F) == 0x02;
}

// Helper: check if packet is ACK (0x03) or PATH_RETURN (0x08) — used for radio efficiency metric
static bool isAckOrPathType(const uint8_t* data, int len) {
    if (len < 1) return false;
    uint8_t ptype = (data[0] >> 2) & 0x0F;
    return ptype == 0x03 || ptype == 0x08;
}

void Orchestrator::registerTransmissions(unsigned long current_ms) {
    int n = (int)_nodes.size();

    // Adversarial pre-pass: modify pending_tx before routing.
    // Only applies to non-replay TXes from adversarial nodes.
    std::uniform_real_distribution<float> prob(0.0f, 1.0f);
    std::uniform_int_distribution<int> bit_dist(0, 7);
    for (int sender = 0; sender < n; sender++) {
        auto& tx_list = _nodes[sender]->pending_tx;
        if (tx_list.empty()) continue;
        const auto& adv = _nodes[sender]->adversarial;
        if (adv.mode == AdversarialMode::None) continue;

        auto it = tx_list.begin();
        while (it != tx_list.end()) {
            if (it->is_replay) { ++it; continue; }

            // Roll probability
            if (prob(_rng_adversarial) >= adv.probability) { ++it; continue; }

            const char* sname = _nodes[sender]->name.c_str();

            if (adv.mode == AdversarialMode::Drop) {
                EventLog::adversarialDrop(current_ms, sname, it->data.data(), (int)it->data.size());
                if (_verbose) {
                    fprintf(stderr, "[%8.3fs] ADV-DROP %s %dB\n",
                            current_ms / 1000.0, sname, (int)it->data.size());
                }
                it = tx_list.erase(it);
                continue;
            }

            if (adv.mode == AdversarialMode::Corrupt) {
                int bits = adv.corrupt_bits;
                EventLog::adversarialCorrupt(current_ms, sname,
                                             it->data.data(), (int)it->data.size(), bits);
                // Flip N random bits
                for (int b = 0; b < bits && !it->data.empty(); b++) {
                    std::uniform_int_distribution<int> byte_dist(0, (int)it->data.size() - 1);
                    it->data[byte_dist(_rng_adversarial)] ^= (1 << bit_dist(_rng_adversarial));
                }
                if (_verbose) {
                    fprintf(stderr, "[%8.3fs] ADV-CORRUPT %s %dB (%d bits)\n",
                            current_ms / 1000.0, sname, (int)it->data.size(), bits);
                }
                ++it;
                continue;
            }

            if (adv.mode == AdversarialMode::Replay) {
                unsigned long delay = adv.replay_delay_ms;
                EventLog::adversarialReplay(current_ms, sname,
                                            it->data.data(), (int)it->data.size(), delay);
                _pending_replays.push_back({
                    current_ms + delay, sender,
                    std::vector<uint8_t>(it->data.begin(), it->data.end()),
                    it->airtime_ms
                });
                if (_verbose) {
                    fprintf(stderr, "[%8.3fs] ADV-REPLAY %s %dB (emit@%.3fs)\n",
                            current_ms / 1000.0, sname, (int)it->data.size(),
                            (current_ms + delay) / 1000.0);
                }
                ++it;  // original still sent
                continue;
            }

            ++it;
        }
    }

    // Pre-pass: set tx_busy_until for ALL senders with pending TX this step.
    // This ensures half-duplex checks are symmetric regardless of iteration order.
    // Also mark any active RX on the sender as halfduplex_abort (TX aborts RX).
    for (int sender = 0; sender < n; sender++) {
        auto& tx_list = _nodes[sender]->pending_tx;
        if (tx_list.empty()) continue;
        _nodes[sender]->tx_busy_from = current_ms;
        for (auto& cap : tx_list) {
            unsigned long end = current_ms + cap.airtime_ms;
            if (end > _nodes[sender]->tx_busy_until)
                _nodes[sender]->tx_busy_until = end;
        }
        // TX aborts any ongoing RX on this sender
        for (auto& prx : _nodes[sender]->active_rx) {
            if (prx.rx_end_ms > current_ms) {
                prx.halfduplex_abort = true;
            }
        }
    }

    // Main pass: route each TX to linked receivers
    for (int sender = 0; sender < n; sender++) {
        auto& tx_list = _nodes[sender]->pending_tx;
        if (tx_list.empty()) continue;

        for (auto& cap : tx_list) {
            unsigned long airtime = cap.airtime_ms;
            unsigned long rx_end = current_ms + airtime;

            _tx_count++;
            _event_counts["tx"]++;
            _event_counts[_node_event_keys[sender].tx]++;
            if (isAckOrPathType(cap.data.data(), (int)cap.data.size())) {
                _ackpath_tx++;
                _event_counts["ackpath_tx"]++;
            }
            _tx_log.push_back({_nodes[sender]->name, cap.airtime_ms});
            EventLog::tx(current_ms, _nodes[sender]->name.c_str(),
                         cap.data.data(), (int)cap.data.size(), cap.airtime_ms);
            if (_verbose) {
                fprintf(stderr, "[%8.3fs] TX %s %dB airtime=%lums\n",
                        current_ms / 1000.0, _nodes[sender]->name.c_str(),
                        (int)cap.data.size(), airtime);
            }

            // Message fate: link TX hash to tracked message
            uint32_t tx_hash = EventLog::packetHash(cap.data.data(), (int)cap.data.size());
            int linked_fate = -1;
            // Check if this is the initial TX from a pending message command
            auto pmf_it = _pending_msg_fates.find(sender);
            if (pmf_it != _pending_msg_fates.end() && isMsgPacketType(cap.data.data(), (int)cap.data.size())) {
                linked_fate = pmf_it->second;
                _message_fates[linked_fate].pkt_hashes.insert(tx_hash);
                _message_fates[linked_fate].tx_count++;
                _hash_to_fate[tx_hash] = linked_fate;
                _pending_msg_fates.erase(pmf_it);
            }
            // Check if this is a relay TX from a node that received tracked packets
            // (persistent across steps — Dispatcher may delay relay by many steps)
            if (linked_fate < 0 && !_step_rx_fates.empty() && !_step_rx_fates[sender].empty()
                && isMsgPacketType(cap.data.data(), (int)cap.data.size())) {
                for (int fi : _step_rx_fates[sender]) {
                    _message_fates[fi].pkt_hashes.insert(tx_hash);
                    _message_fates[fi].tx_count++;
                    _hash_to_fate[tx_hash] = fi;
                    linked_fate = fi;
                }
                _step_rx_fates[sender].clear();  // consumed — prevent re-linking
            }

            // ACK/PATH fate tracking: link ack/path TX hash to the message fate
            if (isAckOrPathType(cap.data.data(), (int)cap.data.size())) {
                // Initial ack/path from message destination?
                auto& pending_ack = _pending_ackpath_fates[sender];
                if (!pending_ack.empty()) {
                    int fi = *pending_ack.begin();
                    pending_ack.erase(pending_ack.begin());
                    _ackpath_hash_to_fate[tx_hash] = fi;
                } else {
                    // Relay of ack/path?
                    auto& relay_ack = _ackpath_relay_fates[sender];
                    if (!relay_ack.empty()) {
                        for (int fi : relay_ack) {
                            _ackpath_hash_to_fate[tx_hash] = fi;
                        }
                        relay_ack.clear();
                    }
                }
            }

            for (int receiver = 0; receiver < n; receiver++) {
                if (receiver == sender) continue;
                LinkParams lp;
                if (!_link_model->getLink(sender, receiver, lp)) continue;

                const char* sname = _nodes[sender]->name.c_str();
                const char* rname = _nodes[receiver]->name.c_str();

                // Sample per-reception SNR: i.i.d. Gaussian or correlated O-U
                float rx_snr = lp.snr;
                if (lp.snr_std_dev > 0.0f) {
                    if (_snr_coherence_ms > 0.0f) {
                        // Ornstein-Uhlenbeck (continuous-time AR(1)) correlated fading.
                        // Consecutive receptions on the same link see correlated SNR.
                        auto& fs = _fading_state[symmetricLinkIndex(sender, receiver, n)];
                        float dt = (float)(current_ms - fs.last_ms);
                        float alpha = std::exp(-dt / _snr_coherence_ms);
                        float alpha_sq = alpha * alpha;
                        if (alpha_sq > 1.0f) alpha_sq = 1.0f;  // float precision guard
                        std::normal_distribution<float> unit(0.0f, 1.0f);
                        fs.offset = alpha * fs.offset
                                  + std::sqrt(1.0f - alpha_sq) * lp.snr_std_dev * unit(_rng_fading);
                        fs.last_ms = current_ms;
                        rx_snr = lp.snr + fs.offset;
                    } else {
                        // i.i.d. Gaussian (original behavior)
                        std::normal_distribution<float> dist(lp.snr, lp.snr_std_dev);
                        rx_snr = dist(_rng_fading);
                    }
                }

                // SNR check: use receiver's radio SF threshold.
                // Below-threshold signals are too weak for both decoding AND
                // preamble detection (CAD), so no LBT notification either.
                float score = _nodes[receiver]->radio.packetScore(rx_snr, (int)cap.data.size());
                if (score <= 0.0f) {
                    float thr = _nodes[receiver]->radio.getSnrThreshold();
                    _event_counts["drop_weak"]++;
                    _event_counts[_node_event_keys[receiver].drop_weak]++;
                    EventLog::dropWeak(current_ms, sname, rname, rx_snr, thr,
                                       cap.data.data(), (int)cap.data.size());
                    if (_verbose) {
                        fprintf(stderr, "[%8.3fs]   X  %s weak (snr=%.1f < %.1f)\n",
                                current_ms / 1000.0, rname, rx_snr, thr);
                    }
                    if (linked_fate >= 0) _message_fates[linked_fate].drops++;
                    continue;
                }

                // LBT: notify receiver of channel activity. Signal is above
                // threshold, so CAD would detect the preamble after ~5 symbols.
                // Convert from orchestrator time domain to node-clock domain.
                uint32_t preamble_detect_ms = _nodes[receiver]->radio.getPreambleDetectMs();
                unsigned long node_now = _nodes[receiver]->own_clock.getMillis();
                uint32_t airtime32 = (uint32_t)(rx_end - current_ms);
                unsigned long lbt_from = node_now + preamble_detect_ms;
                unsigned long lbt_until = node_now + airtime32;
                if (lbt_from < lbt_until) {
                    // SNR-dependent CAD miss: reliable at high SNR, degrades toward marginal
                    float effective_miss = _cad_miss_prob;
                    if (rx_snr < _cad_reliable_snr) {
                        if (rx_snr <= _cad_marginal_snr) {
                            effective_miss = 1.0f;
                        } else {
                            float t = (rx_snr - _cad_marginal_snr) / (_cad_reliable_snr - _cad_marginal_snr);
                            effective_miss = 1.0f - t * (1.0f - _cad_miss_prob);
                        }
                    }
                    if (effective_miss > 0.0f && prob(_rng_cad) < effective_miss) {
                        if (_verbose) {
                            fprintf(stderr, "[%8.3fs]   ~  %s CAD miss (p=%.3f snr=%.1f)\n",
                                    current_ms / 1000.0, rname, effective_miss, rx_snr);
                        }
                    } else {
                        _nodes[receiver]->radio.notifyChannelBusy(lbt_from, lbt_until);
                    }
                }

                // Half-duplex check: receiver is currently transmitting
                if (_nodes[receiver]->tx_busy_until > current_ms) {
                    _event_counts["drop_halfduplex"]++;
                    _event_counts[_node_event_keys[receiver].drop_halfduplex]++;
                    if (isAckOrPathType(cap.data.data(), (int)cap.data.size())) {
                        _ackpath_drop++;
                        _event_counts["ackpath_drop"]++;
                    }
                    EventLog::dropHalfDuplex(current_ms, sname, rname,
                                             cap.data.data(), (int)cap.data.size(),
                                             cap.airtime_ms);
                    if (_verbose) {
                        fprintf(stderr, "[%8.3fs]   X  %s half-duplex (tx until %.3fs)\n",
                                current_ms / 1000.0, rname,
                                _nodes[receiver]->tx_busy_until / 1000.0);
                    }
                    if (linked_fate >= 0) _message_fates[linked_fate].drops++;
                    continue;
                }

                // Stochastic link loss: mark for drop but still create PendingRx
                // so the RF energy participates in collision detection.
                bool lost = false;
                if (lp.loss > 0.0f) {
                    std::uniform_real_distribution<float> dist(0.0f, 1.0f);
                    lost = dist(_rng_loss) < lp.loss;
                }

                // Create PendingRx with sampled SNR
                PendingRx prx;
                prx.sender_idx = sender;
                prx.rx_start_ms = current_ms;
                prx.rx_end_ms = rx_end;
                prx.data.assign(cap.data.begin(), cap.data.end());
                prx.snr = rx_snr;
                prx.rssi = lp.rssi;
                prx.link_loss = lost;

                // Receiver radio timing for collision survival checks
                double t_sym = _nodes[receiver]->radio.getSymbolMs();
                double t_preamble = _nodes[receiver]->radio.getPreambleMs();
                int cr = _nodes[receiver]->radio.getCR();
                // FEC tolerance in symbols per coding rate (LoRa Hamming codes):
                // CR4/5 (5,4): 1 parity bit, detection only → 0
                // CR4/6 (6,4): detection only → 0
                // CR4/7 (7,4) Hamming: corrects 1 bit/codeword → 1 symbol through interleaver
                // CR4/8 (8,4) extended Hamming: also corrects 1 bit/codeword → 1 symbol
                //   (extra parity bit adds detection, not correction capacity)
                static const int fec_sym_table[] = {0, 0, 1, 1};  // CR4/5..CR4/8
                int fec_sym = (cr >= 1 && cr <= 4) ? fec_sym_table[cr - 1] : 0;
                double fec_tolerance_ms = fec_sym * t_sym;
                int pre_sym = _nodes[receiver]->radio.getPreambleSymbols();
                double preamble_grace_ms = (pre_sym - PREAMBLE_LOCK_SYMBOLS) * t_sym;

                // Collision check: test each direction independently
                for (auto& existing : _nodes[receiver]->active_rx) {
                    if (isDestroyedBy(prx, existing, preamble_grace_ms, fec_tolerance_ms, t_preamble, t_sym,
                                      _capture_locked_db, _capture_unlocked_db)) {
                        prx.collided = true;
                        // Track strongest interferer (highest SNR)
                        if (prx.interferer_idx < 0 || existing.snr > prx.interferer_snr) {
                            prx.interferer_idx = existing.sender_idx;
                            prx.interferer_snr = existing.snr;
                            double lock_time = prx.rx_start_ms + PREAMBLE_LOCK_SYMBOLS * t_sym;
                            float cap_thr = ((double)existing.rx_start_ms >= lock_time)
                                            ? _capture_locked_db : _capture_unlocked_db;
                            prx.snr_margin = existing.snr + cap_thr - prx.snr;
                        }
                    }
                    if (isDestroyedBy(existing, prx, preamble_grace_ms, fec_tolerance_ms, t_preamble, t_sym,
                                      _capture_locked_db, _capture_unlocked_db)) {
                        existing.collided = true;
                        // Track strongest interferer on existing entry
                        if (existing.interferer_idx < 0 || prx.snr > existing.interferer_snr) {
                            existing.interferer_idx = sender;
                            existing.interferer_snr = prx.snr;
                            double lock_time_ex = existing.rx_start_ms + PREAMBLE_LOCK_SYMBOLS * t_sym;
                            float cap_thr_ex = ((double)prx.rx_start_ms >= lock_time_ex)
                                               ? _capture_locked_db : _capture_unlocked_db;
                            existing.snr_margin = prx.snr + cap_thr_ex - existing.snr;
                        }
                    }
                }

                if (_verbose) {
                    if (prx.collided) {
                        fprintf(stderr, "[%8.3fs]   !  %s collision (snr=%.1f, lost)\n",
                                current_ms / 1000.0, rname, prx.snr);
                    } else if (prx.link_loss) {
                        fprintf(stderr, "[%8.3fs]   !  %s link-loss (p=%.3f, still interferes)\n",
                                current_ms / 1000.0, rname, lp.loss);
                    } else {
                        fprintf(stderr, "[%8.3fs]   -> %s queued (snr=%.1f rssi=%.1f, delivery@%.3fs)\n",
                                current_ms / 1000.0, rname, rx_snr, lp.rssi,
                                rx_end / 1000.0);
                    }
                }

                _nodes[receiver]->active_rx.push_back(std::move(prx));

                // Notify receiver's SimRadio of the active reception so
                // MeshCore's Dispatcher sees isReceiving()==true and defers TX.
                // Clock-domain safe: notifyRxStart uses node_clock + relative duration.
                if (!lost) {
                    _nodes[receiver]->radio.notifyRxStart(airtime32);
                }
            }
        }
        tx_list.clear();
    }
}

void Orchestrator::deliverReceptions(unsigned long current_ms) {
    int n = (int)_nodes.size();
    for (int i = 0; i < n; i++) {
        auto& arx = _nodes[i]->active_rx;
        // Partition: process completed entries (rx_end_ms <= current_ms)
        auto it = arx.begin();
        while (it != arx.end()) {
            if (it->rx_end_ms <= current_ms) {
                const char* sname = _nodes[it->sender_idx]->name.c_str();
                const char* rname = _nodes[i]->name.c_str();

                // Message fate tracking: look up packet hash
                uint32_t rx_hash = EventLog::packetHash(it->data.data(), (int)it->data.size());
                auto fate_it = _hash_to_fate.find(rx_hash);

                if (!it->collided && !it->link_loss && !it->halfduplex_abort) {
                    _rx_count++;
                    _event_counts["rx"]++;
                    _event_counts[_node_event_keys[i].rx]++;
                    if (isAckOrPathType(it->data.data(), (int)it->data.size())) {
                        _ackpath_rx++;
                        _event_counts["ackpath_rx"]++;
                        // ACK/PATH fate: track copies reaching original sender
                        auto ack_fate_it = _ackpath_hash_to_fate.find(rx_hash);
                        if (ack_fate_it != _ackpath_hash_to_fate.end()) {
                            int fi = ack_fate_it->second;
                            _ackpath_relay_fates[i].insert(fi);  // for relay linking
                            if (i == _message_fates[fi].from_idx) {
                                _message_fates[fi].ackpath_rx_at_sender++;
                            }
                        }
                    }
                    _nodes[i]->activate();
                    _nodes[i]->radio.enqueue(
                        it->data.data(), (int)it->data.size(), it->snr, it->rssi);
                    uint32_t rx_airtime = (uint32_t)(it->rx_end_ms - it->rx_start_ms);
                    // Use rx_start_ms so visualization bars align with TX bars (both use start time)
                    EventLog::rx(it->rx_start_ms, sname, rname, it->snr, it->rssi,
                                 it->data.data(), (int)it->data.size(), rx_airtime);
                    if (_verbose) {
                        fprintf(stderr, "[%8.3fs] RX %s <- %s %dB snr=%.1f\n",
                                it->rx_start_ms / 1000.0, rname, sname,
                                (int)it->data.size(), it->snr);
                    }
                } else if (it->halfduplex_abort) {
                    _event_counts["drop_halfduplex"]++;
                    _event_counts[_node_event_keys[i].drop_halfduplex]++;
                    if (isAckOrPathType(it->data.data(), (int)it->data.size())) {
                        _ackpath_drop++;
                        _event_counts["ackpath_drop"]++;
                    }
                    EventLog::dropHalfDuplex(it->rx_start_ms, sname, rname,
                                             it->data.data(), (int)it->data.size(),
                                             (uint32_t)(it->rx_end_ms - it->rx_start_ms));
                    if (_verbose) {
                        fprintf(stderr, "[%8.3fs] DROP-HD %s <- %s (tx during rx)\n",
                                it->rx_start_ms / 1000.0, rname, sname);
                    }
                } else if (it->link_loss) {
                    _event_counts["drop_loss"]++;
                    _event_counts[_node_event_keys[i].drop_loss]++;
                    if (isAckOrPathType(it->data.data(), (int)it->data.size())) {
                        _ackpath_drop++;
                        _event_counts["ackpath_drop"]++;
                    }
                    EventLog::dropLoss(it->rx_start_ms, sname, rname, 0.0f,
                                       it->data.data(), (int)it->data.size());
                    if (_verbose) {
                        fprintf(stderr, "[%8.3fs] LOST %s <- %s (link-loss)\n",
                                it->rx_start_ms / 1000.0, rname, sname);
                    }
                } else {
                    _event_counts["collision"]++;
                    _event_counts[_node_event_keys[i].collision]++;
                    if (isAckOrPathType(it->data.data(), (int)it->data.size())) {
                        _ackpath_collision++;
                        _event_counts["ackpath_collision"]++;
                    }
                    const char* iname = (it->interferer_idx >= 0)
                        ? _nodes[it->interferer_idx]->name.c_str() : nullptr;
                    EventLog::collision(it->rx_start_ms, sname, rname, it->snr, it->rssi,
                                        it->data.data(), (int)it->data.size(),
                                        iname, it->interferer_snr, it->snr_margin);
                    if (_verbose) {
                        if (iname) {
                            fprintf(stderr, "[%8.3fs] COLLIDED %s <- %s (destroyed by %s, margin=%.1fdB)\n",
                                    it->rx_start_ms / 1000.0, rname, sname, iname, it->snr_margin);
                        } else {
                            fprintf(stderr, "[%8.3fs] COLLIDED %s <- %s (destroyed)\n",
                                    it->rx_start_ms / 1000.0, rname, sname);
                        }
                    }
                }

                // Message fate: count this event for tracked messages
                if (fate_it != _hash_to_fate.end()) {
                    auto& fate = _message_fates[fate_it->second];
                    if (!it->collided && !it->link_loss && !it->halfduplex_abort) {
                        fate.rx_count++;
                        _step_rx_fates[i].insert(fate_it->second);
                        if (i == fate.to_idx) {
                            fate.delivered = true;
                            _pending_ackpath_fates[i].insert(fate_it->second);
                        }
                    } else if (it->collided) {
                        fate.collisions++;
                    } else {
                        fate.drops++;  // halfduplex or link_loss
                    }
                }

                it = arx.erase(it);
            } else {
                ++it;
            }
        }
    }
}

void Orchestrator::processCommands(unsigned long current_ms) {
    while (_next_cmd < _commands.size() && _commands[_next_cmd].at_ms <= current_ms) {
        const auto& cmd = _commands[_next_cmd];

        // Lua-only command: fire callback, skip node handling
        if (!cmd.lua_fn.empty()) {
            if (_lua_callback) {
                _lua_callback(cmd.lua_fn);
            }
            EventLog::luaCallback(current_ms, cmd.lua_fn.c_str());
            if (_verbose) {
                fprintf(stderr, "[%8.3fs] LUA callback: %s\n",
                        current_ms / 1000.0, cmd.lua_fn.c_str());
            }
            _next_cmd++;
            continue;
        }

        auto& node = _nodes[cmd.node_index];
        node->activate();
        // timestamp=0 = local/serial origin, unlocks stats-* commands in MeshCore
        std::string reply = node->mesh->handleCommand(0, cmd.command.c_str());
        _reply_log.push_back({node->name, cmd.command, reply});
        EventLog::cmdReply(current_ms, node->name.c_str(),
                          cmd.command.c_str(), reply.c_str());
        if (_verbose) {
            fprintf(stderr, "[%8.3fs] CMD %s: \"%s\" -> \"%s\"\n",
                    current_ms / 1000.0, node->name.c_str(),
                    cmd.command.c_str(), reply.c_str());
        }

        // Track message fates: detect "msg <dest>" or "msga <dest>" commands
        const std::string& c = cmd.command;
        bool is_msg = (c.size() > 4 && c.compare(0, 4, "msg ") == 0);
        bool is_msga = (c.size() > 5 && c.compare(0, 5, "msga ") == 0);
        if ((is_msg || is_msga) && c.find("msgc") != 0) {
            // Parse destination name (second token)
            size_t name_start = is_msga ? 5 : 4;
            size_t name_end = c.find(' ', name_start);
            if (name_end == std::string::npos) name_end = c.size();
            std::string dest = c.substr(name_start, name_end - name_start);
            int to_idx = findNode(dest);
            if (to_idx >= 0) {
                MessageFate fate;
                fate.from_idx = cmd.node_index;
                fate.to_idx = to_idx;
                fate.send_time_ms = current_ms;
                fate.sent_as_flood = (reply.find("(flood") != std::string::npos);
                int fate_idx = (int)_message_fates.size();
                _message_fates.push_back(std::move(fate));
                _pending_msg_fates[cmd.node_index] = fate_idx;
            }
        }

        _next_cmd++;
    }
}

void Orchestrator::injectReplays(unsigned long current_ms) {
    auto it = _pending_replays.begin();
    while (it != _pending_replays.end()) {
        if (it->emit_ms <= current_ms) {
            TxCapture cap;
            cap.data = std::move(it->data);
            cap.airtime_ms = it->airtime_ms;
            cap.is_replay = true;
            _nodes[it->sender_idx]->pending_tx.push_back(std::move(cap));
            if (_verbose) {
                fprintf(stderr, "[%8.3fs] REPLAY-INJECT %s %dB\n",
                        current_ms / 1000.0,
                        _nodes[it->sender_idx]->name.c_str(),
                        (int)_nodes[it->sender_idx]->pending_tx.back().data.size());
            }
            it = _pending_replays.erase(it);
        } else {
            ++it;
        }
    }
}

void Orchestrator::hotStart() {
    int n = (int)_nodes.size();

    // Collision-free advert exchange: trigger real advert commands on each node,
    // staggered 2s apart (Dispatcher needs ~1500ms), then run a collision-free
    // simulation loop with instant delivery (no physics, no event logging).
    // This lets MeshCore naturally populate neighbor/contact tables via its own
    // flood routing, including multi-hop propagation.
    //
    // Companion adverts need special handling: repeaters don't forward them
    // (Mesh::allowPacketForward returns false for non-flood packets), so we
    // inject companion adverts directly to all other companions.
    //
    // The loop runs until quiescence: after all adverts are triggered, we keep
    // running until no node transmits for QUIESCE_MS. This auto-adapts to any
    // network size/topology without manual settle tuning.

    const unsigned long ADVERT_SPACING_MS = 2000;
    const unsigned long ADVERT_PHASE_MS = ADVERT_SPACING_MS * n;
    const unsigned long QUIESCE_MS = 5000;  // no TX for this long = settled
    const unsigned long MAX_SETTLE_MS = 120000;  // safety cap on settle phase

    if (_verbose) {
        fprintf(stderr, "[hot-start] collision-free advert exchange: %d nodes, "
                "%lums advert phase, quiesce=%lums\n", n, ADVERT_PHASE_MS, QUIESCE_MS);
    }

    // Direct companion→companion advert injection (repeaters won't forward these).
    // Only inject between companions that are topologically connected (BFS through
    // all nodes/links). This prevents phantom routes between disconnected components
    // while correctly handling multi-hop networks.
    //
    // Build per-companion reachability set via BFS through the full link topology.
    std::vector<std::vector<bool>> companion_reach;  // [companion_seq] -> reachable[node_idx]
    std::vector<int> companion_indices;
    for (int i = 0; i < n; i++) {
        if (_nodes[i]->role != NodeRole::Companion) continue;
        companion_indices.push_back(i);
        // BFS from companion i
        std::vector<bool> visited(n, false);
        std::vector<int> queue;
        visited[i] = true;
        queue.push_back(i);
        for (size_t qi = 0; qi < queue.size(); qi++) {
            int cur = queue[qi];
            for (int nb = 0; nb < n; nb++) {
                if (visited[nb]) continue;
                LinkParams lp;
                if (_link_model->getLink(cur, nb, lp)) {
                    visited[nb] = true;
                    queue.push_back(nb);
                }
            }
        }
        companion_reach.push_back(std::move(visited));
    }

    for (size_t ci = 0; ci < companion_indices.size(); ci++) {
        int i = companion_indices[ci];
        _nodes[i]->activate();
        auto bytes = _nodes[i]->mesh->exportSelfAdvert();
        if (bytes.empty()) continue;
        for (size_t cj = 0; cj < companion_indices.size(); cj++) {
            if (ci == cj) continue;
            int j = companion_indices[cj];
            if (companion_reach[ci][j]) {
                _nodes[j]->activate();
                _nodes[j]->radio.enqueue(bytes.data(), (int)bytes.size(), 10.0f, -70.0f);
            }
        }
    }

    unsigned long ms = 0;
    unsigned long last_tx_ms = 0;
    bool adverts_done = false;

    while (true) {
        // Trigger advert on each node at its scheduled time
        if (ms < ADVERT_PHASE_MS) {
            int advert_idx = (int)(ms / ADVERT_SPACING_MS);
            if (advert_idx < n && ms == (unsigned long)advert_idx * ADVERT_SPACING_MS) {
                _nodes[advert_idx]->activate();
                uint32_t ts = _nodes[advert_idx]->own_clock.getCurrentTime();
                _nodes[advert_idx]->mesh->handleCommand(ts, "advert");
                last_tx_ms = ms;
                if (_verbose) {
                    fprintf(stderr, "[hot-start] %8.3fs advert on %s\n",
                            ms / 1000.0, _nodes[advert_idx]->name.c_str());
                }
            }
        } else if (!adverts_done) {
            adverts_done = true;
            if (_verbose) {
                fprintf(stderr, "[hot-start] %8.3fs all adverts triggered, waiting for quiescence\n",
                        ms / 1000.0);
            }
        }

        // Run all node loops
        for (auto& node : _nodes) {
            node->activate();
            node->mesh->loop();
        }

        // Collision-free instant delivery (no stats, no NDJSON logging)
        bool any_tx = false;
        for (int sender = 0; sender < n; sender++) {
            auto& tx_list = _nodes[sender]->pending_tx;
            if (!tx_list.empty()) any_tx = true;
            for (auto& cap : tx_list) {
                for (int receiver = 0; receiver < n; receiver++) {
                    if (receiver == sender) continue;
                    LinkParams lp;
                    if (_link_model->getLink(sender, receiver, lp)) {
                        _nodes[receiver]->activate();
                        _nodes[receiver]->radio.enqueue(
                            cap.data.data(), (int)cap.data.size(), lp.snr, lp.rssi);
                    }
                }
            }
            tx_list.clear();
        }

        if (any_tx) last_tx_ms = ms;

        // Advance clocks
        _clock.advanceMillis(_step_ms);
        for (auto& node : _nodes) {
            node->own_clock.advanceMillis(_step_ms);
        }
        ms += _step_ms;

        // Check quiescence: all adverts triggered + no TX for QUIESCE_MS
        if (adverts_done && (ms - last_tx_ms) >= QUIESCE_MS) break;
        // Safety cap: don't settle forever
        if (adverts_done && (ms - ADVERT_PHASE_MS) >= MAX_SETTLE_MS) {
            if (_verbose) {
                fprintf(stderr, "[hot-start] %8.3fs settle capped at %lums\n",
                        ms / 1000.0, MAX_SETTLE_MS);
            }
            break;
        }
    }

    if (_verbose) {
        fprintf(stderr, "[hot-start] complete at %.3fs (advert phase: %.1fs, settle: %.1fs)\n",
                ms / 1000.0, ADVERT_PHASE_MS / 1000.0, (ms - ADVERT_PHASE_MS) / 1000.0);
    }
}

unsigned long Orchestrator::initSimulation() {
    sim_clock_set_global(&_clock);

    int n = (int)_nodes.size();
    EventLog::simStart(0, n, _step_ms, _warmup_ms, _hot_start);
    fprintf(stderr, "[PROGRESS] init: %d nodes, step=%dms, duration=%.1fs\n",
            n, _step_ms, _duration_ms / 1000.0);
    if (_verbose) {
        fprintf(stderr, "[%8.3fs] SIM START: %d nodes, step=%dms, duration=%.1fs, warmup=%.1fs\n",
                0.0, n, _step_ms, _duration_ms / 1000.0, _warmup_ms / 1000.0);
    }

    // Initialize all nodes
    for (auto& node : _nodes) {
        node->initMesh(_seed);
        const char* role_str = (node->role == NodeRole::Companion) ? "companion" : "repeater";
        EventLog::nodeReady(0, node->name.c_str(), role_str,
                            node->mesh->pubKey(), 32,
                            node->has_location, node->lat, node->lon);
        if (_verbose) {
            // Print first 8 bytes of pubkey as hex
            const uint8_t* pk = node->mesh->pubKey();
            fprintf(stderr, "[%8.3fs] READY %s pub=%02x%02x%02x%02x...\n",
                    0.0, node->name.c_str(), pk[0], pk[1], pk[2], pk[3]);
        }
    }

    // Warn if step_ms exceeds minimum symbol time (timing accuracy check)
    {
        double min_t_sym = 1e9;
        for (const auto& node : _nodes) {
            double t_sym = node->radio.getSymbolMs();
            if (t_sym < min_t_sym) min_t_sym = t_sym;
        }
        if (min_t_sym < 1e9 && _step_ms > min_t_sym) {
            int recommended = (int)std::floor(min_t_sym);
            if (recommended < 1) recommended = 1;
            fprintf(stderr, "WARNING: step_ms=%d exceeds minimum symbol time %.1fms. "
                    "Recommend step_ms <= %d for timing accuracy.\n",
                    _step_ms, min_t_sym, recommended);
        }
    }

    // Stagger node clocks to prevent synchronized periodic timers.
    // Each node runs loop() independently for a random duration (TX suppressed)
    // so MeshCore processes time naturally — internal timers, scheduled events
    // etc. all fire correctly during the stagger.
    {
        std::uniform_int_distribution<unsigned long> stagger_dist(0, 120000);
        for (auto& node : _nodes) {
            unsigned long stagger = stagger_dist(_rng_stagger);
            // Round to step boundary for deterministic advancement
            unsigned long steps = stagger / _step_ms;
            for (unsigned long s = 0; s < steps; s++) {
                node->activate();
                node->mesh->loop();
                node->pending_tx.clear();
                node->own_clock.advanceMillis(_step_ms);
            }
        }
    }

    if (_hot_start) {
        fprintf(stderr, "[PROGRESS] hot-start: exchanging adverts (%d nodes)\n", n);
        hotStart();
        fprintf(stderr, "[PROGRESS] hot-start: complete\n");
        // Reset hardware delay timestamps to prevent stale hot-start values
        for (auto& node : _nodes) {
            node->radio.resetHardwareDelays();
        }
    }

    return 0;
}

unsigned long Orchestrator::executeStep(unsigned long current_ms) {
    bool in_warmup = (current_ms < _warmup_ms);

    processCommands(current_ms);

    if (!in_warmup) {
        deliverReceptions(current_ms);
    }

    for (size_t i = 0; i < _nodes.size(); i++) {
        auto& node = _nodes[i];
        node->activate();
        uint32_t fail_before = node->radio.getTxFailCount();
        node->mesh->loop();
        uint32_t new_fails = node->radio.getTxFailCount() - fail_before;
        if (new_fails > 0) {
            _event_counts["tx_fail"] += new_fails;
            _event_counts[_node_event_keys[i].tx_fail] += new_fails;
            if (_verbose) {
                fprintf(stderr, "[%8.3fs] TX-FAIL %s (%u failures)\n",
                        current_ms / 1000.0, node->name.c_str(), (unsigned)new_fails);
            }
            EventLog::txFail(current_ms, node->name.c_str(), new_fails);
        }
    }

    if (in_warmup) {
        routePackets(current_ms);
    } else {
        injectReplays(current_ms);
        registerTransmissions(current_ms);
    }

    // Clear per-step pending message fates (initial TX is same-step)
    _pending_msg_fates.clear();
    // Note: _step_rx_fates is NOT cleared per-step — Dispatcher may
    // delay relay TX by many steps. Cleared on consumption instead.

    current_ms += _step_ms;
    _clock.advanceMillis(_step_ms);
    for (auto& node : _nodes) {
        node->own_clock.advanceMillis(_step_ms);
    }

    return current_ms;
}

void Orchestrator::emitSummary(unsigned long current_ms) {
    fprintf(stderr, "[PROGRESS] 100%% (%.1fs / %.1fs)\n",
            current_ms / 1000.0, _duration_ms / 1000.0);

    // Deliver any remaining receptions at end
    deliverReceptions(current_ms);

    // Collect per-node radio/packet stats from repeaters
    for (auto& node : _nodes) {
        if (node->role != NodeRole::Repeater) continue;
        static const char* cmds[][2] = {
            {"stats-core", "core"},
            {"stats-radio", "radio"},
            {"stats-packets", "packets"},
        };
        for (auto& [cmd, stype] : cmds) {
            std::string reply = node->mesh->handleCommand(0, cmd);
            if (!reply.empty() && reply[0] == '{') {
                EventLog::nodeStats(current_ms, node->name.c_str(), stype, reply.c_str());
            }
        }
    }

    // Emit per-node message stats (JSON to stdout)
    int total_direct_sent = 0, total_direct_recv = 0;
    int total_group_sent = 0, total_group_recv = 0;
    int num_companions = 0;
    for (auto& node : _nodes) {
        if (node->role != NodeRole::Companion) continue;
        num_companions++;
        auto& s = node->mesh->msg_stats;
        printf("{\"type\":\"node_stats\",\"node\":\"%s\""
               ",\"sent_flood\":%d,\"sent_direct\":%d,\"sent_group\":%d"
               ",\"recv_direct\":%d,\"recv_group\":%d",
               node->name.c_str(),
               s.sent_flood, s.sent_direct, s.sent_group,
               s.totalRecvDirect(), s.recv_group);
        if (!s.sent_flood_to.empty()) {
            printf(",\"sent_flood_to\":{");
            bool first = true;
            for (auto& kv : s.sent_flood_to) {
                if (!first) printf(",");
                printf("\"%s\":%d", kv.first.c_str(), kv.second);
                first = false;
            }
            printf("}");
        }
        if (!s.sent_direct_to.empty()) {
            printf(",\"sent_direct_to\":{");
            bool first = true;
            for (auto& kv : s.sent_direct_to) {
                if (!first) printf(",");
                printf("\"%s\":%d", kv.first.c_str(), kv.second);
                first = false;
            }
            printf("}");
        }
        if (!s.recv_direct.empty()) {
            printf(",\"recv_direct_by_sender\":{");
            bool first = true;
            for (auto& kv : s.recv_direct) {
                if (!first) printf(",");
                printf("\"%s\":%d", kv.first.c_str(), kv.second);
                first = false;
            }
            printf("}");
        }
        if (!s.recv_group_by_sender.empty()) {
            printf(",\"recv_group_by_sender\":{");
            bool first = true;
            for (auto& kv : s.recv_group_by_sender) {
                if (!first) printf(",");
                printf("\"%s\":%d", kv.first.c_str(), kv.second);
                first = false;
            }
            printf("}");
        }
        if (s.acksPending() > 0) {
            printf(",\"acks_pending\":%d,\"acks_received\":%d",
                   s.acksPending(), s.acksReceived());
            if (s.acks_flood_pending > 0)
                printf(",\"acks_flood_pending\":%d,\"acks_flood_received\":%d",
                       s.acks_flood_pending, s.acks_flood_received);
            if (s.acks_direct_pending > 0)
                printf(",\"acks_direct_pending\":%d,\"acks_direct_received\":%d",
                       s.acks_direct_pending, s.acks_direct_received);
        }
        printf("}\n");
        total_direct_sent += s.sent_flood + s.sent_direct;
        total_direct_recv += s.totalRecvDirect();
        total_group_sent += s.sent_group;
        total_group_recv += s.recv_group;
    }

    EventLog::simEnd(current_ms);

    // Build name -> stats lookup for cross-referencing delivery
    std::map<std::string, MsgStats*> stats_by_name;
    for (auto& node : _nodes) {
        if (node->role != NodeRole::Companion) continue;
        stats_by_name[node->name] = &node->mesh->msg_stats;
    }

    // Summary to stderr (always visible)
    fprintf(stderr, "\n=== Simulation Summary (%.1fs) ===\n", current_ms / 1000.0);
    int ec_rx = _event_counts.count("rx") ? _event_counts["rx"] : 0;
    int ec_col = _event_counts.count("collision") ? _event_counts["collision"] : 0;
    int ec_dh = _event_counts.count("drop_halfduplex") ? _event_counts["drop_halfduplex"] : 0;
    int ec_dl = _event_counts.count("drop_loss") ? _event_counts["drop_loss"] : 0;
    int rx_opps = ec_rx + ec_col + ec_dh + ec_dl;
    double radio_eff = rx_opps > 0 ? (ec_rx * 100.0 / rx_opps) : 100.0;
    fprintf(stderr, "Radio: %d TX, %d RX, %d collision, %d drop (%.1f%% rx efficiency)\n",
            _tx_count, _rx_count, ec_col, ec_dh + ec_dl, radio_eff);

    int ap_opps = _ackpath_rx + _ackpath_collision + _ackpath_drop;
    double ap_eff = ap_opps > 0 ? (_ackpath_rx * 100.0 / ap_opps) : 100.0;
    fprintf(stderr, "ACK+path radio: %d TX, %d RX, %d collision, %d drop (%.1f%% rx efficiency)\n\n",
            _ackpath_tx, _ackpath_rx, _ackpath_collision, _ackpath_drop, ap_eff);

    // Per-companion sent with per-destination breakdown and delivery
    fprintf(stderr, "Sent messages:\n");
    for (auto& node : _nodes) {
        if (node->role != NodeRole::Companion) continue;
        auto& s = node->mesh->msg_stats;
        if (s.totalSent() == 0) continue;
        fprintf(stderr, "  %-12s %d (flood:%d direct:%d group:%d)\n",
                node->name.c_str(), s.totalSent(),
                s.sent_flood, s.sent_direct, s.sent_group);
        // Collect all destinations from both maps
        std::set<std::string> all_dests;
        for (auto& kv : s.sent_flood_to) all_dests.insert(kv.first);
        for (auto& kv : s.sent_direct_to) all_dests.insert(kv.first);
        for (auto& dest : all_dests) {
            int flood_n = 0, direct_n = 0;
            auto fi = s.sent_flood_to.find(dest);
            if (fi != s.sent_flood_to.end()) flood_n = fi->second;
            auto di = s.sent_direct_to.find(dest);
            if (di != s.sent_direct_to.end()) direct_n = di->second;
            int total_to = flood_n + direct_n;
            // Look up how many the destination received from this sender
            int delivered = 0;
            auto it = stats_by_name.find(dest);
            if (it != stats_by_name.end()) {
                auto recv_it = it->second->recv_direct.find(node->name);
                if (recv_it != it->second->recv_direct.end())
                    delivered = recv_it->second;
            }
            fprintf(stderr, "    -> %-10s %d sent (F:%d P:%d), %d delivered",
                    dest.c_str(), total_to, flood_n, direct_n, delivered);
            if (total_to > 0)
                fprintf(stderr, " (%d%%)", delivered * 100 / total_to);
            fprintf(stderr, "\n");
        }
    }
    if (total_direct_sent + total_group_sent == 0) fprintf(stderr, "  (none)\n");

    // Per-companion received, broken down by sender
    fprintf(stderr, "\nReceived messages:\n");
    for (auto& node : _nodes) {
        if (node->role != NodeRole::Companion) continue;
        auto& s = node->mesh->msg_stats;
        int recv_total = s.totalRecvDirect() + s.recv_group;
        if (recv_total == 0) continue;
        fprintf(stderr, "  %-12s %d", node->name.c_str(), recv_total);
        if (!s.recv_direct.empty()) {
            fprintf(stderr, "  <-");
            for (auto& kv : s.recv_direct) {
                fprintf(stderr, "  %s:%d", kv.first.c_str(), kv.second);
            }
        }
        if (s.recv_group > 0) {
            fprintf(stderr, "  group:%d", s.recv_group);
        }
        fprintf(stderr, "\n");
    }
    if (total_direct_recv + total_group_recv == 0) fprintf(stderr, "  (none)\n");

    // Delivery summary — direct messages (combined + split by routing type)
    int flood_sent = 0, flood_delivered = 0, path_sent = 0, path_delivered = 0;
    for (auto& f : _message_fates) {
        if (f.sent_as_flood) {
            flood_sent++;
            if (f.delivered) flood_delivered++;
        } else {
            path_sent++;
            if (f.delivered) path_delivered++;
        }
    }
    if (total_direct_sent > 0) {
        fprintf(stderr, "\nDelivery: %d/%d messages (%.0f%%)\n",
                total_direct_recv, total_direct_sent,
                total_direct_recv * 100.0 / total_direct_sent);
        if (flood_sent > 0)
            fprintf(stderr, "Delivery (flood): %d/%d (%.0f%%)\n",
                    flood_delivered, flood_sent, flood_delivered * 100.0 / flood_sent);
        if (path_sent > 0)
            fprintf(stderr, "Delivery (path): %d/%d (%.0f%%)\n",
                    path_delivered, path_sent, path_delivered * 100.0 / path_sent);
    } else if (total_group_sent == 0) {
        fprintf(stderr, "\nTotal: 0 sent, 0 received\n");
    }

    // Channel delivery — each sent msg should reach (num_companions - 1) others
    int chan_expected = (total_group_sent > 0 && num_companions > 1)
        ? total_group_sent * (num_companions - 1) : 0;
    if (chan_expected > 0) {
        fprintf(stderr, "Channel: %d/%d receptions (%.0f%%)\n",
                total_group_recv, chan_expected,
                total_group_recv * 100.0 / chan_expected);
    }

    // Ack summary (combined + split by routing type)
    int total_ack_flood_p = 0, total_ack_flood_r = 0;
    int total_ack_direct_p = 0, total_ack_direct_r = 0;
    for (auto& node : _nodes) {
        if (node->role != NodeRole::Companion) continue;
        auto& s = node->mesh->msg_stats;
        total_ack_flood_p += s.acks_flood_pending;
        total_ack_flood_r += s.acks_flood_received;
        total_ack_direct_p += s.acks_direct_pending;
        total_ack_direct_r += s.acks_direct_received;
    }
    int total_ack_pending = total_ack_flood_p + total_ack_direct_p;
    int total_ack_received = total_ack_flood_r + total_ack_direct_r;
    if (total_ack_pending > 0) {
        fprintf(stderr, "Acks: %d/%d received (%.0f%%)\n",
                total_ack_received, total_ack_pending,
                total_ack_received * 100.0 / total_ack_pending);
        if (total_ack_flood_p > 0)
            fprintf(stderr, "Acks (flood): %d/%d (%.0f%%)\n",
                    total_ack_flood_r, total_ack_flood_p,
                    total_ack_flood_r * 100.0 / total_ack_flood_p);
        if (total_ack_direct_p > 0)
            fprintf(stderr, "Acks (path): %d/%d (%.0f%%)\n",
                    total_ack_direct_r, total_ack_direct_p,
                    total_ack_direct_r * 100.0 / total_ack_direct_p);
    }

    // Message fate summary — per-message collision/drop breakdown
    int n_tracked = (int)_message_fates.size();
    int n_delivered = 0, n_lost = 0;
    double del_tx = 0, del_rx = 0, del_col = 0, del_drop = 0, del_ack = 0;
    double lost_tx = 0, lost_rx = 0, lost_col = 0, lost_drop = 0, lost_ack = 0;
    for (auto& f : _message_fates) {
        if (f.delivered) {
            n_delivered++;
            del_tx += f.tx_count;
            del_rx += f.rx_count;
            del_col += f.collisions;
            del_drop += f.drops;
            del_ack += f.ackpath_rx_at_sender;
        } else {
            n_lost++;
            lost_tx += f.tx_count;
            lost_rx += f.rx_count;
            lost_col += f.collisions;
            lost_drop += f.drops;
            lost_ack += f.ackpath_rx_at_sender;
        }
    }
    if (n_tracked > 0) {
        fprintf(stderr, "\nMessage fate (%d tracked, %d delivered, %d lost):\n",
                n_tracked, n_delivered, n_lost);
        if (n_delivered > 0) {
            fprintf(stderr, "  Per delivered message: mean tx=%.1f  rx=%.1f  collision=%.1f  drop=%.1f  ack_copies=%.1f\n",
                    del_tx / n_delivered, del_rx / n_delivered,
                    del_col / n_delivered, del_drop / n_delivered,
                    del_ack / n_delivered);
        }
        if (n_lost > 0) {
            fprintf(stderr, "  Per lost message:      mean tx=%.1f  rx=%.1f  collision=%.1f  drop=%.1f  ack_copies=%.1f\n",
                    lost_tx / n_lost, lost_rx / n_lost,
                    lost_col / n_lost, lost_drop / n_lost,
                    lost_ack / n_lost);
        }
    }

    // Emit sim_summary JSON event to stdout (for webapp consumption)
    printf("{\"type\":\"sim_summary\",\"time_ms\":%lu", (unsigned long)current_ms);
    // Radio
    printf(",\"radio\":{\"tx\":%d,\"rx\":%d,\"collision\":%d,\"drop\":%d,\"rx_efficiency\":%.1f}",
           _tx_count, _rx_count, ec_col, ec_dh + ec_dl, radio_eff);
    printf(",\"ackpath_radio\":{\"tx\":%d,\"rx\":%d,\"collision\":%d,\"drop\":%d,\"rx_efficiency\":%.1f}",
           _ackpath_tx, _ackpath_rx, _ackpath_collision, _ackpath_drop, ap_eff);
    // Delivery
    printf(",\"delivery\":{\"sent\":%d,\"received\":%d", total_direct_sent, total_direct_recv);
    printf(",\"flood\":{\"sent\":%d,\"received\":%d}", flood_sent, flood_delivered);
    printf(",\"path\":{\"sent\":%d,\"received\":%d}}", path_sent, path_delivered);
    // Channel
    printf(",\"channel\":{\"sent\":%d,\"expected\":%d,\"received\":%d}",
           total_group_sent, chan_expected, total_group_recv);
    // Acks
    printf(",\"acks\":{\"pending\":%d,\"received\":%d",
           total_ack_pending, total_ack_received);
    printf(",\"flood\":{\"pending\":%d,\"received\":%d}", total_ack_flood_p, total_ack_flood_r);
    printf(",\"path\":{\"pending\":%d,\"received\":%d}}", total_ack_direct_p, total_ack_direct_r);
    // Message fate
    if (n_tracked > 0) {
        printf(",\"fate\":{\"tracked\":%d,\"delivered\":%d,\"lost\":%d", n_tracked, n_delivered, n_lost);
        if (n_delivered > 0)
            printf(",\"delivered_mean\":{\"tx\":%.1f,\"rx\":%.1f,\"collision\":%.1f,\"drop\":%.1f,\"ack_copies\":%.1f}",
                   del_tx / n_delivered, del_rx / n_delivered,
                   del_col / n_delivered, del_drop / n_delivered,
                   del_ack / n_delivered);
        if (n_lost > 0)
            printf(",\"lost_mean\":{\"tx\":%.1f,\"rx\":%.1f,\"collision\":%.1f,\"drop\":%.1f,\"ack_copies\":%.1f}",
                   lost_tx / n_lost, lost_rx / n_lost,
                   lost_col / n_lost, lost_drop / n_lost,
                   lost_ack / n_lost);
        printf("}");
    }
    printf("}\n");
}

bool Orchestrator::run() {
    unsigned long current_ms = initSimulation();

    // Main simulation loop — batch mode with progress logging
    unsigned long next_progress_ms = 0;
    // Report progress every ~2% of duration (at least every 5s of sim time)
    unsigned long progress_interval_ms = _duration_ms / 50;
    if (progress_interval_ms < 5000) progress_interval_ms = 5000;
    bool running_logged = false;

    if (_warmup_ms > 0) {
        fprintf(stderr, "[PROGRESS] warmup: instant delivery for %.1fs\n",
                _warmup_ms / 1000.0);
    }

    while (current_ms < _duration_ms) {
        bool in_warmup = (current_ms < _warmup_ms);

        // Log phase transition from warmup to running
        if (!in_warmup && !running_logged) {
            running_logged = true;
            fprintf(stderr, "[PROGRESS] running: physics simulation started\n");
        }

        // Periodic progress: emit sim time / percentage
        if (current_ms >= next_progress_ms) {
            int pct = (int)((uint64_t)current_ms * 100 / _duration_ms);
            fprintf(stderr, "[PROGRESS] %d%% (%.1fs / %.1fs)\n",
                    pct, current_ms / 1000.0, _duration_ms / 1000.0);
            next_progress_ms = current_ms + progress_interval_ms;
        }

        current_ms = executeStep(current_ms);
    }

    emitSummary(current_ms);
    return checkAssertions();
}

bool Orchestrator::checkAssertions() {
    if (_assertions.empty()) return true;

    int pass = 0, fail = 0;
    for (const auto& a : _assertions) {
        bool ok = false;

        if (a.type == "cmd_reply_contains") {
            for (const auto& r : _reply_log) {
                // Match command prefix with word boundary (exact or followed by space)
                if (r.node == a.node && r.command.find(a.command) == 0
                    && (r.command.size() == a.command.size() || r.command[a.command.size()] == ' ')
                    && r.reply.find(a.value) != std::string::npos) {
                    ok = true;
                    break;
                }
            }
        } else if (a.type == "cmd_reply_not_contains") {
            ok = true;  // passes unless we find a match
            for (const auto& r : _reply_log) {
                if (r.node == a.node && r.command.find(a.command) == 0
                    && (r.command.size() == a.command.size() || r.command[a.command.size()] == ' ')
                    && r.reply.find(a.value) != std::string::npos) {
                    ok = false;
                    break;
                }
            }
        } else if (a.type == "event_count_min") {
            int actual = 0;
            if (a.value == "tx") actual = _tx_count;
            else if (a.value == "rx") actual = _rx_count;
            ok = (actual >= a.count);
            if (!ok) {
                fprintf(stderr, "  FAIL: %s >= %d, got %d\n", a.value.c_str(), a.count, actual);
            }
        } else if (a.type == "event_count") {
            // Generic event count assertion: match event_type + optional node filter
            std::string key = a.event_type;
            if (!a.node.empty()) key += ":" + a.node;
            int actual = 0;
            auto it = _event_counts.find(key);
            if (it != _event_counts.end()) actual = it->second;
            ok = true;
            if (a.min >= 0 && actual < a.min) ok = false;
            if (a.max >= 0 && actual > a.max) ok = false;
            if (!ok) {
                fprintf(stderr, "  FAIL: event_count type=%s node=%s actual=%d min=%d max=%d\n",
                        a.event_type.c_str(), a.node.c_str(), actual, a.min, a.max);
            }
        } else if (a.type == "tx_airtime_between") {
            // Check that all TX from a.node have airtime in [a.min, a.max]
            bool found = false;
            ok = true;
            for (const auto& tx : _tx_log) {
                if (!a.node.empty() && tx.node != a.node) continue;
                found = true;
                int at = (int)tx.airtime_ms;
                if (at < a.min || at > a.max) {
                    ok = false;
                    fprintf(stderr, "  FAIL: tx_airtime_between node=%s airtime=%d not in [%d, %d]\n",
                            tx.node.c_str(), at, a.min, a.max);
                    break;
                }
            }
            if (!found) {
                ok = false;
                fprintf(stderr, "  FAIL: tx_airtime_between node=%s — no TX events found\n",
                        a.node.c_str());
            }
        } else {
            fprintf(stderr, "  UNKNOWN assertion type: %s\n", a.type.c_str());
        }

        if (ok) {
            pass++;
        } else {
            fail++;
            if (a.type.find("cmd_reply") == 0) {
                fprintf(stderr, "  FAIL: %s node=%s cmd=\"%s\" value=\"%s\"\n",
                        a.type.c_str(), a.node.c_str(), a.command.c_str(), a.value.c_str());
            }
        }
    }

    fprintf(stderr, "ASSERTIONS: %d passed, %d failed out of %d\n",
            pass, fail, (int)_assertions.size());
    return fail == 0;
}
