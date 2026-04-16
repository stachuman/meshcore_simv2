// FirmwarePlugin.cpp — dlopen/dlsym wrapper for firmware .so plugins.

#include "FirmwarePlugin.h"
#include "MeshWrapper.h"

#include <dlfcn.h>
#include <cstdio>

FirmwarePlugin::FirmwarePlugin(const std::string& name, const std::string& path)
    : _name(name)
{
    // RTLD_LOCAL prevents symbol interposition between plugins.
    // Each plugin keeps its own MeshCore symbols private.
    // Host symbols (shims, globals) are visible via -rdynamic on the host binary.
    _handle = dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!_handle) {
        throw std::runtime_error("Failed to load firmware plugin \"" + name
                                 + "\" from " + path + ": " + dlerror());
    }

    _create_repeater = reinterpret_cast<CreateMeshFn>(dlsym(_handle, "fw_create_repeater_mesh"));
    _create_companion = reinterpret_cast<CreateMeshFn>(dlsym(_handle, "fw_create_companion_mesh"));
    _version = reinterpret_cast<VersionFn>(dlsym(_handle, "fw_version"));

    if (!_create_repeater || !_create_companion) {
        const char* dl_err = dlerror();
        std::string err = dl_err ? dl_err : "symbol not found";
        dlclose(_handle);
        _handle = nullptr;
        throw std::runtime_error("Firmware plugin \"" + name + "\" missing required symbols: " + err);
    }
}

FirmwarePlugin::~FirmwarePlugin() {
    // Don't dlclose — static destructors in the .so may reference
    // host globals. Leaking the handle is safe (OS reclaims at exit).
}

FirmwarePlugin::FirmwarePlugin(FirmwarePlugin&& o) noexcept
    : _name(std::move(o._name)), _handle(o._handle),
      _create_repeater(o._create_repeater),
      _create_companion(o._create_companion),
      _version(o._version)
{
    o._handle = nullptr;
}

FirmwarePlugin& FirmwarePlugin::operator=(FirmwarePlugin&& o) noexcept {
    if (this != &o) {
        _name = std::move(o._name);
        _handle = o._handle;
        _create_repeater = o._create_repeater;
        _create_companion = o._create_companion;
        _version = o._version;
        o._handle = nullptr;
    }
    return *this;
}

std::unique_ptr<MeshWrapper> FirmwarePlugin::createRepeaterMesh(NodeContext& ctx) const {
    return std::unique_ptr<MeshWrapper>(_create_repeater(ctx));
}

std::unique_ptr<MeshWrapper> FirmwarePlugin::createCompanionMesh(NodeContext& ctx) const {
    return std::unique_ptr<MeshWrapper>(_create_companion(ctx));
}

const char* FirmwarePlugin::version() const {
    return _version ? _version() : "unknown";
}

// --- FirmwareRegistry ---

void FirmwareRegistry::load(const std::string& name, const std::string& path) {
    if (_plugins.count(name)) {
        throw std::runtime_error("Firmware plugin already loaded: " + name);
    }
    auto plugin = std::make_unique<FirmwarePlugin>(name, path);
    fprintf(stderr, "[firmware] Loaded plugin \"%s\" from %s (version: %s)\n",
            name.c_str(), path.c_str(), plugin->version());
    _order.push_back(name);
    _plugins[name] = std::move(plugin);
}

FirmwarePlugin* FirmwareRegistry::get(const std::string& name) const {
    auto it = _plugins.find(name);
    return it != _plugins.end() ? it->second.get() : nullptr;
}

FirmwarePlugin* FirmwareRegistry::getDefault() const {
    if (_order.empty()) return nullptr;
    return get(_order.front());
}
