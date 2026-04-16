// plugin_exports.cpp — extern "C" ABI boundary for firmware plugins.
// Each .so exports these functions; the host loads them via dlsym().

#include <memory>
#include "NodeContext.h"
#include "MeshWrapper.h"

// Factory functions defined in RepeaterNode.cpp / CompanionNode.cpp
std::unique_ptr<MeshWrapper> createRepeaterMesh(NodeContext& ctx);
std::unique_ptr<MeshWrapper> createCompanionMesh(NodeContext& ctx);

extern "C" {

MeshWrapper* fw_create_repeater_mesh(NodeContext& ctx) {
    return createRepeaterMesh(ctx).release();
}

MeshWrapper* fw_create_companion_mesh(NodeContext& ctx) {
    return createCompanionMesh(ctx).release();
}

const char* fw_version() {
    return FIRMWARE_VERSION;
}

} // extern "C"
