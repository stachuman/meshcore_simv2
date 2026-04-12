-- Delay model: delay(n) = clamp(base + slope * n^power, min, max)
--
-- Parameters via --lua-var:
--   tx_base, tx_slope, tx_pow     txdelay = clamp(tx_base + tx_slope * n^tx_pow, min, max)
--   dtx_base, dtx_slope, dtx_pow  direct.txdelay
--   rx_base, rx_slope, rx_pow     rxdelay
--   clamp_min (default 0)
--   clamp_max (default 6.0)
--
-- power=1.0 (default) gives linear model, power=0.5 gives sqrt scaling.

local tx_base   = vars.tx_base   or 0
local tx_slope  = vars.tx_slope  or 0
local tx_pow    = vars.tx_pow    or 1.0
local dtx_base  = vars.dtx_base  or 0
local dtx_slope = vars.dtx_slope or 0
local dtx_pow   = vars.dtx_pow   or 1.0
local rx_base   = vars.rx_base   or 0
local rx_slope  = vars.rx_slope  or 0
local rx_pow    = vars.rx_pow    or 1.0
local clamp_min = vars.clamp_min or 0
local clamp_max = vars.clamp_max or 6.0

local function clamp(val, lo, hi)
    if val < lo then return lo end
    if val > hi then return hi end
    return val
end

sim:initialize()

sim:for_each_repeater(function(name, status)
    local n = status.neighbor_count or 0
    local tx  = clamp(tx_base  + tx_slope  * n ^ tx_pow,  clamp_min, clamp_max)
    local dtx = clamp(dtx_base + dtx_slope * n ^ dtx_pow, clamp_min, clamp_max)
    local rx  = clamp(rx_base  + rx_slope  * n ^ rx_pow,  clamp_min, clamp_max)

    sim:cmd(name, string.format("set txdelay %.4f", tx))
    sim:cmd(name, string.format("set direct.txdelay %.4f", dtx))
    sim:cmd(name, string.format("set rxdelay %.4f", rx))
end)

sim:run_all()
sim:finalize()
