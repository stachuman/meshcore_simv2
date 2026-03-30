// Compiles the repeater MyMesh.cpp with class renamed to RepeaterMyMesh.
// This translation unit sees ONLY the repeater's MyMesh/NodePrefs.

#define MyMesh RepeaterMyMesh
#include "../MeshCore/examples/simple_repeater/MyMesh.cpp"
#undef MyMesh
