cmake_minimum_required(VERSION 3.16)
project(meshcore_real_sim LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_C_STANDARD 11)

# Custom build types for runtime performance optimization
if(CMAKE_BUILD_TYPE STREQUAL "NativeRelease")
    set(CMAKE_CXX_FLAGS_NATIVERELEASE "-O3 -march=native -mtune=native -DNDEBUG -flto=auto -ffast-math" CACHE STRING "" FORCE)
    set(CMAKE_C_FLAGS_NATIVERELEASE "-O3 -march=native -mtune=native -DNDEBUG -flto=auto -ffast-math" CACHE STRING "" FORCE)
    set(CMAKE_EXE_LINKER_FLAGS_NATIVERELEASE "-flto=auto" CACHE STRING "" FORCE)
    mark_as_advanced(CMAKE_CXX_FLAGS_NATIVERELEASE CMAKE_C_FLAGS_NATIVERELEASE CMAKE_EXE_LINKER_FLAGS_NATIVERELEASE)
    message(STATUS "Build type: NativeRelease (CPU-specific optimizations for maximum performance)")
endif()

if(CMAKE_BUILD_TYPE STREQUAL "Release")
    set(CMAKE_CXX_FLAGS_RELEASE "-O3 -DNDEBUG -flto=auto" CACHE STRING "" FORCE)
    set(CMAKE_C_FLAGS_RELEASE "-O3 -DNDEBUG -flto=auto" CACHE STRING "" FORCE)
    set(CMAKE_EXE_LINKER_FLAGS_RELEASE "-flto=auto" CACHE STRING "" FORCE)
    mark_as_advanced(CMAKE_CXX_FLAGS_RELEASE CMAKE_C_FLAGS_RELEASE CMAKE_EXE_LINKER_FLAGS_RELEASE)
    message(STATUS "Build type: Release (portable optimizations for Docker/distribution)")
endif()

# ASan build type: AddressSanitizer + UndefinedBehaviorSanitizer.
# Intended for local dev/CI to catch memory and UB bugs in shims, orchestrator, and plugins.
# Must be used for ALL targets (orchestrator + plugins) since plugins are dlopen'd into the host.
# Usage: cmake -S . -B build-asan -DCMAKE_BUILD_TYPE=ASan && cmake --build build-asan
if(CMAKE_BUILD_TYPE STREQUAL "ASan")
    set(_SAN_FLAGS "-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -fno-optimize-sibling-calls")
    set(CMAKE_CXX_FLAGS_ASAN "${_SAN_FLAGS}" CACHE STRING "" FORCE)
    set(CMAKE_C_FLAGS_ASAN "${_SAN_FLAGS}" CACHE STRING "" FORCE)
    set(CMAKE_EXE_LINKER_FLAGS_ASAN "-fsanitize=address,undefined" CACHE STRING "" FORCE)
    set(CMAKE_SHARED_LINKER_FLAGS_ASAN "-fsanitize=address,undefined" CACHE STRING "" FORCE)
    set(CMAKE_MODULE_LINKER_FLAGS_ASAN "-fsanitize=address,undefined" CACHE STRING "" FORCE)
    mark_as_advanced(
        CMAKE_CXX_FLAGS_ASAN CMAKE_C_FLAGS_ASAN
        CMAKE_EXE_LINKER_FLAGS_ASAN CMAKE_SHARED_LINKER_FLAGS_ASAN CMAKE_MODULE_LINKER_FLAGS_ASAN
    )
    message(STATUS "Build type: ASan (AddressSanitizer + UBSan — for dev/CI bug hunting)")
endif()

# MeshCore source directory — override with -DMESHCORE_DIR=/path/to/fork
set(MESHCORE_DIR "${CMAKE_SOURCE_DIR}/MeshCore" CACHE PATH "Path to MeshCore source tree")
message(STATUS "Using MeshCore from: ${MESHCORE_DIR}")
if(NOT EXISTS "${MESHCORE_DIR}/src/Mesh.h")
    message(FATAL_ERROR "MESHCORE_DIR does not point to a valid MeshCore tree: ${MESHCORE_DIR}\n"
                        "  Expected to find ${MESHCORE_DIR}/src/Mesh.h")
endif()

# Runtime delay tuning — when ON, the sweep shim's mutable DelayTuning.h
# shadows the fork's static const header, allowing optimize_tuning.py to
# vary delay parameters via JSON config without rebuilding.
option(DELAY_TUNING_RUNTIME "Enable runtime delay tuning override for sweep builds" OFF)

# Lua scripting — when ON, the orchestrator gains --lua and --lua-var flags
# and an interactive 'lua' command. Requires liblua5.4-dev (apt install liblua5.4-dev).
option(ENABLE_LUA "Enable Lua scripting in orchestrator (requires liblua5.4-dev)" ON)

# Shim context tracing — when ON, NodeContext::activate() logs every node
# swap to stderr. Useful for diagnosing stray callbacks that run against
# the wrong node's context. Off by default (non-zero overhead).
option(SHIM_TRACE_CONTEXT "Trace node context activations to stderr" OFF)
if(SHIM_TRACE_CONTEXT)
    add_compile_definitions(SHIM_TRACE_CONTEXT=1)
    message(STATUS "Shim context tracing: ENABLED")
endif()

# Find OpenSSL
find_package(OpenSSL REQUIRED)

