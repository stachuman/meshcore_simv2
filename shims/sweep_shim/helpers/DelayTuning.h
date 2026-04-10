#pragma once

#include <stdint.h>

struct DelayTuning {
  float tx_delay;
  float direct_tx_delay;
  float rx_delay_base;
};

// Runtime-mutable delay tuning table (sweep shim).
// When DELAY_TUNING_RUNTIME is defined, this replaces the fork's static const
// table with a mutable extern so optimize_tuning.py can vary parameters
// without rebuilding.
extern DelayTuning g_delay_tuning_table[13];
#define DELAY_TUNING_TABLE_SIZE  13

static inline const DelayTuning& getDelayTuning(int neighbor_count) {
  int idx = neighbor_count;
  if (idx < 0) idx = 0;
  if (idx >= DELAY_TUNING_TABLE_SIZE) idx = DELAY_TUNING_TABLE_SIZE - 1;
  return g_delay_tuning_table[idx];
}

// Fill the table from a linear model: value(n) = clamp(base + slope * n, min, max)
void setDelayTuningLinear(float tx_b, float tx_s,
                          float dtx_b, float dtx_s,
                          float rx_b, float rx_s,
                          float clamp_min, float clamp_max);
