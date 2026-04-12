# Lua Scripting

The orchestrator supports Lua scripting for programmable test scenarios, event callbacks, and config-scheduled function calls. Requires `liblua5.4-dev` and building with `-DENABLE_LUA=ON` (enabled by default).

## Quick Start

```bash
# Install dependency
sudo apt install liblua5.4-dev

# Build (ENABLE_LUA is ON by default)
cmake -S . -B build && cmake --build build

# Run a Lua-driven simulation
./build/orchestrator/orchestrator --lua script.lua config.json

# Interactive mode with Lua (--lua optional)
./build/orchestrator/orchestrator -i config.json
```

## Three Execution Modes

### 1. Script-driven mode

The Lua script is the main loop. It must call `sim:initialize()`, `sim:step()`, and `sim:finalize()` itself.

```bash
./build/orchestrator/orchestrator --lua script.lua config.json
```

```lua
-- script.lua
sim:initialize()
while not sim:finished() do
    sim:step(1000)
end
local ok = sim:finalize()
if not ok then os.exit(1) end
```

### 2. Interactive mode with Lua

The REPL runs as normal, but Lua is available via the `lua` command. If `--lua` is given, the script is loaded first (useful for defining helper functions and event callbacks).

```bash
./build/orchestrator/orchestrator -i config.json
./build/orchestrator/orchestrator -i --lua helpers.lua config.json
```

At the prompt:
```
lua return sim:time()
lua sim:for_each_repeater(function(n, s) log(n .. ": " .. s.neighbor_count) end)
lua local stats = sim:msg_stats("alice"); log("sent: " .. stats.sent_flood)
```

### 3. Batch mode (no Lua)

Existing behavior, unchanged. Build with `-DENABLE_LUA=OFF` to disable Lua entirely.

```bash
./build/orchestrator/orchestrator config.json
```

## CLI Flags

| Flag | Description |
|------|-------------|
| `--lua <script.lua>` | Load and run Lua script |
| `--lua-var key=value` | Set variable accessible as `vars.key` in Lua |

Numeric values are auto-detected:
```bash
--lua-var delay=500      # vars.delay = 500 (number)
--lua-var mode=test      # vars.mode = "test" (string)
```

## API Reference

All methods use colon syntax: `sim:method()`.

### Simulation Control

| Method | Returns | Description |
|--------|---------|-------------|
| `sim:initialize()` | - | Initialize simulation (must be called first in script mode) |
| `sim:step(delta_ms)` | table | Advance simulation by `delta_ms` milliseconds |
| `sim:run_to_next()` | table | Run until next scheduled command fires |
| `sim:run_all(chunk_ms?)` | - | Run to completion (default chunk: 1000ms) |
| `sim:time()` | number | Current simulation time in ms |
| `sim:finished()` | boolean | True if simulation has reached duration |
| `sim:step_ms()` | number | Configured step size in ms |
| `sim:finalize()` | boolean | Finalize and check assertions (true = all pass) |

`step()` and `run_to_next()` return a table:
```lua
{
    start_ms = 5000,
    end_ms = 6000,
    events = 3,       -- number of NDJSON events generated
    finished = false
}
```

### Node Queries

| Method | Returns | Description |
|--------|---------|-------------|
| `sim:nodes()` | array | All nodes: `{name, role, pubkey}` |
| `sim:repeaters()` | array | Repeater nodes only |
| `sim:companions()` | array | Companion nodes only |
| `sim:nodes_by_role(role)` | array | Nodes filtered by `"repeater"` or `"companion"` |
| `sim:node_status(name)` | table | Structured node status (role-specific) |
| `sim:neighbor_count(name)` | number | Repeater neighbor count (shortcut) |
| `sim:contact_count(name)` | number | Companion contact count (shortcut) |

**`node_status` for repeaters:**
```lua
{
    node = "relay1",
    role = "repeater",
    time_ms = 6000,
    neighbor_count = 2,
    neighbors = {
        { pubkey = "ABCD1234", name = "relay2", last_seen_s = 5, snr_db = 7.5 },
        ...
    }
}
```

**`node_status` for companions:**
```lua
{
    node = "alice",
    role = "companion",
    time_ms = 6000,
    contact_count = 3,
    contacts = {
        { name = "relay1", type = "repeater", pubkey = "ABCD1234", path_len = 0, last_seen_s = 10 },
        { name = "bob", type = "companion", pubkey = "EFGH5678", path_len = 1, last_seen_s = 15 },
        ...
    }
}
```

### Message Statistics

| Method | Returns | Description |
|--------|---------|-------------|
| `sim:msg_stats(name)` | table | Structured message statistics for a node |

Returns:
```lua
{
    node = "alice",
    sent_flood = 3,          -- direct messages sent via flood
    sent_direct = 2,         -- direct messages sent via path routing
    sent_group = 1,          -- channel/group messages sent
    total_sent = 6,
    acks_flood_pending = 1,
    acks_flood_received = 0,
    acks_direct_pending = 1,
    acks_direct_received = 1,
    acks_pending = 2,
    acks_received = 1,
    sent_flood_to = { bob = 2, charlie = 1 },    -- per-destination flood counts
    sent_direct_to = { bob = 2 },                 -- per-destination direct counts
    recv_direct = { bob = 1 },                    -- per-sender receive counts
    total_recv_direct = 1,
    recv_group = 0,
    recv_group_by_sender = {}                     -- channel messages by sender
}
```

