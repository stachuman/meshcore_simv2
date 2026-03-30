# orchestrator: multi-node single-process MeshCore simulator

# ---- OBJECT library: repeater mesh (compiled with MyMesh -> RepeaterMyMesh) ----
add_library(repeater_mesh_obj OBJECT
    RepeaterMyMesh.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/src/helpers/CommonCLI.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/src/helpers/ClientACL.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/src/helpers/RegionMap.cpp
)
target_include_directories(repeater_mesh_obj PRIVATE
    ${COMMON_INCLUDES}
    ${CMAKE_SOURCE_DIR}/MeshCore/examples/simple_repeater
    ${CMAKE_CURRENT_SOURCE_DIR}
)
target_compile_definitions(repeater_mesh_obj PRIVATE ${COMMON_DEFS} ORCHESTRATOR_BUILD)

# ---- OBJECT library: companion mesh (compiled with MyMesh -> CompanionMyMesh) ----
add_library(companion_mesh_obj OBJECT
    CompanionMyMesh.cpp
    ${CMAKE_SOURCE_DIR}/MeshCore/examples/companion_radio/DataStore.cpp
)
target_include_directories(companion_mesh_obj PRIVATE
    ${COMMON_INCLUDES}
    ${CMAKE_SOURCE_DIR}/MeshCore/examples/companion_radio
    ${CMAKE_CURRENT_SOURCE_DIR}
)
target_compile_definitions(companion_mesh_obj PRIVATE ${COMMON_DEFS} ORCHESTRATOR_BUILD)

# ---- Orchestrator executable ----
add_executable(orchestrator
    main.cpp
    Orchestrator.cpp
    NodeContext.cpp
    RepeaterNode.cpp
    CompanionNode.cpp
    LinkModel.cpp
    EventLog.cpp
    JsonConfig.cpp
    ${SHIM_SOURCES}
    ${MESHCORE_SOURCES}
    ${ED25519_SOURCES}
)

target_include_directories(orchestrator PRIVATE
    ${COMMON_INCLUDES}
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/third_party
)

target_compile_definitions(orchestrator PRIVATE
    ${COMMON_DEFS}
    ORCHESTRATOR_BUILD
)

target_link_libraries(orchestrator PRIVATE
    OpenSSL::Crypto
    repeater_mesh_obj
    companion_mesh_obj
)
