# companion_radio: wraps companion_radio/MyMesh

add_executable(companion_radio
    main.cpp
    ${SHIM_SOURCES}
    ${MESHCORE_SOURCES}
    ${ED25519_SOURCES}
    # Companion-specific MeshCore sources
    ${CMAKE_SOURCE_DIR}/MeshCore/examples/companion_radio/MyMesh.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/examples/companion_radio/DataStore.cpp
)

target_include_directories(companion_radio PRIVATE
    ${COMMON_INCLUDES}
    ${CMAKE_SOURCE_DIR}/MeshCore/examples/companion_radio
)

target_compile_definitions(companion_radio PRIVATE ${COMMON_DEFS})
target_link_libraries(companion_radio PRIVATE OpenSSL::Crypto)
