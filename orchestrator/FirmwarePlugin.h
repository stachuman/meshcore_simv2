#pragma once

#include <string>
#include <vector>
#include <memory>
#include <unordered_map>
#include <stdexcept>

struct NodeContext;
struct MeshWrapper;

// Wraps a dlopen'd firmware .so — provides type-safe access to exported symbols.
class FirmwarePlugin {
public:
    FirmwarePlugin(const std::string& name, const std::string& path);
    ~FirmwarePlugin();

    // Non-copyable, movable
    FirmwarePlugin(const FirmwarePlugin&) = delete;
    FirmwarePlugin& operator=(const FirmwarePlugin&) = delete;
    FirmwarePlugin(FirmwarePlugin&& o) noexcept;
    FirmwarePlugin& operator=(FirmwarePlugin&& o) noexcept;

    std::unique_ptr<MeshWrapper> createRepeaterMesh(NodeContext& ctx) const;
    std::unique_ptr<MeshWrapper> createCompanionMesh(NodeContext& ctx) const;
    const char* version() const;

    const std::string& name() const { return _name; }

private:
    using CreateMeshFn = MeshWrapper* (*)(NodeContext&);
    using VersionFn = const char* (*)();

    std::string _name;
    void* _handle = nullptr;
    CreateMeshFn _create_repeater = nullptr;
    CreateMeshFn _create_companion = nullptr;
    VersionFn _version = nullptr;
};

// Registry of loaded firmware plugins. Owns all FirmwarePlugin instances.
class FirmwareRegistry {
public:
    // Load a plugin from disk. Throws on failure.
    void load(const std::string& name, const std::string& path);

    // Look up a loaded plugin by name. Returns nullptr if not found.
    FirmwarePlugin* get(const std::string& name) const;

    // Get default plugin (first one loaded, typically "fw_default").
    FirmwarePlugin* getDefault() const;

    bool empty() const { return _plugins.empty(); }

private:
    // Ordered: first inserted = default
    std::vector<std::string> _order;
    std::unordered_map<std::string, std::unique_ptr<FirmwarePlugin>> _plugins;
};
