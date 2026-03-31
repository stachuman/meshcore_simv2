#pragma once
// Wrapper header: renames MyMesh -> RepeaterMyMesh so it can coexist
// with the companion's MyMesh in the same orchestrator binary.
// Only include this in RepeaterNode.cpp and RepeaterMyMesh.cpp.

#define MyMesh RepeaterMyMesh
#include "MyMesh.h"  // resolved via include path (MESHCORE_DIR/examples/simple_repeater)
#undef MyMesh
