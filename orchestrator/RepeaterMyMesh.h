#pragma once
// Wrapper header: renames MyMesh -> RepeaterMyMesh so it can coexist
// with the companion's MyMesh in the same orchestrator binary.
// Only include this in RepeaterNode.cpp and RepeaterMyMesh.cpp.

#define MyMesh RepeaterMyMesh
#include "../MeshCore/examples/simple_repeater/MyMesh.h"
#undef MyMesh
