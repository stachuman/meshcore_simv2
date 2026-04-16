// Compiles the companion MyMesh.cpp with class renamed to CompanionMyMesh.
// This translation unit sees ONLY the companion's MyMesh/NodePrefs.

#define MyMesh CompanionMyMesh
#include "MyMesh.cpp"  // resolved via include path (MESHCORE_DIR/examples/companion_radio)
#undef MyMesh
