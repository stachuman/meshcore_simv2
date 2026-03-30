// STL before Arduino.h
#include <string>
#include <vector>
#include <memory>
#include <algorithm>
#include <cstdio>

#include "Orchestrator.h"
#include "MeshWrapper.h"
#include "SimClock.h"

static constexpr float CAPTURE_THRESHOLD_DB = 6.0f;

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
    _warmup_ms = cfg.warmup_ms;
    _verbose = cfg.verbose;
    _clock = VirtualClock(cfg.epoch_start);

    _pending_replays.clear();
    _reply_log.clear();
    _assertions = cfg.assertions;
    _tx_count = 0;
    _rx_count = 0;

    // Create nodes
    for (const auto& nd : cfg.nodes) {
        auto ctx = std::make_unique<NodeContext>(nd.name, nd.role, _clock, nd.sf, nd.bw, nd.cr);
        ctx->adversarial = nd.adversarial;
        ctx->lat = nd.lat;
        ctx->lon = nd.lon;
        ctx->has_location = nd.has_location;
        _nodes.push_back(std::move(ctx));
    }

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

    _hot_start = cfg.hot_start;
    _hot_start_settle_ms = cfg.hot_start_settle_ms;

    // Build scheduled commands (sorted by time)
    for (const auto& cd : cfg.commands) {
        int idx = findNode(cd.node);
        if (idx < 0) {
            fprintf(stderr, "Warning: command references unknown node: %s\n", cd.node.c_str());
            continue;
        }
        _commands.push_back({cd.at_ms, idx, cd.command});
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
                                 cap.data.data(), (int)cap.data.size());
                }
            }
        }
        tx_list.clear();
    }
}

// Check if 'primary' packet survives interference from 'interferer' using
// 3-stage survival: (1) capture effect, (2) preamble grace, (3) FEC tolerance.
// Returns true if primary is destroyed by interferer.
static bool isDestroyedBy(const PendingRx& primary, const PendingRx& interferer,
                          double preamble_grace_ms, double fec_tolerance_ms,
                          double t_preamble_ms) {
    // No temporal overlap → no interference
    if (interferer.rx_end_ms <= primary.rx_start_ms ||
        primary.rx_end_ms <= interferer.rx_start_ms)
        return false;

    // Stage 1: Capture — primary survives if it's much stronger
    if (primary.snr >= interferer.snr + CAPTURE_THRESHOLD_DB)
        return false;

    // Stage 2: Preamble grace — primary survives if interferer only
    // hits the non-critical part of primary's preamble (first 3 of 8 symbols)
    unsigned long critical = primary.rx_start_ms + (unsigned long)preamble_grace_ms;
    if (interferer.rx_end_ms <= critical)
        return false;

    // Also check: if interferer starts after primary's preamble is done and
    // only overlaps briefly, FEC might save it (handled in stage 3).

    // Stage 3: FEC overlap tolerance — if overlap is small and within payload,
    // forward error correction can recover.
    if (fec_tolerance_ms > 0.0) {
        unsigned long overlap_start = (primary.rx_start_ms > interferer.rx_start_ms)
                                      ? primary.rx_start_ms : interferer.rx_start_ms;
        unsigned long overlap_end = (primary.rx_end_ms < interferer.rx_end_ms)
                                    ? primary.rx_end_ms : interferer.rx_end_ms;
        double overlap_ms = (double)(overlap_end - overlap_start);
        if (overlap_ms <= fec_tolerance_ms) {
            unsigned long payload_start = primary.rx_start_ms + (unsigned long)t_preamble_ms;
            if (overlap_start >= payload_start)
                return false;
        }
    }

    return true;  // primary is destroyed
}

