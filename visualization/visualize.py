#!/usr/bin/env python3
"""MeshCore simulation visualizer.

Reads NDJSON event log, builds indexes, serves interactive swim-lane visualization.
Optionally serves a topology map view on port+1 when --config is given.

Usage:
    python3 visualize.py output.ndjson [--config config.json] [--port 8000] [--no-open]
"""

import argparse
import json
import os
import sys
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from bisect import bisect_left, bisect_right


class EventIndex:
    """In-memory index of simulation events for fast viewport queries."""

    def __init__(self, path: str):
        self.events: list[dict] = []
        self.times: list[int] = []          # parallel to events, for bisect
        self.by_node: dict[str, list[int]] = {}  # node -> event indices
        self.by_pkt: dict[str, list[int]] = {}   # pkt hash -> event indices
        self.nodes: list[dict] = []          # node metadata from node_ready
        self.node_set: dict[str, dict] = {}  # name -> metadata
        self.time_min: int = 0
        self.time_max: int = 0
        self.sim_info: dict = {}
        self.cmd_replies: list[int] = []     # indices of cmd_reply events
        self.stats: list[dict] = []          # node_stats events

        self._load(path)

    def _load(self, path: str):
        print(f"Loading {path}...", file=sys.stderr)
        parse_errors = 0
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    parse_errors += 1
                    continue
                self._index_event(ev)

        if parse_errors:
            print(f"WARNING: skipped {parse_errors} malformed JSON lines", file=sys.stderr)

        if self.events:
            self.time_min = self.times[0]
            self.time_max = self.times[-1]

        # Infer roles: nodes with node_stats are companions
        companion_names = {s["node"] for s in self.stats}
        for n in self.nodes:
            n["role"] = "companion" if n["name"] in companion_names else "repeater"

        print(f"Loaded {len(self.events)} events, {len(self.node_set)} nodes, "
              f"time range {self.time_min}-{self.time_max}ms "
              f"({(self.time_max - self.time_min) / 1000:.1f}s)",
              file=sys.stderr)

    def _index_event(self, ev: dict):
        etype = ev.get("type", "")
        time_ms = ev.get("time_ms", 0)

        if etype == "sim_start":
            self.sim_info = ev
            return
        if etype == "sim_end":
            self.sim_info["end_ms"] = time_ms
            return
        if etype == "node_ready":
            meta = {"name": ev["node"], "pub": ev.get("pub", "")}
            if "lat" in ev:
                meta["lat"] = ev["lat"]
                meta["lon"] = ev["lon"]
            self.node_set[ev["node"]] = meta
            self.nodes.append(meta)
            # Don't store as regular event — metadata only
            return
        if etype == "node_stats":
            self.stats.append(ev)
            return

        idx = len(self.events)
        self.events.append(ev)
        self.times.append(time_ms)

        # Index by node(s) involved
        for key in ("node", "from", "to"):
            if key in ev:
                name = ev[key]
                if name not in self.by_node:
                    self.by_node[name] = []
                self.by_node[name].append(idx)

        # Index by packet fingerprint
        if "pkt" in ev:
            pkt = ev["pkt"]
            if pkt not in self.by_pkt:
                self.by_pkt[pkt] = []
            self.by_pkt[pkt].append(idx)

        if etype == "cmd_reply":
            self.cmd_replies.append(idx)

    def query_time_range(self, from_ms: int, to_ms: int, max_events: int = 20000) -> list[dict]:
        """Return events in [from_ms, to_ms]."""
        lo = bisect_left(self.times, from_ms)
        hi = bisect_right(self.times, to_ms)
        if hi - lo > max_events:
            # Subsample to avoid overwhelming the browser
            step = (hi - lo) // max_events
            return [self.events[i] for i in range(lo, hi, step)]
        return self.events[lo:hi]

    def query_pkt(self, pkt: str) -> list[dict]:
        """Return all events for a packet fingerprint."""
        indices = self.by_pkt.get(pkt, [])
        return [self.events[i] for i in indices]

    def query_node_range(self, node: str, from_ms: int, to_ms: int) -> list[dict]:
        """Return events involving a node in a time range."""
        indices = self.by_node.get(node, [])
        result = []
        for i in indices:
            t = self.times[i]
            if t < from_ms:
                continue
            if t > to_ms:
                break
            result.append(self.events[i])
        return result

    def density(self, from_ms: int, to_ms: int, bucket_ms: int = 1000) -> dict:
        """Return per-node event density in time buckets (for zoomed-out view)."""
        lo = bisect_left(self.times, from_ms)
        hi = bisect_right(self.times, to_ms)

        n_buckets = max(1, (to_ms - from_ms + bucket_ms - 1) // bucket_ms)
        # node -> list of bucket counts by event category
        result: dict[str, dict[str, list[int]]] = {}

        for i in range(lo, hi):
            ev = self.events[i]
            t = self.times[i]
            bucket = min((t - from_ms) // bucket_ms, n_buckets - 1)
            etype = ev["type"]

            # Categorize
            if etype == "tx":
                cat = "tx"
                node = ev["node"]
            elif etype == "rx":
                cat = "rx"
                node = ev["to"]
            elif etype.startswith("collision"):
                cat = "collision"
                node = ev["to"]
            elif etype.startswith("drop"):
                cat = "drop"
                node = ev.get("to", ev.get("node", ""))
            elif etype == "cmd_reply":
                cat = "cmd"
                node = ev["node"]
            else:
                continue

            if node not in result:
                result[node] = {}
            if cat not in result[node]:
                result[node][cat] = [0] * n_buckets
            result[node][cat][bucket] += 1

        return {
            "from_ms": from_ms,
            "to_ms": to_ms,
            "bucket_ms": bucket_ms,
            "n_buckets": n_buckets,
            "nodes": result,
        }

    def find_msg_tx(self, node: str, after_ms: int) -> dict | None:
        """Find the first TX from a node after a given time (for cmd_reply→TX correlation)."""
        indices = self.by_node.get(node, [])
        for i in indices:
            ev = self.events[i]
            if self.times[i] >= after_ms and ev["type"] == "tx":
                return ev
        return None

    def _find_relay_tx(self, node: str, after_ms: int, pkt_type: str | None,
                       exclude_pkts: set) -> dict | None:
        """Find a relay TX from node within 3s after an RX, matching pkt_type.

        Only repeaters relay packets. Companions originate new messages but
        never forward — skip them to avoid confusing a response with a relay.
        """
        meta = self.node_set.get(node, {})
        if meta.get("role") == "companion":
            return None
        indices = self.by_node.get(node, [])
        for i in indices:
            t = self.times[i]
            if t < after_ms:
                continue
            if t > after_ms + 3000:
                break
            ev = self.events[i]
            if (ev["type"] == "tx"
                    and ev.get("pkt") not in exclude_pkts
                    and (pkt_type is None or ev.get("pkt_type") == pkt_type)):
                return ev
        return None

    def _find_response_txs(self, node: str, after_ms: int,
                           exclude_pkts: set) -> list[dict]:
        """Find response TXs from a companion node after receiving a packet.

        Returns all TXs within 3s (ack, path, msg responses) regardless of
        pkt_type — companions can respond with any packet type.
        """
        results = []
        indices = self.by_node.get(node, [])
        for i in indices:
            t = self.times[i]
            if t < after_ms:
                continue
            if t > after_ms + 3000:
                break
            ev = self.events[i]
            if ev["type"] == "tx" and ev.get("pkt") not in exclude_pkts:
                results.append(ev)
        return results

    def deep_trace(self, pkt: str, max_hops: int = 30) -> dict:
        """Follow relay chain across packet hash boundaries.

        Returns hops: [{pkt, events, relay_from?, response_from?}] where each
        hop is one packet hash in the relay chain.

        relay_from: this hop is a relay (repeater forwarded the parent packet)
        response_from: this hop is a response (companion reacted to the parent)
        """
        visited_pkts: set[str] = set()
        hops: list[dict] = []
        # Queue: (pkt_hash, parent_pkt, is_response)
        queue: list[tuple[str, str | None, bool]] = [(pkt, None, False)]

        while queue and len(hops) < max_hops:
            current_pkt, parent_pkt, is_response = queue.pop(0)
            if current_pkt in visited_pkts:
                continue
            visited_pkts.add(current_pkt)

            events = self.query_pkt(current_pkt)
            if not events:
                continue

            hop: dict = {"pkt": current_pkt, "events": events}
            if parent_pkt:
                if is_response:
                    hop["response_from"] = parent_pkt
                else:
                    hop["relay_from"] = parent_pkt
            hops.append(hop)

            # For each RX, look for relay or response TXs
            pkt_type = events[0].get("pkt_type") if events else None
            for ev in events:
                if ev["type"] != "rx":
                    continue
                receiver = ev["to"]
                rx_time = ev["time_ms"]

                meta = self.node_set.get(receiver, {})
                if meta.get("role") == "companion":
                    # Companion: find response TXs (any type)
                    responses = self._find_response_txs(
                        receiver, rx_time, visited_pkts)
                    for resp_tx in responses:
                        if resp_tx["pkt"] not in visited_pkts:
                            queue.append((resp_tx["pkt"], current_pkt, True))
                else:
                    # Repeater: find relay TX (same pkt_type)
                    relay_tx = self._find_relay_tx(
                        receiver, rx_time, pkt_type, visited_pkts)
                    if relay_tx and relay_tx["pkt"] not in visited_pkts:
                        queue.append((relay_tx["pkt"], current_pkt, False))

        return {"root_pkt": pkt, "hops": hops}

    def get_meta(self) -> dict:
        return {
            "nodes": self.nodes,
            "time_min": self.time_min,
            "time_max": self.time_max,
            "event_count": len(self.events),
            "sim": self.sim_info,
            "stats": self.stats,
        }


def load_topology(path: str) -> dict:
    """Load topology from config JSON for map view."""
    print(f"Loading topology from {path}...", file=sys.stderr)
    with open(path, "r") as f:
        config = json.load(f)

    nodes = []
    has_geo = False
    for n in config.get("nodes", []):
        node = {"name": n["name"], "role": n.get("role", "repeater")}
        if "lat" in n and "lon" in n:
            node["lat"] = n["lat"]
            node["lon"] = n["lon"]
            has_geo = True
        else:
            node["lat"] = None
            node["lon"] = None
        nodes.append(node)

    links = []
    topo = config.get("topology", {})
    for link in topo.get("links", []):
        entry = {
            "from": link["from"],
            "to": link["to"],
            "snr": link.get("snr", 0),
            "rssi": link.get("rssi", 0),
            "snr_std_dev": link.get("snr_std_dev", 0),
            "loss": link.get("loss", 0),
            "bidir": link.get("bidir", False),
        }
        links.append(entry)
        if entry["bidir"]:
            rev = dict(entry)
            rev["from"], rev["to"] = rev["to"], rev["from"]
            links.append(rev)

    print(f"Topology: {len(nodes)} nodes, {len(links)} links, geo={has_geo}",
          file=sys.stderr)
    return {"nodes": nodes, "links": links, "has_geo": has_geo}


def compute_map_stats(index: EventIndex) -> dict:
    """Compute per-node and per-link simulation statistics for the map view."""
    nodes: dict[str, dict[str, int]] = {}
    links: dict[str, dict[str, int]] = {}
    totals = {"tx": 0, "rx": 0, "collision": 0, "drop": 0}

    for ev in index.events:
        etype = ev.get("type", "")
        if etype == "tx":
            node = ev.get("node", "")
            if node:
                nodes.setdefault(node, {"tx": 0, "rx": 0, "collision": 0, "drop": 0})
                nodes[node]["tx"] += 1
                totals["tx"] += 1
        elif etype == "rx":
            to_node = ev.get("to", "")
            from_node = ev.get("node", ev.get("from", ""))
            if to_node:
                nodes.setdefault(to_node, {"tx": 0, "rx": 0, "collision": 0, "drop": 0})
                nodes[to_node]["rx"] += 1
                totals["rx"] += 1
            if from_node and to_node:
                lk = f"{from_node}>{to_node}"
                links.setdefault(lk, {"rx": 0, "collision": 0, "drop": 0})
                links[lk]["rx"] += 1
        elif etype.startswith("collision"):
            to_node = ev.get("to", "")
            from_node = ev.get("node", ev.get("from", ""))
            if to_node:
                nodes.setdefault(to_node, {"tx": 0, "rx": 0, "collision": 0, "drop": 0})
                nodes[to_node]["collision"] += 1
                totals["collision"] += 1
            if from_node and to_node:
                lk = f"{from_node}>{to_node}"
                links.setdefault(lk, {"rx": 0, "collision": 0, "drop": 0})
                links[lk]["collision"] += 1
        elif etype.startswith("drop"):
            to_node = ev.get("to", ev.get("node", ""))
            from_node = ev.get("node", ev.get("from", ""))
            if to_node:
                nodes.setdefault(to_node, {"tx": 0, "rx": 0, "collision": 0, "drop": 0})
                nodes[to_node]["drop"] += 1
                totals["drop"] += 1
            if from_node and to_node and from_node != to_node:
                lk = f"{from_node}>{to_node}"
                links.setdefault(lk, {"rx": 0, "collision": 0, "drop": 0})
                links[lk]["drop"] += 1

    return {
        "nodes": nodes,
        "links": links,
        "totals": totals,
        "msg_stats": index.stats,
        "time_min": index.time_min,
        "time_max": index.time_max,
    }


class MapHandler(BaseHTTPRequestHandler):
    topology: dict    # set by server factory
    index: EventIndex | None = None
    stats: dict | None = None

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._serve_html()
        elif path == "/api/topology":
            self._json_response(self.topology)
        elif path == "/api/stats":
            if self.stats:
                self._json_response(self.stats)
            else:
                self._json_response({})
        elif path.startswith("/api/trace/"):
            pkt = path.split("/")[-1]
            if self.index:
                self._json_response(self.index.deep_trace(pkt))
            else:
                self._json_response({"root_pkt": pkt, "hops": []})
        elif path == "/api/messages":
            self._json_response(self._get_messages())
        elif path.startswith("/api/node_events/"):
            node = path.split("/")[-1]
            params = parse_qs(parsed.query)
            from_ms = int(params.get("from", [self.index.time_min if self.index else 0])[0])
            to_ms = int(params.get("to", [self.index.time_max if self.index else 0])[0])
            limit = int(params.get("limit", [5000])[0])
            if self.index:
                events = self.index.query_node_range(node, from_ms, to_ms)
                if len(events) > limit:
                    events = events[:limit]
                self._json_response({"node": node, "events": events, "count": len(events)})
            else:
                self._json_response({"node": node, "events": [], "count": 0})
        else:
            self.send_error(404)

    def _get_messages(self) -> list:
        if not self.index:
            return []
        messages = []
        for idx in self.index.cmd_replies:
            ev = self.index.events[idx]
            reply = ev.get("reply", "")
            if "msg sent" in reply.lower() or "channel msg sent" in reply.lower():
                cmd = ev.get("command", ev.get("cmd", ""))
                node = ev.get("node", "")
                time_ms = ev.get("time_ms", 0)
                tx = self.index.find_msg_tx(node, time_ms)
                pkt = tx.get("pkt") if tx else None

                # Parse command for dest/text: "msga <dest> <text>" or "msgc <chan> <text>"
                parts = cmd.split(None, 2)  # split max 3 parts
                cmd_verb = parts[0] if parts else ""
                dest = parts[1] if len(parts) > 1 else ""
                text = parts[2] if len(parts) > 2 else ""

                is_channel = "channel" in reply.lower() or cmd_verb == "msgc"
                msg_type = "channel" if is_channel else "dm"

                # Enrich from TX event
                route = tx.get("route", "") if tx else ""
                airtime_ms = tx.get("airtime_ms", 0) if tx else 0
                pkt_type = tx.get("pkt_type", "") if tx else ""

                # Parse reply for ack info
                ack_tracked = "ack tracked" in reply.lower()

                messages.append({
                    "time_ms": time_ms,
                    "node": node,
                    "command": cmd,
                    "pkt": pkt,
                    "dest": dest,
                    "text": text,
                    "msg_type": msg_type,
                    "pkt_type": pkt_type,
                    "route": route,
                    "airtime_ms": airtime_ms,
                    "ack_tracked": ack_tracked,
                    "reply": reply,
                })
        return messages

    def _json_response(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        html_path = Path(__file__).parent / "map_view.html"
        if not html_path.exists():
            self.send_error(500, "map_view.html not found")
            return
        body = html_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


class VisualizerHandler(BaseHTTPRequestHandler):
    index: EventIndex  # set by server factory

    def log_message(self, format, *args):
        pass  # silence request logs

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._serve_html()
        elif path == "/api/meta":
            self._json_response(self.index.get_meta())
        elif path == "/api/events":
            from_ms = int(params.get("from", [self.index.time_min])[0])
            to_ms = int(params.get("to", [self.index.time_max])[0])
            events = self.index.query_time_range(from_ms, to_ms)
            self._json_response({"events": events, "count": len(events)})
        elif path == "/api/density":
            from_ms = int(params.get("from", [self.index.time_min])[0])
            to_ms = int(params.get("to", [self.index.time_max])[0])
            bucket_ms = int(params.get("bucket", [1000])[0])
            self._json_response(self.index.density(from_ms, to_ms, bucket_ms))
        elif path.startswith("/api/deep_trace/"):
            pkt = path.split("/")[-1]
            self._json_response(self.index.deep_trace(pkt))
        elif path.startswith("/api/trace/"):
            pkt = path.split("/")[-1]
            events = self.index.query_pkt(pkt)
            self._json_response({"pkt": pkt, "events": events, "count": len(events)})
        elif path.startswith("/api/msg_tx/"):
            # /api/msg_tx/nodename?after=12345
            node = path.split("/")[-1]
            after_ms = int(params.get("after", [0])[0])
            tx = self.index.find_msg_tx(node, after_ms)
            self._json_response({"tx": tx})
        else:
            self.send_error(404)

    def _json_response(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        html_path = Path(__file__).parent / "visualize.html"
        if not html_path.exists():
            self.send_error(500, "visualize.html not found")
            return
        body = html_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description="MeshCore simulation visualizer")
    parser.add_argument("input", help="NDJSON event log file")
    parser.add_argument("--config", help="Input config JSON for topology map view")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--bind", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--no-open", action="store_true", help="Don't open browser")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: {args.input} not found", file=sys.stderr)
        sys.exit(1)

    index = EventIndex(args.input)

    VisualizerHandler.index = index
    server = HTTPServer((args.bind, args.port), VisualizerHandler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Swim-lane: {url} (bind={args.bind})", file=sys.stderr)

    map_server = None
    if args.config:
        if not os.path.exists(args.config):
            print(f"Error: {args.config} not found", file=sys.stderr)
            sys.exit(1)
        topo = load_topology(args.config)
        MapHandler.topology = topo
        MapHandler.index = index
        MapHandler.stats = compute_map_stats(index)
        map_port = args.port + 1
        map_server = HTTPServer((args.bind, map_port), MapHandler)
        map_url = f"http://127.0.0.1:{map_port}"
        print(f"Map view:  {map_url}", file=sys.stderr)
        # Run map server in daemon thread
        map_thread = threading.Thread(target=map_server.serve_forever, daemon=True)
        map_thread.start()
        if not args.no_open:
            threading.Timer(1.0, lambda: webbrowser.open(map_url)).start()

    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
        if map_server:
            map_server.shutdown()
        server.shutdown()


if __name__ == "__main__":
    main()
