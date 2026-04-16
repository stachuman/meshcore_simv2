# Multi-Firmware Plugin System

The orchestrator supports running nodes with **different MeshCore firmware versions** simultaneously. Each firmware version is compiled into a shared library (`.so`) plugin. Nodes in the same simulation can use different plugins, enabling mixed-firmware testing.

## Quick Start

### Default setup (single firmware)

No configuration needed. The orchestrator auto-loads `fw_default.so` from its binary directory, built from `MESHCORE_DIR` (defaults to `./MeshCore`).

```bash
cmake -S . -B build && cmake --build build
build/orchestrator/orchestrator test/t01_hot_start_neighbors.json
```

### Adding a second firmware

1. Clone or checkout a different MeshCore version:

```bash
git clone https://github.com/user/MeshCore.git MeshCore-scope
cd MeshCore-scope && git checkout default-scope && cd ..
```

2. Build with the additional plugin:

```bash
cmake -S . -B build -DFIRMWARE_PLUGINS="scope=$(pwd)/MeshCore-scope"
cmake --build build
```

This produces `fw_default.so` (from `./MeshCore`) and `fw_scope.so` (from `./MeshCore-scope`).

3. Use it in a config:

```json
{
  "nodes": [
    { "name": "R1", "role": "repeater", "firmware": "fw_scope" },
    { "name": "R2", "role": "repeater" }
  ]
}
```

`R1` uses `fw_scope`, `R2` uses the default (`fw_default`).

## Architecture

```
orchestrator (host binary)
  |-- Shims (Arduino, FS, crypto, SimRadio, VirtualClock, etc.)
  |-- Orchestrator, SimController, NodeContext, LinkModel, EventLog
  |-- FirmwarePlugin loader (dlopen/dlsym)
  |-- Links with: -rdynamic (exports shim symbols)
  |
  +-- fw_default.so (firmware plugin)
  |     |-- ALL MeshCore sources (Mesh, Dispatcher, BaseChatMesh, etc.)
  |     |-- ALL ed25519 sources
  |     |-- RepeaterNode + CompanionNode factories
  |     +-- Exports: fw_create_repeater_mesh(), fw_create_companion_mesh()
  |
  +-- fw_scope.so (same structure, different MeshCore checkout)
        +-- ...
```

Each plugin is a self-contained shared library with its own copy of all MeshCore code. Symbol isolation is achieved via `-Wl,-Bsymbolic`, ensuring each plugin uses its own function implementations even when the host binary also contains MeshCore symbols.

**Note:** The host binary links against `fw_default.so` at build time (`target_link_libraries(orchestrator PRIVATE fw_default)`). This resolves MeshCore symbols referenced transitively through shared headers (e.g. `SimpleMeshTables`, `target.h`). Additional plugins are loaded purely via `dlopen` at runtime. The `-Wl,-Bsymbolic` flag on each plugin prevents the host's `fw_default` symbols from interposing on other plugins' internal calls.

### Plugin ABI

Each plugin exports three `extern "C"` functions:

```cpp
MeshWrapper* fw_create_repeater_mesh(NodeContext& ctx);
MeshWrapper* fw_create_companion_mesh(NodeContext& ctx);
const char* fw_version();
```

The `MeshWrapper` virtual interface is the ABI contract between host and plugin. Its header is shared (compiled by both sides). The plugin returns raw pointers; the host wraps them in `std::unique_ptr`.

### How globals work

Shim globals (`board`, `sensors`, `_ctx_radio`, `_ctx_serial`, `_ctx_fs`, `millis()`, `delay()`) are defined in the host binary. Plugins include shim **headers** (declarations + macros) but not shim **sources** (no definitions). At `dlopen` time, undefined symbols resolve from the host via `-rdynamic`.

`NodeContext::activate()` swaps global pointers before each node's `loop()` call. This works identically for plugin-loaded code since it accesses the same host globals.

## Configuration

### Simulation-level default

```json
{
  "simulation": {
    "firmware": {
      "default": "fw_scope"
    }
  }
}
```

All nodes without per-node firmware use this plugin. Defaults to `"fw_default"` if omitted.

### Per-node firmware

```json
{
  "nodes": [
    { "name": "R_old",  "role": "repeater", "firmware": "fw_default" },
    { "name": "R_new",  "role": "repeater", "firmware": "fw_scope" },
    { "name": "alice",  "role": "companion" }
  ]
}
```

`alice` uses the simulation default; `R_old` and `R_new` use explicit firmware.

### Explicit plugin paths (optional)

If plugins are not in the orchestrator's directory:

```json
{
  "simulation": {
    "firmware": {
      "plugins": {
        "fw_custom": "/path/to/fw_custom.so"
      }
    }
  }
}
```

## CMake Reference

### `MESHCORE_DIR`

Path to the MeshCore source tree used for `fw_default`. Defaults to `${CMAKE_SOURCE_DIR}/MeshCore`.

```bash
cmake -S . -B build -DMESHCORE_DIR=/path/to/MeshCore-fork
```

