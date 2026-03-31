# simple_repeater: wraps simple_repeater/MyMesh

add_executable(simple_repeater
    main.cpp
    ${SHIM_SOURCES}
    ${MESHCORE_SOURCES}
    ${ED25519_SOURCES}
    # Repeater-specific MeshCore sources
    ${MESHCORE_DIR}/examples/simple_repeater/MyMesh.cpp
    ${MESHCORE_DIR}/src/helpers/CommonCLI.cpp
    ${MESHCORE_DIR}/src/helpers/ClientACL.cpp
    ${MESHCORE_DIR}/src/helpers/RegionMap.cpp
)

target_include_directories(simple_repeater PRIVATE
    ${COMMON_INCLUDES}
    ${MESHCORE_DIR}/examples/simple_repeater
)

target_compile_definitions(simple_repeater PRIVATE ${COMMON_DEFS})
target_link_libraries(simple_repeater PRIVATE OpenSSL::Crypto)
