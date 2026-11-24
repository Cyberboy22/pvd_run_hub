"""Condense one cleaned run into one row of features (= one training example).

Features are computed from the *measured* signals of the coating step, not from
the recipe. If a mass-flow controller drifts, the model sees what really
happened in the chamber.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .phases import phase_mask


def run_features(df: pd.DataFrame, phases: list[dict], arc_rate: float, gaps: list[dict],
                 dt: float = 10.0) -> dict:
    coat = phase_mask(df, phases, "coating")
    etch = phase_mask(df, phases, "etching")
    c = df.loc[coat]
    mean = lambda s: float(np.nanmean(s)) if len(s) and np.isfinite(s).any() else None  # noqa: E731
    ar, n2 = mean(c["ar_sccm"]), mean(c["n2_sccm"])
    energy = float(np.nansum(df["power_kw"].to_numpy()) * dt / 3600.0)
    return {
        "coat_h": round(coat.sum() * dt / 3600.0, 3),
        "etch_min": round(etch.sum() * dt / 60.0, 1),
        "etch_bias_v": mean(df.loc[etch, "bias_v"]),
        # robust to arcs: median for the electrical signals
        "bias_v": float(np.nanmedian(c["bias_v"])) if len(c) else None,
        "power_kw": float(np.nanmedian(c["power_kw"])) if len(c) else None,
        "cathode_v": float(np.nanmedian(c["cathode_v"])) if len(c) else None,
        "pressure_pa": mean(c["pressure_pa"]),
        "n2_ratio": (n2 / (ar + n2)) if (ar is not None and n2 is not None and ar + n2 > 0) else None,
        "temp_c": mean(c["temp_c"]),
        "arc_rate_per_h": round(arc_rate, 2),
        "gap_min": round(sum(g["duration_s"] for g in gaps) / 60.0, 1),
        "energy_kwh": round(energy, 2),
    }
