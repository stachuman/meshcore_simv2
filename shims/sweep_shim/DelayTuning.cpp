#include <helpers/DelayTuning.h>
#include <algorithm>

// Default table: matches the fork's compiled-in values so behavior is
// identical when no runtime override is applied.
DelayTuning g_delay_tuning_table[13] = {
    {0.500f, 0.000f, 1.000f},  // 0 neighbors
    {1.100f, 0.300f, 1.600f},  // 1 neighbors
    {1.700f, 0.600f, 2.200f},  // 2 neighbors
    {2.300f, 0.900f, 2.800f},  // 3 neighbors
    {2.900f, 1.200f, 3.400f},  // 4 neighbors
    {3.500f, 1.500f, 4.000f},  // 5 neighbors
    {4.100f, 1.800f, 4.600f},  // 6 neighbors
    {4.700f, 2.100f, 5.200f},  // 7 neighbors
    {5.300f, 2.400f, 5.800f},  // 8 neighbors
    {5.900f, 2.700f, 6.400f},  // 9 neighbors
    {6.500f, 3.000f, 7.000f},  // 10 neighbors
    {7.100f, 3.300f, 7.600f},  // 11 neighbors
    {7.700f, 3.600f, 8.200f},  // 12 neighbors
};

void setDelayTuningLinear(float tx_b, float tx_s,
                          float dtx_b, float dtx_s,
                          float rx_b, float rx_s,
                          float clamp_min, float clamp_max) {
    for (int n = 0; n < DELAY_TUNING_TABLE_SIZE; n++) {
        auto clamp = [=](float v) {
            return std::min(clamp_max, std::max(clamp_min, v));
        };
        g_delay_tuning_table[n].tx_delay        = clamp(tx_b  + tx_s  * n);
        g_delay_tuning_table[n].direct_tx_delay  = clamp(dtx_b + dtx_s * n);
        g_delay_tuning_table[n].rx_delay_base    = clamp(rx_b  + rx_s  * n);
    }
}
