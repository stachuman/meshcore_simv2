cmake_minimum_required(VERSION 3.16)
project(meshcore_real_sim LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_C_STANDARD 11)

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

# ---- Common include directories (shims first) ----
set(COMMON_INCLUDES
    ${CMAKE_SOURCE_DIR}/shims/arduino_shim
    ${CMAKE_SOURCE_DIR}/shims/lib_shim
    ${CMAKE_SOURCE_DIR}/shims/crypto_shim
    ${CMAKE_SOURCE_DIR}/shims/platform_shim
    ${CMAKE_SOURCE_DIR}/MeshCore/src
    ${CMAKE_SOURCE_DIR}/MeshCore/src/helpers
    ${CMAKE_SOURCE_DIR}/MeshCore/lib/ed25519
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
    ${CMAKE_SOURCE_DIR}/MeshCore/src/Packet.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/src/Dispatcher.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/src/Mesh.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/src/Utils.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/src/Identity.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/src/helpers/StaticPoolPacketManager.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/src/helpers/BaseChatMesh.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/src/helpers/TxtDataHelpers.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/src/helpers/AdvertDataHelpers.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/src/helpers/IdentityStore.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/src/helpers/TransportKeyStore.cpp
)

# ---- ed25519 library (C sources, excluding seed.c) ----
set(ED25519_SOURCES
    ${CMAKE_SOURCE_DIR}/MeshCore/lib/ed25519/add_scalar.c
    ${CMAKE_SOURCE_DIR}/MeshCore/lib/ed25519/keypair.c
    ${CMAKE_SOURCE_DIR}/MeshCore/lib/ed25519/sha512.c
    ${CMAKE_SOURCE_DIR}/MeshCore/lib/ed25519/sc.c
    ${CMAKE_SOURCE_DIR}/MeshCore/lib/ed25519/ge.c
    ${CMAKE_SOURCE_DIR}/MeshCore/lib/ed25519/fe.c
    ${CMAKE_SOURCE_DIR}/MeshCore/lib/ed25519/key_exchange.c
    ${CMAKE_SOURCE_DIR}/MeshCore/lib/ed25519/verify.c
    ${CMAKE_SOURCE_DIR}/MeshCore/lib/ed25519/sign.c
)

# ---- Subdirectories ----
add_subdirectory(simple_repeater)
add_subdirectory(companion_radio)
add_subdirectory(orchestrator)
