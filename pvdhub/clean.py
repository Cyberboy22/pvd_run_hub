"""Cleaning: one regular time base, honest handling of missing data.

Rules (kept deliberately simple and explicit, so a process engineer can check them):

* every run is put on a regular grid of `dt` seconds (10 s here)
* gaps up to `max_fill_s` (30 s) are linearly interpolated -- a logger hiccup
* longer gaps stay missing and become a quality flag; we never invent minutes of data
* a signal that does not change at all for >= 10 minutes while the plant is active is
  reported as a frozen sensor (real sensors always show some noise)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .ingest import SIGNALS


def to_regular_grid(df: pd.DataFrame, dt: float = 10.0) -> pd.DataFrame:
    grid = pd.DataFrame({"t_s": np.arange(0.0, df["t_s"].max() + dt / 2, dt)})
    return pd.merge_asof(grid, df.sort_values("t_s"), on="t_s", direction="nearest", tolerance=dt / 2)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Start/end indices (inclusive) of consecutive True values."""
    if not mask.any():
        return []
    m = np.concatenate([[False], mask, [False]]).astype(int)
    d = np.diff(m)
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1))


def find_gaps(df: pd.DataFrame, dt: float = 10.0, min_s: float = 30.0) -> list[dict]:
    gaps = []
    t = df["t_s"].to_numpy()
    for sig in SIGNALS:
        for a, b in _runs(df[sig].isna().to_numpy()):
            dur = (b - a + 1) * dt
            if dur > min_s:
                gaps.append({"signal": sig, "start_s": float(t[a]), "end_s": float(t[b]), "duration_s": float(dur)})
    return gaps


def find_frozen(df: pd.DataFrame, signal: str = "temp_c", dt: float = 10.0,
                min_s: float = 600.0) -> list[dict]:
    """Stretches where a normally noisy signal repeats the exact same value."""
    v = df[signal].to_numpy()
    same = np.zeros(len(v), dtype=bool)
    same[1:] = (np.diff(v) == 0) & ~np.isnan(v[1:]) & (np.abs(v[1:]) > 1e-9)
    out = []
    for a, b in _runs(same):
        dur = (b - a + 2) * dt
        if dur >= min_s:
            out.append({"signal": signal, "start_s": float(df["t_s"].iloc[a - 1]),
                        "end_s": float(df["t_s"].iloc[b]), "duration_s": float(dur)})
    return out


def fill_short_gaps(df: pd.DataFrame, dt: float = 10.0, max_fill_s: float = 30.0) -> pd.DataFrame:
    out = df.copy()
    limit = int(max_fill_s // dt)
    for sig in SIGNALS:
        isna = out[sig].isna().to_numpy()
        fill_ok = np.zeros(len(out), dtype=bool)
        for a, b in _runs(isna):
            if (b - a + 1) <= limit:
                fill_ok[a:b + 1] = True
        interp = out[sig].interpolate(limit_area="inside")
        out.loc[fill_ok, sig] = interp[fill_ok]
    return out


def mask_frozen(df: pd.DataFrame, frozen: list[dict]) -> pd.DataFrame:
    """A frozen sensor value is not a measurement -- treat it as missing."""
    out = df.copy()
    for f in frozen:
        sel = (out["t_s"] >= f["start_s"]) & (out["t_s"] <= f["end_s"])
        out.loc[sel, f["signal"]] = np.nan
    return out
