"""Quality flags: everything a person should know before trusting a run.

Each flag is a plain dict that goes 1:1 into the `quality_flag` table:
    code, severity (info | warning | critical), signal, start_s, end_s, message (German, for the UI)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .clean import _runs
from .phases import PHASE_DE, phase_mask

SIGNAL_DE = {
    "bias_v": "Bias-Spannung", "power_kw": "Kathodenleistung", "cathode_v": "Kathodenspannung",
    "pressure_pa": "Kammerdruck", "ar_sccm": "Ar-Fluss", "n2_sccm": "N₂-Fluss", "temp_c": "Substrattemperatur",
}

ARC_WARN_PER_H = 10.0


def de(x: float, d: int = 1) -> str:
    """German number format for messages shown in the UI (16.0 -> '16,0')."""
    return f"{x:.{d}f}".replace(".", ",")


def _flag(code, severity, message, signal=None, start_s=None, end_s=None) -> dict:
    return {"code": code, "severity": severity, "signal": signal, "start_s": start_s, "end_s": end_s,
            "message": message}


def parse_flags(report: dict) -> list[dict]:
    out = []
    if report.get("duplicate_rows"):
        out.append(_flag("DUPLICATES", "info", f"{report['duplicate_rows']} doppelte Zeilen beim Einlesen entfernt"))
    if report.get("out_of_order_rows"):
        out.append(_flag("OUT_OF_ORDER", "info",
                         f"{report['out_of_order_rows']} Zeilen nicht in zeitlicher Reihenfolge – sortiert"))
    if report.get("sentinel_values"):
        out.append(_flag("SENTINEL", "info",
                         f"{report['sentinel_values']} Platzhalterwerte (-9999) als fehlend markiert"))
    return out


def gap_flags(gaps: list[dict], phases: list[dict]) -> list[dict]:
    out = []
    coat = next((p for p in phases if p["phase"] == "coating"), None)
    for g in gaps:
        in_coat = coat is not None and g["start_s"] < coat["end_s"] and g["end_s"] > coat["start_s"]
        sev = "warning" if (in_coat or g["duration_s"] >= 300) else "info"
        where = " (während der Beschichtung)" if in_coat else ""
        out.append(_flag("GAP", sev, f"Datenlücke {SIGNAL_DE[g['signal']]}: {de(g['duration_s'] / 60)} min{where}",
                         g["signal"], g["start_s"], g["end_s"]))
    return out


def frozen_flags(frozen: list[dict]) -> list[dict]:
    return [_flag("FROZEN_SENSOR", "warning",
                  f"{SIGNAL_DE[f['signal']]}: Sensor eingefroren ({f['duration_s'] / 60:.0f} min identischer Wert)",
                  f["signal"], f["start_s"], f["end_s"]) for f in frozen]


def detect_arcs(df: pd.DataFrame, phases: list[dict]) -> tuple[int, float, list[float]]:
    """Count short bias collapses during coating (= likely arc events)."""
    m = phase_mask(df, phases, "coating")
    bias = df["bias_v"].abs()
    ref = bias.rolling(9, center=True, min_periods=3).median()
    hit = (bias < 0.6 * ref) & m & ref.notna() & bias.notna()
    times = df.loc[hit, "t_s"].tolist()
    coat_h = m.sum() * 10.0 / 3600.0
    rate = len(times) / coat_h if coat_h > 0 else 0.0
    return len(times), rate, times


def arc_flags(n: int, rate: float) -> list[dict]:
    if rate > ARC_WARN_PER_H:
        return [_flag("ARCS", "warning", f"Erhöhte Arc-Rate: {de(rate)} pro Stunde ({n} Ereignisse)", "bias_v")]
    return []


def setpoint_flags(df: pd.DataFrame, phases: list[dict], recipe: dict, dt: float = 10.0) -> list[dict]:
    """Compare the coating step with the recipe. Rolling 5-min medians ignore arcs and noise."""
    m = phase_mask(df, phases, "coating")
    if not m.any():
        return []
    c = df.loc[m].copy()
    win = int(300 / dt)
    roll = lambda s: s.rolling(win, center=True, min_periods=win // 2).median()  # noqa: E731
    n2_set = recipe["n2_ratio"] * recipe["total_flow_sccm"]
    checks = [
        ("n2_sccm", "N₂-Fluss", (roll(c["n2_sccm"]) - n2_set) / n2_set * 100, 8.0, "%"),
        ("bias_v", "Bias-Spannung", (roll(c["bias_v"]) - recipe["bias_v"]) / abs(recipe["bias_v"]) * 100, 5.0, "%"),
        ("power_kw", "Kathodenleistung", (roll(c["power_kw"]) - recipe["power_kw"]) / recipe["power_kw"] * 100, 5.0, "%"),
        ("temp_c", "Substrattemperatur", roll(c["temp_c"]) - recipe["temp_c"], 20.0, "K"),
    ]
    out = []
    t = c["t_s"].to_numpy()
    for sig, label, dev, tol, unit in checks:
        d = dev.to_numpy()
        for a, b in _runs(np.abs(np.nan_to_num(d)) > tol):
            dur = (b - a + 1) * dt
            if dur >= 600:
                mean_dev = float(np.nanmean(d[a:b + 1]))
                out.append(_flag("SETPOINT", "warning",
                                 f"{label} weicht {mean_dev:+.0f} {unit} vom Sollwert ab ({dur / 60:.0f} min)",
                                 sig, float(t[a]), float(t[b] + dt)))
    return out


def abort_flags(phases: list[dict], recipe: dict) -> list[dict]:
    coat = next((p for p in phases if p["phase"] == "coating"), None)
    if coat is None:
        return [_flag("NO_COATING", "critical", "Keine Beschichtungsphase erkannt")]
    share = (coat["end_s"] - coat["start_s"]) / (recipe["coat_h"] * 3600.0)
    if share < 0.8:
        return [_flag("ABORTED", "critical",
                      f"{PHASE_DE['coating']} nach {share * 100:.0f} % der Sollzeit beendet – Charge abgebrochen",
                      "power_kw", coat["end_s"], None)]
    return []
