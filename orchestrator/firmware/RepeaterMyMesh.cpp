// Compiles the repeater MyMesh.cpp with class renamed to RepeaterMyMesh.
// This translation unit sees ONLY the repeater's MyMesh/NodePrefs.

#define MyMesh RepeaterMyMesh
#include "MyMesh.cpp"  // resolved via include path (MESHCORE_DIR/examples/simple_repeater)
#undef MyMesh
