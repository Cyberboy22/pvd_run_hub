"""Read raw plant logs and bring them into ONE canonical schema.

Every plant exports differently. This module hides that:

    parse_raw_file(path) -> RawRun(meta, frame, report)

The canonical frame always has these columns, in these units:

    t_s          seconds since run start
    bias_v       substrate bias voltage            [V]   (negative)
    power_kw     total cathode power               [kW]
    cathode_v    cathode / discharge voltage       [V]
    pressure_pa  chamber pressure                  [Pa]
    ar_sccm      argon flow                        [sccm]
    n2_sccm      nitrogen flow                     [sccm]
    temp_c       substrate temperature             [°C]

The `report` records what had to be fixed while reading (sentinel values,
duplicated rows, rows out of order) so it can become a quality flag later.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

BERLIN = ZoneInfo("Europe/Berlin")
SIGNALS = ["bias_v", "power_kw", "cathode_v", "pressure_pa", "ar_sccm", "n2_sccm", "temp_c"]
CANONICAL = ["t_s", *SIGNALS]


@dataclass
class RawRun:
    meta: dict                      # run_id, plant_id, recipe_id, operator, started_at (local, naive ISO)
    frame: pd.DataFrame             # canonical columns, sorted, duplicates removed
    report: dict = field(default_factory=dict)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finish(df: pd.DataFrame, report: dict) -> pd.DataFrame:
    """Shared last step: order by time, drop exact duplicate time stamps."""
    report["rows_read"] = int(len(df))
    report["out_of_order_rows"] = int((np.diff(df["t_s"].to_numpy()) < 0).sum())
    df = df.sort_values("t_s", kind="stable")
    dup = df["t_s"].duplicated(keep="first")
    report["duplicate_rows"] = int(dup.sum())
    df = df.loc[~dup].reset_index(drop=True)
    report["missing_values"] = {c: int(df[c].isna().sum()) for c in SIGNALS if df[c].isna().any()}
    return df[CANONICAL]


# ---------------------------------------------------------------- MSIP ----

def parse_msip(path: Path) -> RawRun:
    """German HMI export: '#' header, ';' separated, decimal comma, dd.mm.yyyy, mPa."""
    lines = path.read_text(encoding="utf-8").splitlines()
    head = {k.strip("# ").strip(): v.strip() for k, v in
            (ln.split(":", 1) for ln in lines if ln.startswith("#"))}
    body = "\n".join(ln for ln in lines if not ln.startswith("#"))
    raw = pd.read_csv(StringIO(body), sep=";", decimal=",")
    ts = pd.to_datetime(raw["Zeit"], format="%d.%m.%Y %H:%M:%S")
    start = ts.min()
    df = pd.DataFrame({
        "t_s": (ts - start).dt.total_seconds(),
        "bias_v": raw["Bias [V]"],
        "power_kw": raw["Leistung [kW]"],
        "cathode_v": raw["Kathodenspannung [V]"],
        "pressure_pa": raw["Druck [mPa]"] / 1000.0,
        "ar_sccm": raw["Ar [sccm]"],
        "n2_sccm": raw["N2 [sccm]"],
        "temp_c": raw["Temperatur [°C]"],
    })
    report: dict = {"format": "msip_csv_de"}
    meta = {"run_id": head["Charge"], "plant_id": head["Anlage"], "recipe_id": head["Rezept"],
            "operator": head.get("Bediener"), "started_at": start.isoformat(timespec="minutes")}
    return RawRun(meta, _finish(df, report), report)


# ----------------------------------------------------------------- AIP ----

def parse_aip(path: Path) -> RawRun:
    """English controller export: ISO-8601 UTC time stamps, Pa, arc current x voltage."""
    raw = pd.read_csv(path, na_values=["nan", "NaN"])
    ts_utc = pd.to_datetime(raw["timestamp"], utc=True)
    ts = ts_utc.dt.tz_convert(BERLIN).dt.tz_localize(None)  # store local plant time
    start = ts.min()
    df = pd.DataFrame({
        "t_s": (ts - start).dt.total_seconds(),
        "bias_v": raw["bias_voltage_V"],
        "power_kw": raw["arc_current_A"] * raw["arc_voltage_V"] / 1000.0,
        "cathode_v": raw["arc_voltage_V"],
        "pressure_pa": raw["chamber_pressure_Pa"],
        "ar_sccm": raw["Ar_flow_sccm"],
        "n2_sccm": raw["N2_flow_sccm"],
        "temp_c": raw["substrate_temp_C"],
    })
    report: dict = {"format": "aip_csv_utc", "timezone_converted": "UTC -> Europe/Berlin"}
    run_id = path.stem  # this controller does not write the run id into the file
    meta = {"run_id": run_id, "plant_id": "AIP-1", "recipe_id": None, "operator": None,
            "started_at": start.isoformat(timespec="minutes")}
    return RawRun(meta, _finish(df, report), report)


# ------------------------------------------------------------------ HS ----

def parse_hs(path: Path) -> RawRun:
    """Lab-built logger: KEY=VALUE header, TSV, relative seconds, W, mbar, K, -9999 = missing."""
    lines = path.read_text(encoding="utf-8").splitlines()
    head, body_start = {}, 0
    for i, ln in enumerate(lines):
        if "=" in ln and "\t" not in ln:
            k, v = ln.split("=", 1)
            head[k.strip()] = v.strip()
        else:
            body_start = i
            break
    raw = pd.read_csv(StringIO("\n".join(lines[body_start:])), sep="\t")
    sentinels = int((raw == -9999).sum().sum())
    raw = raw.replace(-9999, np.nan)
    df = pd.DataFrame({
        "t_s": raw["t_rel[s]"].astype(float),
        "bias_v": raw["U_bias[V]"],
        "power_kw": raw["P_kat[W]"] / 1000.0,
        "cathode_v": raw["U_kat[V]"],
        "pressure_pa": raw["p[mbar]"] * 100.0,
        "ar_sccm": raw["Q_Ar[sccm]"],
        "n2_sccm": raw["Q_N2[sccm]"],
        "temp_c": raw["T[K]"] - 273.15,
    })
    report: dict = {"format": "hs_tsv_rel", "sentinel_values": sentinels}
    start = datetime.strptime(head["START"], "%Y-%m-%d %H:%M:%S")
    meta = {"run_id": head["RUN"], "plant_id": "HS-1", "recipe_id": head.get("RECIPE"),
            "operator": head.get("OP"), "started_at": start.isoformat(timespec="minutes")}
    return RawRun(meta, _finish(df, report), report)


# --------------------------------------------------------------- router ---

def detect_format(path: Path) -> str:
    """Sniff the first line -- we do not trust file names alone."""
    first = path.open(encoding="utf-8").readline()
    if first.startswith("# Anlage"):
        return "msip_csv_de"
    if first.startswith("timestamp,"):
        return "aip_csv_utc"
    if first.startswith("START="):
        return "hs_tsv_rel"
    raise ValueError(f"Unbekanntes Logformat: {path.name}")


PARSERS = {"msip_csv_de": parse_msip, "aip_csv_utc": parse_aip, "hs_tsv_rel": parse_hs}


def parse_raw_file(path: Path) -> RawRun:
    run = PARSERS[detect_format(path)](path)
    run.meta["raw_file"] = path.name
    run.meta["raw_sha256"] = sha256_of(path)
    return run
