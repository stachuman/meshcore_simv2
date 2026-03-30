# simple_repeater: wraps simple_repeater/MyMesh

add_executable(simple_repeater
    main.cpp
    ${SHIM_SOURCES}
    ${MESHCORE_SOURCES}
    ${ED25519_SOURCES}
    # Repeater-specific MeshCore sources
    ${CMAKE_SOURCE_DIR}/MeshCore/examples/simple_repeater/MyMesh.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/src/helpers/CommonCLI.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/src/helpers/ClientACL.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/src/helpers/RegionMap.cpp
)

target_include_directories(simple_repeater PRIVATE
    ${COMMON_INCLUDES}
    ${CMAKE_SOURCE_DIR}/MeshCore/examples/simple_repeater
)

target_compile_definitions(simple_repeater PRIVATE ${COMMON_DEFS})
target_link_libraries(simple_repeater PRIVATE OpenSSL::Crypto)
