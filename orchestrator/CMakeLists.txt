# orchestrator: multi-node single-process MeshCore simulator
# Architecture: host binary (shims + orchestrator logic) + firmware .so plugins

# ---- Host orchestrator (no MeshCore sources — those live in plugins) ----
add_executable(orchestrator
    main.cpp
    Orchestrator.cpp
    SimController.cpp
    InteractiveRepl.cpp
    NodeContext.cpp
    LinkModel.cpp
    EventLog.cpp
    JsonConfig.cpp
    FirmwarePlugin.cpp
    ${SHIM_SOURCES}
)

# Our code gets regular -I so warnings fire; MeshCore/ed25519 get -isystem so
# their warnings are suppressed. sol.hpp (third_party) is vendored — SYSTEM too.
target_include_directories(orchestrator PRIVATE
    ${OUR_INCLUDES}
    ${CMAKE_CURRENT_SOURCE_DIR}
)
target_include_directories(orchestrator SYSTEM PRIVATE
    ${THIRDPARTY_INCLUDES}
    ${CMAKE_CURRENT_SOURCE_DIR}/third_party
)

target_compile_definitions(orchestrator PRIVATE
    ${COMMON_DEFS}
    ORCHESTRATOR_BUILD
)

# Warnings: apply to orchestrator target (covers our orchestrator sources + shims).
# Plugins compile MeshCore sources which we don't own — warnings are not applied there.
# -Werror is intentionally OFF for now; enable per-developer via -DORCH_WERROR=ON once
# the current warning backlog is cleared.
option(ORCH_WERROR "Treat warnings as errors in orchestrator/shim code" OFF)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(orchestrator PRIVATE
        -Wall
        -Wextra
        -Wpedantic
        -Wshadow
        -Wcast-align
        -Wunused
        -Woverloaded-virtual
        -Wconversion
        -Wsign-conversion
        -Wnull-dereference
        -Wformat=2
        # Noise suppression for headers we can't control / conscious choices:
        -Wno-unused-parameter       # Arduino-style callbacks with unused args
        -Wno-missing-field-initializers  # nlohmann::json aggregate init is noisy
        # -Wnon-virtual-dtor disabled: MeshCore's base classes (mesh::RNG,
        #   mesh::RTCClock, mesh::Radio, mesh::Board) do not have virtual
        #   destructors. Our shims inherit from them, and the warning fires
        #   on our class declaration even though we never delete shim
        #   objects through base pointers. Cannot be fixed without editing
        #   MeshCore; consciously dropped.
        # -Wdouble-promotion disabled: most hits are printf("%f", float)
        #   where the float-to-double promotion is exactly what the printf
        #   format specifier expects. Chasing casts at every emit site is
        #   busywork with no correctness benefit.
    )
    if(ORCH_WERROR)
        target_compile_options(orchestrator PRIVATE -Werror)
    endif()
endif()

# -rdynamic exports host symbols (shims) so plugins can resolve them at dlopen time.
# dl provides dlopen/dlsym/dlclose.
target_link_libraries(orchestrator PRIVATE
    OpenSSL::Crypto
    dl
)
target_link_options(orchestrator PRIVATE -rdynamic)

# ---- Optional Lua scripting support ----
if(ENABLE_LUA)
    find_package(Lua 5.4 REQUIRED)
    target_sources(orchestrator PRIVATE LuaEngine.cpp)
    target_include_directories(orchestrator SYSTEM PRIVATE ${LUA_INCLUDE_DIR})
    target_link_libraries(orchestrator PRIVATE ${LUA_LIBRARIES})
    target_compile_definitions(orchestrator PRIVATE ENABLE_LUA)
endif()