# ---- Common compile definitions ----
set(COMMON_DEFS
    RP2040_PLATFORM
    ED25519_NO_SEED=1
    MAX_GROUP_CHANNELS=4
    MAX_CONTACTS=100
    MAX_NEIGHBOURS=50
    MAX_CLIENTS=20
    MAX_LORA_TX_POWER=20
    FIRMWARE_VERSION="sim-v1.0"
    FIRMWARE_BUILD_DATE="2024-01-01"
    ENABLE_ADVERT_ON_BOOT=1
    BLE_NAME_PREFIX="MeshCore-"
    OFFLINE_QUEUE_SIZE=16
)

# ---- Common include directories ----
# Split into OUR_INCLUDES (our shim code — warnings active) and
# THIRDPARTY_INCLUDES (MeshCore + ed25519 — treated as SYSTEM so their
# warnings don't drown out our own). Shim directories MUST come before
# MESHCORE_DIR so that our Arduino.h, FS.h, target.h etc. shadow the real
# platform headers that MeshCore expects.
set(OUR_INCLUDES
    ${CMAKE_SOURCE_DIR}/shims/arduino_shim
    ${CMAKE_SOURCE_DIR}/shims/lib_shim
    ${CMAKE_SOURCE_DIR}/shims/crypto_shim
    ${CMAKE_SOURCE_DIR}/shims/platform_shim
)
set(THIRDPARTY_INCLUDES
    ${MESHCORE_DIR}/src
    ${MESHCORE_DIR}/src/helpers
    ${MESHCORE_DIR}/lib/ed25519
)
# COMMON_INCLUDES preserved as the full ordered list for callers that don't
# care about the split (legacy interface); shadow ordering is preserved.
set(COMMON_INCLUDES ${OUR_INCLUDES} ${THIRDPARTY_INCLUDES})

if(DELAY_TUNING_RUNTIME)
    # Prepend sweep shim so its helpers/DelayTuning.h shadows the fork's version
    list(INSERT OUR_INCLUDES 0 ${CMAKE_SOURCE_DIR}/shims/sweep_shim)
    list(INSERT COMMON_INCLUDES 0 ${CMAKE_SOURCE_DIR}/shims/sweep_shim)
    message(STATUS "Runtime delay tuning: ENABLED (sweep mode)")
endif()

# ---- Shim sources (shared by both binaries) ----
set(SHIM_SOURCES
    ${CMAKE_SOURCE_DIR}/shims/arduino_shim/Arduino.cpp
    ${CMAKE_SOURCE_DIR}/shims/arduino_shim/Stream.cpp
    ${CMAKE_SOURCE_DIR}/shims/crypto_shim/SHA256.cpp
    ${CMAKE_SOURCE_DIR}/shims/crypto_shim/AES.cpp
    ${CMAKE_SOURCE_DIR}/shims/platform_shim/SimClock.cpp
    ${CMAKE_SOURCE_DIR}/shims/platform_shim/SimRNG.cpp
    ${CMAKE_SOURCE_DIR}/shims/platform_shim/SimRadio.cpp
    ${CMAKE_SOURCE_DIR}/shims/platform_shim/target.cpp
    ${CMAKE_SOURCE_DIR}/shims/lib_shim/FS.cpp
    ${CMAKE_SOURCE_DIR}/shims/lib_shim/CayenneLPP.cpp
)

if(DELAY_TUNING_RUNTIME)
    list(APPEND SHIM_SOURCES ${CMAKE_SOURCE_DIR}/shims/sweep_shim/DelayTuning.cpp)
    add_compile_definitions(DELAY_TUNING_RUNTIME)
endif()

# ---- MeshCore sources (shared by both binaries) ----
set(MESHCORE_SOURCES
    ${MESHCORE_DIR}/src/Packet.cpp
    ${MESHCORE_DIR}/src/Dispatcher.cpp
    ${MESHCORE_DIR}/src/Mesh.cpp
    ${MESHCORE_DIR}/src/Utils.cpp
    ${MESHCORE_DIR}/src/Identity.cpp
    ${MESHCORE_DIR}/src/helpers/StaticPoolPacketManager.cpp
    ${MESHCORE_DIR}/src/helpers/BaseChatMesh.cpp
    ${MESHCORE_DIR}/src/helpers/TxtDataHelpers.cpp
    ${MESHCORE_DIR}/src/helpers/AdvertDataHelpers.cpp
    ${MESHCORE_DIR}/src/helpers/IdentityStore.cpp
    ${MESHCORE_DIR}/src/helpers/TransportKeyStore.cpp
)

# ---- ed25519 library (C sources, excluding seed.c) ----
set(ED25519_SOURCES
    ${MESHCORE_DIR}/lib/ed25519/add_scalar.c
    ${MESHCORE_DIR}/lib/ed25519/keypair.c
    ${MESHCORE_DIR}/lib/ed25519/sha512.c
    ${MESHCORE_DIR}/lib/ed25519/sc.c
    ${MESHCORE_DIR}/lib/ed25519/ge.c
    ${MESHCORE_DIR}/lib/ed25519/fe.c
    ${MESHCORE_DIR}/lib/ed25519/key_exchange.c
    ${MESHCORE_DIR}/lib/ed25519/verify.c
    ${MESHCORE_DIR}/lib/ed25519/sign.c
)

# ---- Subdirectories ----
add_subdirectory(simple_repeater)
add_subdirectory(companion_radio)
add_subdirectory(orchestrator)
