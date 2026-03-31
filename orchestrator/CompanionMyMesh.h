#pragma once
// Wrapper header: renames MyMesh -> CompanionMyMesh so it can coexist
// with the repeater's MyMesh in the same orchestrator binary.
// Only include this in CompanionNode.cpp and CompanionMyMesh.cpp.

#define MyMesh CompanionMyMesh
#include "MyMesh.h"  // resolved via include path (MESHCORE_DIR/examples/companion_radio)
#undef MyMesh
