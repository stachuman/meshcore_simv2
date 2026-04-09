// Include json.hpp and STL FIRST, before anything pulls in Arduino.h min/max macros
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <set>
#include "third_party/json.hpp"

#include "JsonConfig.h"

// Undo min/max macros from Arduino.h (pulled in via Orchestrator.h -> NodeContext.h)
#undef min
#undef max

using json = nlohmann::json;

static NodeRole parseRole(const std::string& s) {
    if (s == "repeater")  return NodeRole::Repeater;
    if (s == "companion") return NodeRole::Companion;
    throw std::runtime_error("Unknown node role: \"" + s + "\" (expected \"repeater\" or \"companion\")");
}

static AdversarialMode parseAdversarialMode(const std::string& s) {
    if (s == "drop")    return AdversarialMode::Drop;
    if (s == "corrupt") return AdversarialMode::Corrupt;
    if (s == "replay")  return AdversarialMode::Replay;
    throw std::runtime_error("Unknown adversarial mode: \"" + s + "\" (expected \"drop\", \"corrupt\", or \"replay\")");
}

static OrchestratorConfig parseJson(const json& j) {
    OrchestratorConfig cfg;

    if (j.contains("simulation")) {
        auto& sim = j["simulation"];
        if (sim.contains("duration_ms")) cfg.duration_ms = sim["duration_ms"].get<unsigned long>();
        if (sim.contains("step_ms"))     cfg.step_ms = sim["step_ms"].get<int>();
        if (sim.contains("epoch_start")) cfg.epoch_start = sim["epoch_start"].get<uint32_t>();
        if (sim.contains("warmup_ms"))  cfg.warmup_ms = sim["warmup_ms"].get<unsigned long>();
        if (sim.contains("hot_start"))  cfg.hot_start = sim["hot_start"].get<bool>();
        if (sim.contains("seed")) cfg.seed = sim["seed"].get<uint64_t>();
        if (sim.contains("radio")) {
            auto& r = sim["radio"];
            if (r.contains("sf")) cfg.sf = r["sf"].get<int>();
            if (r.contains("bw")) cfg.bw = r["bw"].get<int>();
            if (r.contains("cr")) cfg.cr = r["cr"].get<int>();
            if (r.contains("capture_locked_db"))   cfg.capture_locked_db = r["capture_locked_db"].get<float>();
            if (r.contains("capture_unlocked_db")) cfg.capture_unlocked_db = r["capture_unlocked_db"].get<float>();
            if (r.contains("cad_miss_prob"))        cfg.cad_miss_prob = r["cad_miss_prob"].get<float>();
            if (r.contains("cad_reliable_snr"))     cfg.cad_reliable_snr = r["cad_reliable_snr"].get<float>();
            if (r.contains("cad_marginal_snr"))     cfg.cad_marginal_snr = r["cad_marginal_snr"].get<float>();
            if (r.contains("snr_coherence_ms"))     cfg.snr_coherence_ms = r["snr_coherence_ms"].get<float>();
            if (r.contains("hardware")) {
                auto& hw = r["hardware"];
                if (hw.contains("rx_to_tx_delay_ms")) cfg.rx_to_tx_delay_ms = hw["rx_to_tx_delay_ms"].get<float>();
                if (hw.contains("tx_to_rx_delay_ms")) cfg.tx_to_rx_delay_ms = hw["tx_to_rx_delay_ms"].get<float>();
            }
        }
    }

    if (j.contains("nodes")) {
        for (auto& nd : j["nodes"]) {
            OrchestratorConfig::NodeDef def;
            def.name = nd["name"].get<std::string>();
            if (nd.contains("role")) def.role = parseRole(nd["role"].get<std::string>());
            if (nd.contains("radio")) {
                auto& r = nd["radio"];
                if (r.contains("sf")) def.sf = r["sf"].get<int>();
                if (r.contains("bw")) def.bw = r["bw"].get<int>();
                if (r.contains("cr")) def.cr = r["cr"].get<int>();
            }
            // Also accept flat sf/bw/cr at node level
            if (nd.contains("sf")) def.sf = nd["sf"].get<int>();
            if (nd.contains("bw")) def.bw = nd["bw"].get<int>();
            if (nd.contains("cr")) def.cr = nd["cr"].get<int>();
            if (nd.contains("lat") && nd.contains("lon")) {
                def.lat = nd["lat"].get<double>();
                def.lon = nd["lon"].get<double>();
                def.has_location = true;
            }
            if (nd.contains("tx_fail_prob"))
                def.tx_fail_prob = nd["tx_fail_prob"].get<float>();
            if (nd.contains("adversarial")) {
                auto& adv = nd["adversarial"];
                if (adv.contains("mode"))
                    def.adversarial.mode = parseAdversarialMode(adv["mode"].get<std::string>());
                if (adv.contains("probability"))
                    def.adversarial.probability = adv["probability"].get<float>();
                if (adv.contains("corrupt_bits"))
                    def.adversarial.corrupt_bits = adv["corrupt_bits"].get<int>();
                if (adv.contains("replay_delay_ms"))
                    def.adversarial.replay_delay_ms = adv["replay_delay_ms"].get<unsigned long>();
            }
            cfg.nodes.push_back(std::move(def));
        }
    }

    // Merge global radio defaults into nodes where not explicitly set
    for (auto& nd : cfg.nodes) {
        if (nd.sf == -1) nd.sf = cfg.sf;
        if (nd.bw == -1) nd.bw = cfg.bw;
        if (nd.cr == -1) nd.cr = cfg.cr;
    }

    if (j.contains("topology")) {
        auto& topo = j["topology"];
        if (topo.contains("links")) {
            for (auto& lk : topo["links"]) {
                OrchestratorConfig::LinkDef def;
                def.from = lk["from"].get<std::string>();
                def.to   = lk["to"].get<std::string>();
                if (lk.contains("snr"))         def.snr  = lk["snr"].get<float>();
                if (lk.contains("rssi"))        def.rssi = lk["rssi"].get<float>();
                if (lk.contains("snr_std_dev")) def.snr_std_dev = lk["snr_std_dev"].get<float>();
                if (lk.contains("loss"))        def.loss = lk["loss"].get<float>();
                if (lk.contains("bidir"))       def.bidir = lk["bidir"].get<bool>();
                cfg.links.push_back(std::move(def));
            }
        }
    }

    if (j.contains("commands")) {
        for (auto& cd : j["commands"]) {
            unsigned long at_ms = cd["at_ms"].get<unsigned long>();
            std::string node    = cd["node"].get<std::string>();
            std::string command = cd["command"].get<std::string>();

            if (node[0] == '@') {
                // Expand @repeaters, @companions, @all into per-node commands
                for (const auto& nd : cfg.nodes) {
                    bool match = (node == "@all")
                              || (node == "@repeaters"  && nd.role == NodeRole::Repeater)
                              || (node == "@companions" && nd.role == NodeRole::Companion);
                    if (match) {
                        OrchestratorConfig::CmdDef def;
                        def.at_ms   = at_ms;
                        def.node    = nd.name;
                        def.command = command;
                        cfg.commands.push_back(std::move(def));
                    }
                }
                if (node != "@all" && node != "@repeaters" && node != "@companions")
                    throw std::runtime_error("Unknown target group \"" + node +
                        "\" (expected @all, @repeaters, or @companions)");
            } else {
                OrchestratorConfig::CmdDef def;
                def.at_ms   = at_ms;
                def.node    = node;
                def.command = command;
                cfg.commands.push_back(std::move(def));
            }
        }
    }

    // Expand message_schedule into CmdDef entries
    if (j.contains("message_schedule")) {
        for (auto& ms : j["message_schedule"]) {
            std::string from = ms["from"].get<std::string>();
            std::string to   = ms["to"].get<std::string>();
            unsigned long start_ms    = ms.value("start_ms", 10000UL);
            unsigned long interval_ms = ms["interval_ms"].get<unsigned long>();
            std::string msg_template  = ms.value("message", std::string("test msg {n}"));

            int count;
            if (ms.contains("count")) {
                count = ms["count"].get<int>();
            } else {
                // Auto-fill: generate messages until duration_ms - 10000
                unsigned long end_ms = (cfg.duration_ms > 10000) ? cfg.duration_ms - 10000 : cfg.duration_ms;
                if (end_ms > start_ms && interval_ms > 0) {
                    count = static_cast<int>((end_ms - start_ms) / interval_ms) + 1;
                } else {
                    count = 1;
                }
            }

            for (int i = 0; i < count; i++) {
                unsigned long at_ms = start_ms + static_cast<unsigned long>(i) * interval_ms;
                if (at_ms >= cfg.duration_ms) break;

                // Replace {n} with 1-based sequence number
                std::string text = msg_template;
                std::string seq = std::to_string(i + 1);
                size_t pos = 0;
                while ((pos = text.find("{n}", pos)) != std::string::npos) {
                    text.replace(pos, 3, seq);
                    pos += seq.length();
                }

                OrchestratorConfig::CmdDef def;
                def.at_ms   = at_ms;
                def.node    = from;
                std::string cmd = ms.value("ack", false) ? "msga " : "msg ";
                def.command = cmd + to + " " + text;
                cfg.commands.push_back(std::move(def));
            }
        }
    }

    // Expand channel_schedule into CmdDef entries (msgc commands)
    if (j.contains("channel_schedule")) {
        for (auto& cs : j["channel_schedule"]) {
            std::string from = cs["from"].get<std::string>();
            unsigned long start_ms    = cs.value("start_ms", 10000UL);
            unsigned long interval_ms = cs["interval_ms"].get<unsigned long>();
            std::string msg_template  = cs.value("message", std::string("channel msg {n}"));

            int count;
            if (cs.contains("count")) {
                count = cs["count"].get<int>();
            } else {
                unsigned long end_ms = (cfg.duration_ms > 10000) ? cfg.duration_ms - 10000 : cfg.duration_ms;
                if (end_ms > start_ms && interval_ms > 0) {
                    count = static_cast<int>((end_ms - start_ms) / interval_ms) + 1;
                } else {
                    count = 1;
                }
            }

            for (int i = 0; i < count; i++) {
                unsigned long at_ms = start_ms + static_cast<unsigned long>(i) * interval_ms;
                if (at_ms >= cfg.duration_ms) break;

                std::string text = msg_template;
                std::string seq = std::to_string(i + 1);
                size_t pos = 0;
                while ((pos = text.find("{n}", pos)) != std::string::npos) {
                    text.replace(pos, 3, seq);
                    pos += seq.length();
                }

                OrchestratorConfig::CmdDef def;
                def.at_ms   = at_ms;
                def.node    = from;
                def.command = "msgc " + text;
                cfg.commands.push_back(std::move(def));
            }
        }
    }

    if (j.contains("expect")) {
        for (auto& ex : j["expect"]) {
            OrchestratorConfig::Assertion a;
            a.type = ex["type"].get<std::string>();
            if (ex.contains("node"))       a.node       = ex["node"].get<std::string>();
            if (ex.contains("command"))    a.command    = ex["command"].get<std::string>();
            if (ex.contains("value"))      a.value      = ex["value"].get<std::string>();
            if (ex.contains("event_type")) a.event_type = ex["event_type"].get<std::string>();
            if (ex.contains("count"))      a.count      = ex["count"].get<int>();
            if (ex.contains("min"))        a.min        = ex["min"].get<int>();
            if (ex.contains("max"))        a.max        = ex["max"].get<int>();
            cfg.assertions.push_back(std::move(a));
        }
    }

    return cfg;
}