void Orchestrator::registerTransmissions(unsigned long current_ms) {
    int n = (int)_nodes.size();

    // Adversarial pre-pass: modify pending_tx before routing.
    // Only applies to non-replay TXes from adversarial nodes.
    for (int sender = 0; sender < n; sender++) {
        auto& tx_list = _nodes[sender]->pending_tx;
        if (tx_list.empty()) continue;
        const auto& adv = _nodes[sender]->adversarial;
        if (adv.mode == AdversarialMode::None) continue;

        auto it = tx_list.begin();
        while (it != tx_list.end()) {
            if (it->is_replay) { ++it; continue; }

            // Roll probability
            std::uniform_real_distribution<float> prob(0.0f, 1.0f);
            if (prob(_rng) >= adv.probability) { ++it; continue; }

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
                    std::uniform_int_distribution<int> bit_dist(0, 7);
                    it->data[byte_dist(_rng)] ^= (1 << bit_dist(_rng));
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
    for (int sender = 0; sender < n; sender++) {
        auto& tx_list = _nodes[sender]->pending_tx;
        if (tx_list.empty()) continue;
        for (auto& cap : tx_list) {
            unsigned long end = current_ms + cap.airtime_ms;
            if (end > _nodes[sender]->tx_busy_until)
                _nodes[sender]->tx_busy_until = end;
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
            EventLog::tx(current_ms, _nodes[sender]->name.c_str(),
                         cap.data.data(), (int)cap.data.size(), cap.airtime_ms);
            if (_verbose) {
                fprintf(stderr, "[%8.3fs] TX %s %dB airtime=%lums\n",
                        current_ms / 1000.0, _nodes[sender]->name.c_str(),
                        (int)cap.data.size(), airtime);
            }

            for (int receiver = 0; receiver < n; receiver++) {
                if (receiver == sender) continue;
                LinkParams lp;
                if (!_link_model->getLink(sender, receiver, lp)) continue;

                const char* sname = _nodes[sender]->name.c_str();
                const char* rname = _nodes[receiver]->name.c_str();

                // Sample per-reception SNR from Gaussian(mean, std_dev)
                float rx_snr = lp.snr;
                if (lp.snr_std_dev > 0.0f) {
                    std::normal_distribution<float> dist(lp.snr, lp.snr_std_dev);
                    rx_snr = dist(_rng);
                }

                // SNR check: use receiver's radio SF threshold.
                // Below-threshold signals are too weak for both decoding AND
                // preamble detection (CAD), so no LBT notification either.
                float score = _nodes[receiver]->radio.packetScore(rx_snr, (int)cap.data.size());
                if (score <= 0.0f) {
                    float thr = _nodes[receiver]->radio.getSnrThreshold();
                    EventLog::dropWeak(current_ms, sname, rname, rx_snr, thr,
                                       cap.data.data(), (int)cap.data.size());
                    if (_verbose) {
                        fprintf(stderr, "[%8.3fs]   X  %s weak (snr=%.1f < %.1f)\n",
                                current_ms / 1000.0, rname, rx_snr, thr);
                    }
                    continue;
                }

                // LBT: notify receiver of channel activity. Signal is above
                // threshold, so CAD would detect the preamble after ~5 symbols.
                uint32_t preamble_detect_ms = _nodes[receiver]->radio.getPreambleDetectMs();
                unsigned long lbt_from = current_ms + preamble_detect_ms;
                if (lbt_from < rx_end) {
                    _nodes[receiver]->radio.notifyChannelBusy(lbt_from, rx_end);
                }

                // Half-duplex check: receiver is currently transmitting
                if (_nodes[receiver]->tx_busy_until > current_ms) {
                    EventLog::dropHalfDuplex(current_ms, sname, rname,
                                             cap.data.data(), (int)cap.data.size());
                    if (_verbose) {
                        fprintf(stderr, "[%8.3fs]   X  %s half-duplex (tx until %.3fs)\n",
                                current_ms / 1000.0, rname,
                                _nodes[receiver]->tx_busy_until / 1000.0);
                    }
                    continue;
                }

                // Stochastic link loss: mark for drop but still create PendingRx
                // so the RF energy participates in collision detection.
                bool lost = false;
                if (lp.loss > 0.0f) {
                    std::uniform_real_distribution<float> dist(0.0f, 1.0f);
                    lost = dist(_rng) < lp.loss;
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
                static const int fec_sym_table[] = {0, 0, 1, 2};  // CR4/5..CR4/8
                int fec_sym = (cr >= 1 && cr <= 4) ? fec_sym_table[cr - 1] : 0;
                double fec_tolerance_ms = fec_sym * t_sym;
                int pre_sym = _nodes[receiver]->radio.getPreambleSymbols();
                double preamble_grace_ms = (pre_sym - 5) * t_sym;

                // Collision check: test each direction independently
                for (auto& existing : _nodes[receiver]->active_rx) {
                    if (isDestroyedBy(prx, existing, preamble_grace_ms, fec_tolerance_ms, t_preamble))
                        prx.collided = true;
                    if (isDestroyedBy(existing, prx, preamble_grace_ms, fec_tolerance_ms, t_preamble))
                        existing.collided = true;
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
                if (!it->collided && !it->link_loss) {
                    _rx_count++;
                    _nodes[i]->activate();
                    _nodes[i]->radio.enqueue(
                        it->data.data(), (int)it->data.size(), it->snr, it->rssi);
                    EventLog::rx(current_ms, sname, rname, it->snr, it->rssi,
                                 it->data.data(), (int)it->data.size());
                    if (_verbose) {
                        fprintf(stderr, "[%8.3fs] RX %s <- %s %dB snr=%.1f\n",
                                current_ms / 1000.0, rname, sname,
                                (int)it->data.size(), it->snr);
                    }
                } else if (it->link_loss) {
                    EventLog::dropLoss(current_ms, sname, rname, 0.0f,
                                       it->data.data(), (int)it->data.size());
                    if (_verbose) {
                        fprintf(stderr, "[%8.3fs] LOST %s <- %s (link-loss)\n",
                                current_ms / 1000.0, rname, sname);
                    }
                } else {
                    EventLog::collision(current_ms, sname, rname, it->snr, it->rssi,
                                        it->data.data(), (int)it->data.size());
                    if (_verbose) {
                        fprintf(stderr, "[%8.3fs] COLLIDED %s <- %s (destroyed)\n",
                                current_ms / 1000.0, rname, sname);
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
        auto& node = _nodes[cmd.node_index];
        node->activate();
        uint32_t ts = _clock.getCurrentTime();
        std::string reply = node->mesh->handleCommand(ts, cmd.command.c_str());
        _reply_log.push_back({node->name, cmd.command, reply});
        EventLog::cmdReply(current_ms, node->name.c_str(),
                          cmd.command.c_str(), reply.c_str());
        if (_verbose) {
            fprintf(stderr, "[%8.3fs] CMD %s: \"%s\" -> \"%s\"\n",
                    current_ms / 1000.0, node->name.c_str(),
                    cmd.command.c_str(), reply.c_str());
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
    const unsigned long SETTLE_MS = _hot_start_settle_ms;
    int n = (int)_nodes.size();

    // Phase 1: Collect advert bytes from all nodes via exportSelfAdvert().
    // No flood traffic is triggered — just packet serialization.
    struct AdvertInfo {
        int idx;
        NodeRole role;
        std::vector<uint8_t> data;
    };
    std::vector<AdvertInfo> adverts;

    for (int i = 0; i < n; i++) {
        _nodes[i]->activate();
        auto bytes = _nodes[i]->mesh->exportSelfAdvert();
        if (!bytes.empty()) {
            adverts.push_back({i, _nodes[i]->role, std::move(bytes)});
        }
    }

    // Phase 2: Direct injection into radio queues.
    //  - Companion advert → all other companions (can't discover via mesh)
    //  - Any advert → direct link neighbors (0-hop)
    for (auto& adv : adverts) {
        for (int recv = 0; recv < n; recv++) {
            if (recv == adv.idx) continue;

            bool deliver = false;
            float snr = 10.0f, rssi = -70.0f;

            // Companion → all other companions
            if (adv.role == NodeRole::Companion && _nodes[recv]->role == NodeRole::Companion) {
                deliver = true;
            }

            // Any node → direct link neighbors
            LinkParams lp;
            if (_link_model->getLink(adv.idx, recv, lp)) {
                deliver = true;
                snr = lp.snr;
                rssi = lp.rssi;
            }

            if (deliver) {
                _nodes[recv]->activate();
                _nodes[recv]->radio.enqueue(
                    adv.data.data(), (int)adv.data.size(), snr, rssi);
            }
        }
    }

    if (_verbose) {
        fprintf(stderr, "[hot-start] injected %d adverts, settling for %lums\n",
                (int)adverts.size(), SETTLE_MS);
    }

    // Phase 3: Settle — run loop() with all TX suppressed.
    // Nodes process injected adverts and populate contact/neighbor tables.
    // Any TX generated (re-floods, periodic timers) is discarded.
    for (unsigned long ms = 0; ms < SETTLE_MS; ms += _step_ms) {
        for (auto& node : _nodes) {
            node->activate();
            node->mesh->loop();
        }
        for (auto& node : _nodes) {
            node->pending_tx.clear();
        }
        _clock.advanceMillis(_step_ms);
    }

    if (_verbose) {
        fprintf(stderr, "[hot-start] complete\n");
    }
}

bool Orchestrator::run() {
    sim_clock_set_global(&_clock);

    int n = (int)_nodes.size();
    EventLog::simStart(0, n, _step_ms);
    if (_verbose) {
        fprintf(stderr, "[%8.3fs] SIM START: %d nodes, step=%dms, duration=%.1fs, warmup=%.1fs\n",
                0.0, n, _step_ms, _duration_ms / 1000.0, _warmup_ms / 1000.0);
    }

    // Initialize all nodes
    for (auto& node : _nodes) {
        node->initMesh();
        EventLog::nodeReady(0, node->name.c_str(),
                            node->mesh->pubKey(), 32,
                            node->has_location, node->lat, node->lon);
        if (_verbose) {
            // Print first 8 bytes of pubkey as hex
            const uint8_t* pk = node->mesh->pubKey();
            fprintf(stderr, "[%8.3fs] READY %s pub=%02x%02x%02x%02x...\n",
                    0.0, node->name.c_str(), pk[0], pk[1], pk[2], pk[3]);
        }
    }

    if (_hot_start) hotStart();

    // Main simulation loop
    unsigned long current_ms = 0;
    while (current_ms < _duration_ms) {
        bool in_warmup = (current_ms < _warmup_ms);

        processCommands(current_ms);

        if (!in_warmup) {
            deliverReceptions(current_ms);
        }

        for (auto& node : _nodes) {
            node->activate();
            node->mesh->loop();
        }

        if (in_warmup) {
            routePackets(current_ms);
        } else {
            injectReplays(current_ms);
            registerTransmissions(current_ms);
        }

        current_ms += _step_ms;
        _clock.advanceMillis(_step_ms);
    }

    // Deliver any remaining receptions at end
    deliverReceptions(current_ms);

    // Emit per-node message stats (JSON to stdout)
    int total_sent = 0, total_recv = 0;
    for (auto& node : _nodes) {
        if (node->role != NodeRole::Companion) continue;
        auto& s = node->mesh->msg_stats;
        printf("{\"type\":\"node_stats\",\"node\":\"%s\""
               ",\"sent_flood\":%d,\"sent_direct\":%d,\"sent_group\":%d"
               ",\"recv_direct\":%d,\"recv_group\":%d",
               node->name.c_str(),
               s.sent_flood, s.sent_direct, s.sent_group,
               s.totalRecvDirect(), s.recv_group);
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
        printf("}\n");
        total_sent += s.totalSent();
        total_recv += s.totalRecvDirect() + s.recv_group;
    }

    EventLog::simEnd(current_ms);

    // Summary to stderr (always visible)
    fprintf(stderr, "\n=== Simulation Summary (%.1fs) ===\n", current_ms / 1000.0);
    fprintf(stderr, "Radio: %d TX, %d RX\n\n", _tx_count, _rx_count);

    // Per-companion sent
    fprintf(stderr, "Sent messages:\n");
    for (auto& node : _nodes) {
        if (node->role != NodeRole::Companion) continue;
        auto& s = node->mesh->msg_stats;
        if (s.totalSent() == 0) continue;
        fprintf(stderr, "  %-12s %d (flood:%d direct:%d group:%d)\n",
                node->name.c_str(), s.totalSent(),
                s.sent_flood, s.sent_direct, s.sent_group);
    }
    if (total_sent == 0) fprintf(stderr, "  (none)\n");

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
    if (total_recv == 0) fprintf(stderr, "  (none)\n");

    fprintf(stderr, "\nTotal: %d sent, %d received\n", total_sent, total_recv);

    return checkAssertions();
}

bool Orchestrator::checkAssertions() {
    if (_assertions.empty()) return true;

    int pass = 0, fail = 0;
    for (const auto& a : _assertions) {
        bool ok = false;

        if (a.type == "cmd_reply_contains") {
            for (const auto& r : _reply_log) {
                if (r.node == a.node && r.command.find(a.command) == 0
                    && r.reply.find(a.value) != std::string::npos) {
                    ok = true;
                    break;
                }
            }
        } else if (a.type == "cmd_reply_not_contains") {
            ok = true;  // passes unless we find a match
            for (const auto& r : _reply_log) {
                if (r.node == a.node && r.command.find(a.command) == 0
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
