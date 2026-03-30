#!/usr/bin/env python3
"""MeshCore simulation visualizer.

Reads NDJSON event log, builds indexes, serves interactive swim-lane visualization.

Usage:
    python3 visualize.py output.ndjson [--port 8000] [--no-open]
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
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._index_event(ev)

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

    def get_meta(self) -> dict:
        return {
            "nodes": self.nodes,
            "time_min": self.time_min,
            "time_max": self.time_max,
            "event_count": len(self.events),
            "sim": self.sim_info,
            "stats": self.stats,
        }


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
        elif path.startswith("/api/trace/"):
            pkt = path.split("/")[-1]
            events = self.index.query_pkt(pkt)
            # Also find correlated msg command if this pkt was a message
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
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-open", action="store_true", help="Don't open browser")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: {args.input} not found", file=sys.stderr)
        sys.exit(1)

    index = EventIndex(args.input)

    VisualizerHandler.index = index
    server = HTTPServer(("127.0.0.1", args.port), VisualizerHandler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Serving at {url}", file=sys.stderr)

    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
        server.shutdown()


if __name__ == "__main__":
    main()
