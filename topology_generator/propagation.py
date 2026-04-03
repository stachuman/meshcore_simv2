"""ITM (Longley-Rice) propagation model wrapper and link budget calculations."""

import math

from itmlogic.preparatory_subroutines.qlrps import qlrps
from itmlogic.preparatory_subroutines.qlrpfl import qlrpfl
from itmlogic.statistics.avar import avar
from itmlogic.misc.qerfi import qerfi

# SNR-to-loss logistic sigmoid (same as convert_topology.py)
_LOSS_SNR_MID = -6.0
_LOSS_STEEPNESS = 0.8


def snr_to_loss(snr_db: float) -> float:
    """Map SNR to packet loss probability via logistic sigmoid."""
    return round(1.0 / (1.0 + math.exp(_LOSS_STEEPNESS * (snr_db - _LOSS_SNR_MID))), 4)


def noise_floor_dbm(bandwidth_hz: float, noise_figure_db: float = 6.0) -> float:
    """Thermal noise floor in dBm.

    noise_floor = -174 + 10*log10(BW_Hz) + NF_dB
    For BW=62500 Hz, NF=6 dB: -174 + 47.96 + 6 = -120.04 dBm
    """
    return -174.0 + 10.0 * math.log10(bandwidth_hz) + noise_figure_db


def fspl_db(distance_km: float, freq_mhz: float) -> float:
    """Free-space path loss in dB.

    FSPL = 32.44 + 20*log10(d_km) + 20*log10(f_MHz)
    """
    if distance_km <= 0:
        return 0.0
    return 32.44 + 20.0 * math.log10(distance_km) + 20.0 * math.log10(freq_mhz)


def compute_path_loss_itm(
    surface_profile_m: list[float],
    distance_km: float,
    freq_mhz: float,
    antenna_heights_m: tuple[float, float],
    polarization: int = 1,
    climate: int = 6,
    ground_permittivity: float = 15.0,
    ground_conductivity: float = 0.005,
    surface_refractivity: float = 314.0,
) -> dict:
    """Run ITM point-to-point prediction.

    Returns dict with:
        loss_median_db: total path loss at 50% reliability / 50% confidence
        loss_10pct_db: total path loss at 10% reliability / 50% confidence
        loss_90pct_db: total path loss at 90% reliability / 50% confidence
        kwx: warning/error flag (0=ok, 1-4=increasing severity)
    """
    num_pts = len(surface_profile_m)
    if num_pts < 3 or distance_km <= 0:
        return {"loss_median_db": 0.0, "loss_10pct_db": 0.0,
                "loss_90pct_db": 0.0, "kwx": 4}

    step_m = distance_km * 1000.0 / (num_pts - 1)
    pfl = [num_pts - 1, step_m] + list(surface_profile_m)

    # General preparatory: compute wave number, earth curvature, impedance
    wn, gme, ens, zgnd = qlrps(freq_mhz, 0.0, surface_refractivity,
                                polarization, ground_permittivity,
                                ground_conductivity)

    prop = {
        "pfl": pfl,
        "hg": list(antenna_heights_m),
        "wn": wn,
        "gme": gme,
        "ens": ens,
        "zgnd": zgnd,
        "kwx": 0,
        "lvar": 5,
        "mdvar": 12,       # single message mode
        "mdvarx": 12,
        "klimx": climate,
        "klim": climate,
    }

    # Point-to-point preparatory
    prop = qlrpfl(prop)

    # ITM returns excess attenuation over FSPL; add FSPL for total
    fspl = fspl_db(distance_km, freq_mhz)

    # Get z-scores for reliability levels
    zc_50 = qerfi([0.5])[0]   # confidence = 50%
    zt_50 = qerfi([0.5])[0]   # time = 50%

    zr_50 = qerfi([0.5])[0]
    zr_10 = qerfi([0.1])[0]
    zr_90 = qerfi([0.9])[0]

    excess_50, prop = avar(zt_50, zr_50, zc_50, prop)
    excess_10, _ = avar(zt_50, zr_10, zc_50, prop)
    excess_90, _ = avar(zt_50, zr_90, zc_50, prop)

    return {
        "loss_median_db": fspl + excess_50,
        "loss_10pct_db": fspl + excess_10,
        "loss_90pct_db": fspl + excess_90,
        "kwx": prop.get("kwx", 0),
    }


def compute_link(
    surface_profile_m: list[float],
    distance_km: float,
    freq_mhz: float,
    antenna_heights_m: tuple[float, float],
    tx_power_dbm: float,
    bandwidth_hz: float,
    noise_figure_db: float,
    min_snr: float,
    climate: int = 6,
    polarization: int = 1,
    ground_permittivity: float = 15.0,
    ground_conductivity: float = 0.005,
    surface_refractivity: float = 314.0,
    clutter_db: float = 0.0,
) -> dict | None:
    """Compute a single link's SNR, RSSI, snr_std_dev, and loss.

    clutter_db: additional path loss for urban/suburban clutter not modeled
                by ITM (buildings, cables, connectors). Applied to both ends.
    Returns None if link SNR is below min_snr.
    Returns dict with {snr, rssi, snr_std_dev, loss, dist_km} otherwise.
    """
    nf = noise_floor_dbm(bandwidth_hz, noise_figure_db)

    # Short links: use FSPL directly (ITM unreliable < 0.5km)
    if distance_km < 0.5:
        fspl = fspl_db(distance_km, freq_mhz)
        snr = tx_power_dbm - fspl - clutter_db - nf
        if snr < min_snr:
            return None
        rssi = nf + snr
        return {
            "snr": round(snr, 2),
            "rssi": round(rssi, 2),
            "snr_std_dev": 1.0,
            "dist_km": round(distance_km, 3),
        }

    itm = compute_path_loss_itm(
        surface_profile_m, distance_km, freq_mhz,
        antenna_heights_m, polarization, climate,
        ground_permittivity, ground_conductivity,
        surface_refractivity,
    )

    snr_median = tx_power_dbm - itm["loss_median_db"] - clutter_db - nf
    if snr_median < min_snr:
        return None

    # snr_std_dev from 10th-90th percentile spread
    snr_10 = tx_power_dbm - itm["loss_10pct_db"] - clutter_db - nf
    snr_90 = tx_power_dbm - itm["loss_90pct_db"] - clutter_db - nf
    std_dev = abs(snr_10 - snr_90) / 2.56  # 10-90 span ≈ 2.56 sigma
    std_dev = max(std_dev, 0.5)  # minimum variability

    rssi = nf + snr_median
    loss = snr_to_loss(snr_median)

    result = {
        "snr": round(snr_median, 2),
        "rssi": round(rssi, 2),
        "snr_std_dev": round(std_dev, 2),
        "dist_km": round(distance_km, 3),
    }
    if loss > 0.0001:
        result["loss"] = loss

    return result
