"""Split a run into its process phases from the signals alone.

Heating -> Etching -> Coating -> Cooling

The plant logs do not say which step is running, so the step is inferred:

* Coating: reactive gas (N2) flows AND the cathodes run at substantial power
* Etching: high substrate bias (> 250 V) or argon flow, without reactive gas, before coating
* Heating: everything before etching (pump-down + radiant heating)
* Cooling: everything after the coating step

Short interruptions (arcs, a dropped sample) must not split a phase, so the masks
are smoothed with a rolling majority vote and tiny holes are closed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PHASES = ["heating", "etching", "coating", "cooling"]
PHASE_DE = {"heating": "Heizen", "etching": "Ätzen", "coating": "Beschichten", "cooling": "Abkühlen"}


def _smooth(mask: np.ndarray, window: int = 7) -> np.ndarray:
    return pd.Series(mask.astype(float)).rolling(window, center=True, min_periods=1).mean().to_numpy() > 0.5


def _longest_block(mask: np.ndarray, close_gap: int) -> tuple[int, int] | None:
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return None
    # split where the hole between True values is larger than close_gap samples
    breaks = np.flatnonzero(np.diff(idx) > close_gap + 1)
    starts = np.concatenate([[idx[0]], idx[breaks + 1]])
    ends = np.concatenate([idx[breaks], [idx[-1]]])
    k = int(np.argmax(ends - starts))
    return int(starts[k]), int(ends[k])


def detect_phases(df: pd.DataFrame, dt: float = 10.0) -> list[dict]:
    t = df["t_s"].to_numpy()
    power = df["power_kw"].ffill().fillna(0).to_numpy()
    n2 = df["n2_sccm"].ffill().fillna(0).to_numpy()
    bias = df["bias_v"].abs().ffill().fillna(0).to_numpy()
    ar = df["ar_sccm"].ffill().fillna(0).to_numpy()

    p_ref = np.percentile(power[power > 0.05], 90) if (power > 0.05).any() else 0.0
    coat = _smooth((n2 > 5.0) & (power > 0.4 * p_ref))
    block = _longest_block(coat, close_gap=int(180 / dt))
    if block is None:
        raise ValueError("Keine Beschichtungsphase gefunden")
    c0, c1 = block

    # two independent hints, so one missing signal cannot hide the etching step
    etch = _smooth(((bias > 250.0) | (ar > 20.0)) & (n2 <= 5.0))
    etch[c0:] = False
    eblock = _longest_block(etch, close_gap=int(120 / dt))
    e0, e1 = eblock if eblock is not None else (c0, c0 - 1)

    segs = [("heating", 0, e0 - 1), ("etching", e0, c0 - 1), ("coating", c0, c1), ("cooling", c1 + 1, len(t) - 1)]
    out = []
    for name, a, b in segs:
        if b >= a:
            out.append({"phase": name, "start_s": float(t[a]), "end_s": float(t[b] + dt)})
    return out


def phase_mask(df: pd.DataFrame, phases: list[dict], name: str) -> np.ndarray:
    for p in phases:
        if p["phase"] == name:
            return ((df["t_s"] >= p["start_s"]) & (df["t_s"] < p["end_s"])).to_numpy()
    return np.zeros(len(df), dtype=bool)
