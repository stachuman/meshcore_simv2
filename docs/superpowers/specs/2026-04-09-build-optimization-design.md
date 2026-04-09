# Build Optimization Design

**Date**: 2026-04-09
**Purpose**: Enable dual-mode compilation for maximum runtime performance in delay optimization sweeps while maintaining portable Docker builds

## Problem Statement

The orchestrator runs simulations that are computationally intensive (physics models, collision detection, SNR calculations, fading models). Currently, builds use no optimization flags, resulting in slow simulation execution. Delay optimization sweeps require maximum performance on the development machine, while Docker releases need portability across different x86_64 CPUs.

## Requirements

1. **NativeRelease mode**: Aggressive optimizations for local delay optimization work (maximum speed on specific CPU)
2. **Release mode**: Portable optimizations for Docker distribution (works on any x86_64 CPU)
3. **Backward compatibility**: Existing unoptimized builds remain available for debugging
4. **Workflow integration**: Delay optimization scripts automatically use optimized builds
5. **Correctness**: Optimizations must not break simulation physics or test suite

## Architecture

### CMake Build Types

We add two custom build types extending CMake's standard configuration:

#### NativeRelease (Local Delay Optimization)
**Compiler Flags**:
- `-O3` - Maximum optimization level
- `-march=native` - CPU-specific instructions (AVX2, SSE4.2, etc.)
- `-mtune=native` - Instruction scheduling for specific CPU
- `-DNDEBUG` - Disable runtime assertions
- `-flto=auto` - Link-Time Optimization with parallel jobs
- `-ffast-math` - Relaxed IEEE 754 compliance for faster floating-point

**Linker Flags**:
- `-flto=auto` - Enable LTO during linking

**Use case**: Building on the development machine (12 cores) for running delay optimization sweeps.

#### Release (Docker Distribution)
**Compiler Flags**:
- `-O3` - Maximum optimization level
- `-march=x86-64` - Generic x86_64 baseline (or omit for compiler default)
- `-DNDEBUG` - Disable runtime assertions
- `-flto=auto` - Link-Time Optimization

**Linker Flags**:
- `-flto=auto` - Enable LTO during linking

**Use case**: Docker builds that run on any x86_64 host.

**Key difference**: No `-march=native` or `-ffast-math` to ensure portability and IEEE 754 compliance.

### Build Commands

```bash
# For delay optimization (local machine):
cmake -S . -B build-native -DCMAKE_BUILD_TYPE=NativeRelease
cmake --build build-native -j12

# For Docker (portable):
cmake -S . -B build-release -DCMAKE_BUILD_TYPE=Release
cmake --build build-release -j12

# For development (debugging):
cmake -S . -B build
cmake --build build
```

## Component Changes

### 1. Root CMakeLists.txt

Add custom build type configuration after the `project()` declaration:

```cmake
# Custom build types for optimization
if(CMAKE_BUILD_TYPE STREQUAL "NativeRelease")
    set(CMAKE_CXX_FLAGS_NATIVERELEASE "-O3 -march=native -mtune=native -DNDEBUG -flto=auto -ffast-math" CACHE STRING "" FORCE)
    set(CMAKE_C_FLAGS_NATIVERELEASE "-O3 -march=native -mtune=native -DNDEBUG -flto=auto -ffast-math" CACHE STRING "" FORCE)
    set(CMAKE_EXE_LINKER_FLAGS_NATIVERELEASE "-flto=auto" CACHE STRING "" FORCE)
    mark_as_advanced(CMAKE_CXX_FLAGS_NATIVERELEASE CMAKE_C_FLAGS_NATIVERELEASE CMAKE_EXE_LINKER_FLAGS_NATIVERELEASE)
endif()

if(CMAKE_BUILD_TYPE STREQUAL "Release")
    set(CMAKE_CXX_FLAGS_RELEASE "-O3 -DNDEBUG -flto=auto" CACHE STRING "" FORCE)
    set(CMAKE_C_FLAGS_RELEASE "-O3 -DNDEBUG -flto=auto" CACHE STRING "" FORCE)
    set(CMAKE_EXE_LINKER_FLAGS_RELEASE "-flto=auto" CACHE STRING "" FORCE)
    mark_as_advanced(CMAKE_CXX_FLAGS_RELEASE CMAKE_C_FLAGS_RELEASE CMAKE_EXE_LINKER_FLAGS_RELEASE)
endif()
```

### 2. Docker Integration

Update `webapp/Dockerfile` build stage to use Release mode:

```dockerfile
# Build stage
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && \
    cmake --build build --target orchestrator -j$(nproc)
```

### 3. Delay Optimization Scripts

Update `delay_optimization/run_{sparse,medium,dense}.sh` to build/use NativeRelease:

```bash
#!/bin/bash
set -e

# Use optimized build for maximum performance
ORCHESTRATOR="../build-native/orchestrator/orchestrator"

# Build if not present
if [ ! -f "$ORCHESTRATOR" ]; then
    echo "Building NativeRelease orchestrator for maximum performance..."
    cmake -S .. -B ../build-native -DCMAKE_BUILD_TYPE=NativeRelease
    cmake --build ../build-native --target orchestrator -j$(nproc)
fi

# Rest of script continues with $ORCHESTRATOR...
```

## Testing & Validation

### Build Verification
1. Test NativeRelease builds successfully: `cmake -DCMAKE_BUILD_TYPE=NativeRelease ...`
2. Test Release builds successfully: `cmake -DCMAKE_BUILD_TYPE=Release ...`
3. Verify both binaries run without crashes

### Correctness Testing
Run the test suite with optimized builds to ensure optimizations don't break physics:

```bash
# Build NativeRelease
cmake -S . -B build-native -DCMAKE_BUILD_TYPE=NativeRelease
cmake --build build-native

# Run test suite
cd test
ORCHESTRATOR=../build-native/orchestrator/orchestrator ./run_tests.sh
```

All tests should pass. If `-ffast-math` causes failures, it can be removed from NativeRelease.

### Performance Benchmarking
Run a delay optimization sweep before/after to measure speedup:

```bash
# Baseline (unoptimized)
time ./run_sparse.sh  # using build/orchestrator/orchestrator

# Optimized
time ./run_sparse.sh  # using build-native/orchestrator/orchestrator
```

Expected speedup: 4-6x faster with NativeRelease.

## Trade-offs

### NativeRelease
**Pros**:
- Maximum runtime performance (4-6x speedup expected)
- Optimized for available CPU features (AVX2, etc.)
- Faster delay optimization sweeps

**Cons**:
- Longer compile time (3-5x slower due to LTO)
- Binary not portable to other CPUs
- Harder to debug (optimizations obscure code flow)
- `-ffast-math` may cause subtle floating-point differences

### Release
**Pros**:
- Portable across x86_64 CPUs
- IEEE 754 compliant
- Still 2-3x faster than unoptimized

**Cons**:
- Slightly slower than NativeRelease (~20-30%)
- Longer compile time due to LTO

## Success Criteria

1. ✅ Both NativeRelease and Release builds succeed
2. ✅ Test suite passes with optimized builds
3. ✅ Docker image builds with Release mode
4. ✅ Delay optimization scripts use NativeRelease automatically
5. ✅ Measured 4-6x speedup in simulation runtime
6. ✅ Documentation updated (README build instructions)

## Future Considerations

- **Profile-Guided Optimization (PGO)**: Could provide another 10-20% speedup by profiling representative workloads
- **Parallelization**: The orchestrator is single-threaded; multi-threading could provide additional speedup
- **Alternative compilers**: Clang vs GCC may have different optimization characteristics worth benchmarking
