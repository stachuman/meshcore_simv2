# companion_radio: wraps companion_radio/MyMesh

add_executable(companion_radio
    main.cpp
    ${SHIM_SOURCES}
    ${MESHCORE_SOURCES}
    ${ED25519_SOURCES}
    # Companion-specific MeshCore sources
    ${MESHCORE_DIR}/examples/companion_radio/MyMesh.cpp
    ${MESHCORE_DIR}/examples/companion_radio/DataStore.cpp
)

target_include_directories(companion_radio PRIVATE
    ${COMMON_INCLUDES}
    ${MESHCORE_DIR}/examples/companion_radio
)

target_compile_definitions(companion_radio PRIVATE ${COMMON_DEFS})
target_link_libraries(companion_radio PRIVATE OpenSSL::Crypto)