# ---- Firmware plugin builder function ----
# Each plugin is a shared library containing ALL MeshCore sources + node factories.
# Shim symbols are resolved from the host binary at dlopen time.
function(add_firmware_plugin PLUGIN_NAME MESHCORE_PATH)
    # Include paths: shims first, then this MeshCore version
    set(FW_INCLUDES
        ${CMAKE_SOURCE_DIR}/shims/arduino_shim
        ${CMAKE_SOURCE_DIR}/shims/lib_shim
        ${CMAKE_SOURCE_DIR}/shims/crypto_shim
        ${CMAKE_SOURCE_DIR}/shims/platform_shim
        ${MESHCORE_PATH}/src
        ${MESHCORE_PATH}/src/helpers
        ${MESHCORE_PATH}/lib/ed25519
        ${CMAKE_CURRENT_SOURCE_DIR}
        ${CMAKE_CURRENT_SOURCE_DIR}/firmware
    )

    if(DELAY_TUNING_RUNTIME)
        list(INSERT FW_INCLUDES 0 ${CMAKE_SOURCE_DIR}/shims/sweep_shim)
    endif()

    # Repeater OBJECT lib (TU isolation for MyMesh renaming)
    add_library(${PLUGIN_NAME}_rep OBJECT
        firmware/RepeaterMyMesh.cpp
        firmware/RepeaterNode.cpp
        ${MESHCORE_PATH}/src/helpers/CommonCLI.cpp
        ${MESHCORE_PATH}/src/helpers/ClientACL.cpp
        ${MESHCORE_PATH}/src/helpers/RegionMap.cpp
    )
    target_include_directories(${PLUGIN_NAME}_rep PRIVATE
        ${FW_INCLUDES} ${MESHCORE_PATH}/examples/simple_repeater)
    target_compile_definitions(${PLUGIN_NAME}_rep PRIVATE ${COMMON_DEFS} ORCHESTRATOR_BUILD)
    set_target_properties(${PLUGIN_NAME}_rep PROPERTIES POSITION_INDEPENDENT_CODE ON)

    # Detect scope support: check if NodePrefs has default_scope_name
    file(STRINGS "${MESHCORE_PATH}/examples/companion_radio/NodePrefs.h" _scope_check REGEX "default_scope_name")
    set(_COMP_EXTRA_DEFS "")
    if(_scope_check)
        set(_COMP_EXTRA_DEFS "HAS_SCOPE_SUPPORT")
        message(STATUS "  ${PLUGIN_NAME}: scope support detected")
    endif()

    # Companion OBJECT lib (TU isolation for MyMesh renaming)
    add_library(${PLUGIN_NAME}_comp OBJECT
        firmware/CompanionMyMesh.cpp
        firmware/CompanionNode.cpp
        ${MESHCORE_PATH}/examples/companion_radio/DataStore.cpp
    )
    target_include_directories(${PLUGIN_NAME}_comp PRIVATE
        ${FW_INCLUDES} ${MESHCORE_PATH}/examples/companion_radio)
    target_compile_definitions(${PLUGIN_NAME}_comp PRIVATE ${COMMON_DEFS} ORCHESTRATOR_BUILD ${_COMP_EXTRA_DEFS})
    set_target_properties(${PLUGIN_NAME}_comp PROPERTIES POSITION_INDEPENDENT_CODE ON)

    # Shared library — contains MeshCore + ed25519 + node factories + plugin exports
    add_library(${PLUGIN_NAME} SHARED
        firmware/plugin_exports.cpp
        ${MESHCORE_PATH}/src/Packet.cpp
        ${MESHCORE_PATH}/src/Dispatcher.cpp
        ${MESHCORE_PATH}/src/Mesh.cpp
        ${MESHCORE_PATH}/src/Utils.cpp
        ${MESHCORE_PATH}/src/Identity.cpp
        ${MESHCORE_PATH}/src/helpers/StaticPoolPacketManager.cpp
        ${MESHCORE_PATH}/src/helpers/BaseChatMesh.cpp
        ${MESHCORE_PATH}/src/helpers/TxtDataHelpers.cpp
        ${MESHCORE_PATH}/src/helpers/AdvertDataHelpers.cpp
        ${MESHCORE_PATH}/src/helpers/IdentityStore.cpp
        ${MESHCORE_PATH}/src/helpers/TransportKeyStore.cpp
        # ed25519
        ${MESHCORE_PATH}/lib/ed25519/add_scalar.c
        ${MESHCORE_PATH}/lib/ed25519/keypair.c
        ${MESHCORE_PATH}/lib/ed25519/sha512.c
        ${MESHCORE_PATH}/lib/ed25519/sc.c
        ${MESHCORE_PATH}/lib/ed25519/ge.c
        ${MESHCORE_PATH}/lib/ed25519/fe.c
        ${MESHCORE_PATH}/lib/ed25519/key_exchange.c
        ${MESHCORE_PATH}/lib/ed25519/verify.c
        ${MESHCORE_PATH}/lib/ed25519/sign.c
    )
    target_include_directories(${PLUGIN_NAME} PRIVATE
        ${FW_INCLUDES}
        ${CMAKE_CURRENT_SOURCE_DIR}/third_party
    )
    target_compile_definitions(${PLUGIN_NAME} PRIVATE ${COMMON_DEFS} ORCHESTRATOR_BUILD)
    target_link_libraries(${PLUGIN_NAME} PRIVATE ${PLUGIN_NAME}_rep ${PLUGIN_NAME}_comp)
    set_target_properties(${PLUGIN_NAME} PROPERTIES PREFIX "" SUFFIX ".so")
    # -Bsymbolic: bind internal symbol references directly, preventing
    # the host binary's symbols from interposing on plugin-internal calls.
    # Critical when multiple plugins define the same MeshCore symbols.
    target_link_options(${PLUGIN_NAME} PRIVATE -Wl,-Bsymbolic)

    # Place plugin .so next to orchestrator binary
    set_target_properties(${PLUGIN_NAME} PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}
    )

    endfunction()

# Build default firmware plugin from MESHCORE_DIR
add_firmware_plugin(fw_default ${MESHCORE_DIR})

# Host links against fw_default.so at build time — resolves MeshCore symbols
# referenced transitively through headers (SimpleMeshTables, target.cpp).
# Additional plugins are loaded purely via dlopen at runtime.
target_link_libraries(orchestrator PRIVATE fw_default)

# Additional firmware versions via CMake list
# Usage: cmake -DFIRMWARE_PLUGINS="scope=/path/to/MeshCore-scope;main=/path/to/MeshCore-main"
if(DEFINED FIRMWARE_PLUGINS)
    foreach(entry IN LISTS FIRMWARE_PLUGINS)
        string(REGEX MATCH "^([^=]+)=(.+)$" _m "${entry}")
        if(_m)
            add_firmware_plugin("fw_${CMAKE_MATCH_1}" "${CMAKE_MATCH_2}")
        endif()
    endforeach()
endif()
