// Compiles the companion MyMesh.cpp with class renamed to CompanionMyMesh.
// This translation unit sees ONLY the companion's MyMesh/NodePrefs.

#define MyMesh CompanionMyMesh
#include "../MeshCore/examples/companion_radio/MyMesh.cpp"
#undef MyMesh
