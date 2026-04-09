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

# MeshCore source directory — override with -DMESHCORE_DIR=/path/to/fork
set(MESHCORE_DIR "${CMAKE_SOURCE_DIR}/MeshCore" CACHE PATH "Path to MeshCore source tree")
message(STATUS "Using MeshCore from: ${MESHCORE_DIR}")
if(NOT EXISTS "${MESHCORE_DIR}/src/Mesh.h")
    message(FATAL_ERROR "MESHCORE_DIR does not point to a valid MeshCore tree: ${MESHCORE_DIR}\n"
                        "  Expected to find ${MESHCORE_DIR}/src/Mesh.h")
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
# Shim directories MUST come before MESHCORE_DIR so that our Arduino.h, FS.h,
# target.h etc. shadow the real platform headers that MeshCore expects.
set(COMMON_INCLUDES
    ${CMAKE_SOURCE_DIR}/shims/arduino_shim
    ${CMAKE_SOURCE_DIR}/shims/lib_shim
    ${CMAKE_SOURCE_DIR}/shims/crypto_shim
    ${CMAKE_SOURCE_DIR}/shims/platform_shim
    ${MESHCORE_DIR}/src
    ${MESHCORE_DIR}/src/helpers
    ${MESHCORE_DIR}/lib/ed25519
)

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