### `FIRMWARE_PLUGINS`

Semicolon-separated list of `name=path` pairs for additional firmware plugins.

```bash
# Single additional plugin
cmake -S . -B build -DFIRMWARE_PLUGINS="scope=$(pwd)/MeshCore-scope"

# Multiple plugins
cmake -S . -B build -DFIRMWARE_PLUGINS="scope=$(pwd)/MeshCore-scope;v113=$(pwd)/MeshCore-1.13"
```

Each entry produces `fw_<name>.so`. The `add_firmware_plugin()` CMake function handles the full build: OBJECT libs for repeater/companion TU isolation, PIC compilation, MeshCore + ed25519 sources, and proper include paths.

### Feature detection

The build system automatically detects firmware-specific features. Currently detected:

| Feature | Detection | Define |
|---------|-----------|--------|
| Default flood scope | `NodePrefs::default_scope_name` in NodePrefs.h | `HAS_SCOPE_SUPPORT` |

When a feature is detected, the corresponding define is added to the companion OBJECT lib. The firmware-specific code uses `#ifdef` guards.

## Adding New Firmware Versions

### Step 1: Get the MeshCore source tree

```bash
# From a git tag
git clone https://github.com/user/MeshCore.git MeshCore-v1.13
cd MeshCore-v1.13 && git checkout repeater-v1.13.0 && cd ..

# From a branch
git clone https://github.com/user/MeshCore.git MeshCore-feature
cd MeshCore-feature && git checkout feature-branch && cd ..
```

### Step 2: Build

```bash
cmake -S . -B build -DFIRMWARE_PLUGINS="v113=$(pwd)/MeshCore-v1.13"
cmake --build build
```

### Step 3: Handle API differences

If the new firmware has API differences (new/removed methods, changed struct fields), you may need to add compatibility code:

1. **For struct field differences** (e.g., `NodePrefs` gains new fields): Add CMake feature detection in `add_firmware_plugin()`:

```cmake
# In orchestrator/CMakeLists.txt, inside add_firmware_plugin():
file(STRINGS "${MESHCORE_PATH}/examples/companion_radio/NodePrefs.h"
     _check REGEX "my_new_field")
if(_check)
    list(APPEND _COMP_EXTRA_DEFS "HAS_MY_NEW_FIELD")
    message(STATUS "  ${PLUGIN_NAME}: my_new_field detected")
endif()
```

2. **For method differences** (e.g., new virtual methods): Use `#ifdef` in the node factory files (`CompanionNode.cpp` or `RepeaterNode.cpp`):

```cpp
#ifdef HAS_MY_NEW_FIELD
    prefs->my_new_field = some_value;
#endif
```

3. **For new commands**: Add handling inside `handleCommand()` in the appropriate node factory, guarded by `#ifdef`.

### Step 4: Add tests

Create a test config that uses the new firmware. Add `_requires_plugins` to skip when the plugin isn't built:

```json
{
  "_requires_plugins": ["fw_v113"],
  "nodes": [
    { "name": "R1", "role": "repeater", "firmware": "fw_v113" }
  ]
}
```

## Plugin Files

All plugin-specific source files live in `orchestrator/firmware/`:

| File | Purpose |
|------|---------|
| `plugin_exports.cpp` | `extern "C"` wrappers for factory functions |
| `RepeaterMyMesh.h/cpp` | `#define MyMesh RepeaterMyMesh` + include |
| `CompanionMyMesh.h/cpp` | `#define MyMesh CompanionMyMesh` + include |
| `RepeaterNode.cpp` | Repeater factory: `createRepeaterMesh()` |
| `CompanionNode.cpp` | Companion factory: `createCompanionMesh()`, feature-detection `#ifdef`s |

These files are compiled once per plugin, each time against a different MeshCore tree. The `#include "MyMesh.h"` resolves via include path to the correct MeshCore version.

## Test Runner

The test runner (`test/run_tests.sh`) automatically handles plugin requirements:

- Tests with `"_requires_plugins": ["fw_scope"]` are skipped if `fw_scope.so` is not present
- Plugin `.so` files are detected from the orchestrator binary's directory
- All `fw_*.so` files in the binary directory are auto-loaded at startup

## Troubleshooting

### "firmware plugin not loaded" error

The node references a firmware name that wasn't built. Either:
- Add it to `FIRMWARE_PLUGINS` in the cmake command
- Check that the `.so` file exists next to the orchestrator binary

### Symbol interposition issues

If different firmware versions behave identically despite code differences, check that `-Wl,-Bsymbolic` is set on plugin targets. Without it, the host's MeshCore symbols (from `fw_default`) can override plugin-internal calls.

### Build errors with API differences

If a new MeshCore version has API changes:
1. Check `CompanionNode.cpp` and `RepeaterNode.cpp` for firmware-specific code
2. Add `#ifdef` guards with CMake feature detection
3. The pattern: detect a struct field/method in CMake, add a `-D` define, use `#ifdef` in C++