static void validateConfig(const OrchestratorConfig& cfg) {
    std::vector<std::string> errors;

    // Simulation parameters
    if (cfg.step_ms <= 0)
        errors.push_back("simulation.step_ms must be > 0 (got " + std::to_string(cfg.step_ms) + ")");
    if (cfg.duration_ms == 0)
        errors.push_back("simulation.duration_ms must be > 0");
    if (cfg.warmup_ms >= cfg.duration_ms)
        errors.push_back("simulation.warmup_ms (" + std::to_string(cfg.warmup_ms) +
                         ") must be < duration_ms (" + std::to_string(cfg.duration_ms) + ")");
    if (cfg.capture_locked_db < 0.0f)
        errors.push_back("simulation.radio.capture_locked_db must be >= 0 (got " +
                         std::to_string(cfg.capture_locked_db) + ")");
    if (cfg.capture_unlocked_db < 0.0f)
        errors.push_back("simulation.radio.capture_unlocked_db must be >= 0 (got " +
                         std::to_string(cfg.capture_unlocked_db) + ")");
    if (cfg.cad_miss_prob < 0.0f || cfg.cad_miss_prob > 1.0f)
        errors.push_back("simulation.radio.cad_miss_prob must be [0.0, 1.0] (got " +
                         std::to_string(cfg.cad_miss_prob) + ")");
    if (cfg.cad_reliable_snr < cfg.cad_marginal_snr)
        errors.push_back("simulation.radio.cad_reliable_snr (" +
                         std::to_string(cfg.cad_reliable_snr) +
                         ") must be >= cad_marginal_snr (" +
                         std::to_string(cfg.cad_marginal_snr) + ")");
    if (cfg.snr_coherence_ms < 0.0f)
        errors.push_back("simulation.radio.snr_coherence_ms must be >= 0 (got " +
                         std::to_string(cfg.snr_coherence_ms) + ")");

    // Build node name set for cross-validation
    std::set<std::string> node_names;
    for (const auto& nd : cfg.nodes)
        node_names.insert(nd.name);

    // Node radio parameters
    for (const auto& nd : cfg.nodes) {
        std::string pfx = "node \"" + nd.name + "\": ";
        if (nd.sf < 7 || nd.sf > 12)
            errors.push_back(pfx + "sf must be 7-12 (got " + std::to_string(nd.sf) + ")");
        if (nd.bw <= 0)
            errors.push_back(pfx + "bw must be > 0 (got " + std::to_string(nd.bw) + ")");
        if (nd.cr < 1 || nd.cr > 4)
            errors.push_back(pfx + "cr must be 1-4 (got " + std::to_string(nd.cr) + ")");

        // Adversarial parameters
        if (nd.adversarial.mode != AdversarialMode::None) {
            if (nd.adversarial.probability < 0.0f || nd.adversarial.probability > 1.0f)
                errors.push_back(pfx + "adversarial.probability must be [0.0, 1.0] (got " +
                                 std::to_string(nd.adversarial.probability) + ")");
            if (nd.adversarial.mode == AdversarialMode::Corrupt && nd.adversarial.corrupt_bits <= 0)
                errors.push_back(pfx + "adversarial.corrupt_bits must be > 0 when mode=corrupt");
        }

        if (nd.tx_fail_prob < 0.0f || nd.tx_fail_prob > 1.0f)
            errors.push_back(pfx + "tx_fail_prob must be [0.0, 1.0] (got " +
                             std::to_string(nd.tx_fail_prob) + ")");
    }

    // Link cross-validation
    for (const auto& lk : cfg.links) {
        if (node_names.find(lk.from) == node_names.end())
            errors.push_back("link from \"" + lk.from + "\" references unknown node");
        if (node_names.find(lk.to) == node_names.end())
            errors.push_back("link to \"" + lk.to + "\" references unknown node");
        if (lk.loss < 0.0f || lk.loss > 1.0f)
            errors.push_back("link " + lk.from + " -> " + lk.to +
                             ": loss must be [0.0, 1.0] (got " + std::to_string(lk.loss) + ")");
    }

    // Command cross-validation
    for (const auto& cd : cfg.commands) {
        if (node_names.find(cd.node) == node_names.end())
            errors.push_back("command at " + std::to_string(cd.at_ms) +
                             "ms references unknown node \"" + cd.node + "\"");
    }

    if (!errors.empty()) {
        std::string msg = "Config validation failed (" + std::to_string(errors.size()) + " error(s)):";
        for (const auto& e : errors)
            msg += "\n  - " + e;
        throw std::runtime_error(msg);
    }
}

OrchestratorConfig parseConfigFile(const std::string& path) {
    std::ifstream f(path);
    if (!f.is_open()) {
        throw std::runtime_error("Cannot open config file: " + path);
    }
    json j = json::parse(f);
    auto cfg = parseJson(j);
    validateConfig(cfg);
    return cfg;
}

OrchestratorConfig parseConfigString(const std::string& json_str) {
    json j = json::parse(json_str);
    auto cfg = parseJson(j);
    validateConfig(cfg);
    return cfg;
}