### Command Injection

| Method | Returns | Description |
|--------|---------|-------------|
| `sim:cmd(node, command)` | table | Execute CLI command on a node |

```lua
local result = sim:cmd("relay1", "neighbors")
-- result = { node = "relay1", command = "neighbors", reply = "...", time_ms = 6000 }
```

### Events

| Method | Returns | Description |
|--------|---------|-------------|
| `sim:events(n?)` | array | Last N events as parsed tables (default 10) |
| `sim:summary()` | table | Simulation summary |
| `sim:on(type, fn)` | - | Register event callback |

Event types: `"tx"`, `"rx"`, `"collision"`, `"drop_halfduplex"`, `"drop_weak"`, `"drop_loss"`, `"cmd_reply"`, `"lua_callback"`, `"*"` (wildcard).

```lua
sim:on('tx', function(evt)
    log("TX from " .. evt.node .. " at " .. evt.time_ms .. "ms")
end)

sim:on('*', function(evt)
    -- fires for every event
end)
```

Callbacks fire automatically after `sim:step()` and `sim:run_to_next()`.

### Iteration Helpers

| Method | Description |
|--------|-------------|
| `sim:for_each_node(fn)` | Call `fn(name, status)` for every node |
| `sim:for_each_repeater(fn)` | Call `fn(name, status)` for every repeater |
| `sim:for_each_companion(fn)` | Call `fn(name, status)` for every companion |

```lua
sim:for_each_repeater(function(name, status)
    log(name .. " has " .. status.neighbor_count .. " neighbors")
end)
```

### Utilities

| Function | Description |
|----------|-------------|
| `log(msg)` | Print message to stderr as `[lua] msg` |
| `sim_assert(cond, msg)` | Assert with error message on failure |

### Variables

CLI variables are accessible via the `vars` table:

```bash
./build/orchestrator/orchestrator --lua script.lua --lua-var delay=500 --lua-var mode=test config.json
```

```lua
local d = vars.delay   -- 500 (number)
local m = vars.mode    -- "test" (string)
```

## Config-Scheduled Lua Calls

Add `{"at_ms": N, "lua": "function_name"}` entries to the `commands` array in your JSON config. The named function is called from the loaded Lua script at the specified time.

```json
{
    "commands": [
        { "at_ms": 5000, "lua": "on_checkpoint" },
        { "at_ms": 10000, "node": "alice", "command": "neighbors" },
        { "at_ms": 15000, "lua": "on_finish" }
    ]
}
```

```lua
-- script.lua (loaded via --lua)
function on_checkpoint()
    log("Checkpoint at " .. sim:time() .. "ms")
    sim:for_each_repeater(function(name, s)
        log(name .. ": " .. s.neighbor_count .. " neighbors")
    end)
end

function on_finish()
    local stats = sim:msg_stats("alice")
    sim_assert(stats.total_recv_direct > 0, "alice should have received messages")
end

sim:initialize()
sim:run_all()
sim:finalize()
```

## Examples

### Count delivery rate across a simulation

```lua
sim:initialize()
sim:run_all()

local total_sent = 0
local total_recv = 0
sim:for_each_companion(function(name, status)
    local stats = sim:msg_stats(name)
    total_sent = total_sent + stats.total_sent
    total_recv = total_recv + stats.total_recv_direct
    log(name .. ": sent=" .. stats.total_sent .. " recv=" .. stats.total_recv_direct)
end)

if total_sent > 0 then
    log(string.format("Delivery rate: %.1f%%", 100 * total_recv / total_sent))
end

sim:finalize()
```

### Track TX events and report per-node counts

```lua
local tx_by_node = {}

sim:on('tx', function(evt)
    local n = evt.node
    tx_by_node[n] = (tx_by_node[n] or 0) + 1
end)

sim:initialize()
sim:run_all()

for node, count in pairs(tx_by_node) do
    log(node .. ": " .. count .. " transmissions")
end

sim:finalize()
```

### Parameterized test with --lua-var

```bash
./build/orchestrator/orchestrator --lua test.lua --lua-var interval=5000 config.json
```

```lua
sim:initialize()

local interval = vars.interval or 10000
local checks = 0

while not sim:finished() do
    sim:step(interval)
    checks = checks + 1
    sim:for_each_repeater(function(name, s)
        log(string.format("[check %d] %s: %d neighbors", checks, name, s.neighbor_count))
    end)
end

sim:finalize()
```

### Interactive exploration

```
$ ./build/orchestrator/orchestrator -i test/t01_hot_start_neighbors.json
[   0.000s] > step 6000
> {"events":0,"finished":false,"stepped_to_ms":6000}
[   6.000s] > lua sim:for_each_companion(function(n,s) log(n..': '..s.contact_count..' contacts') end)
[lua] alice: 2 contacts
[lua] bob: 2 contacts
[   6.000s] > lua local s = sim:msg_stats("alice"); log("sent="..s.total_sent.." recv="..s.total_recv_direct)
[lua] sent=0 recv=0
[   6.000s] > cmd alice msg bob hello
> {"command":"msg bob hello","node":"alice","reply":"msg sent to bob (flood)","time_ms":6000}
[   6.000s] > step 5000
> {"events":4,"finished":true,"stepped_to_ms":10000}
[  10.000s] > lua local s = sim:msg_stats("bob"); log("bob recv="..s.total_recv_direct)
[lua] bob recv=1
```
