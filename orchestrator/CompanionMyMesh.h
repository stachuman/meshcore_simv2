#pragma once
// Wrapper header: renames MyMesh -> CompanionMyMesh so it can coexist
// with the repeater's MyMesh in the same orchestrator binary.
// Only include this in CompanionNode.cpp and CompanionMyMesh.cpp.

#define MyMesh CompanionMyMesh
#include "../MeshCore/examples/companion_radio/MyMesh.h"
#undef MyMesh
