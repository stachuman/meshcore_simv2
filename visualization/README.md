# Visualization

Interactive swim-lane visualizer for orchestrator NDJSON output. Zero dependencies beyond Python 3 standard library.

## Usage

```bash
# Run simulation, capture NDJSON output
build/orchestrator/orchestrator config.json > output.ndjson

# Launch visualizer (opens browser automatically)
python3 visualization/visualize.py output.ndjson

# Custom port, don't auto-open browser
python3 visualization/visualize.py output.ndjson --port 9000 --no-open
```

## What it shows

A canvas-based swim-lane diagram:
- **Y axis**: one lane per node (companions at top, repeaters below)
- **X axis**: simulation time
- **TX**: orange boxes proportional to airtime duration
- **RX**: green triangles at reception time
- **Collisions**: red X marks
- **Half-duplex drops**: orange squares
- **Weak/loss drops**: brown dots
- **Commands**: blue diamonds

## Controls

| Input | Action |
|---|---|
| Mouse wheel | Zoom time axis (centered on cursor) |
| Click + drag | Pan time axis |
| Arrow left/right | Scroll time by 20% |
| **F** | Fit entire simulation in view |
| **T** | Toggle trace mode |
| **Esc** | Clear trace / close sidebar |
| Click node label | Show node details in sidebar |
| Click event (trace mode) | Trace packet across all nodes |

## Trace mode

Press **T** then click any packet. The sidebar shows:
- All events for that packet fingerprint (TX, RX, collisions, drops)
- A **spread tree** showing how the packet propagated through the network
- Highlighted path on the canvas with dashed arrows between hops

To correlate a `msg` command to its radio packet, click the cmd_reply event and use "Find TX packet".

## Scaling

Designed for large simulations (100+ nodes, 1 hour+):
- Backend indexes events by time, node, and packet hash
- Frontend fetches only the visible time window from the server
- Zoomed out (>60s visible): shows per-node activity density heatmap
- Zoomed in: shows individual packet-level detail

## Files

- `visualize.py` -- Python HTTP server + REST API + event indexing
- `visualize.html` -- Self-contained HTML/JS/CSS frontend (served by the Python server)
