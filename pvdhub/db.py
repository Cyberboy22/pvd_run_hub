"""Load everything into one SQLite database.

    raw logs  ─┐
    run sheet ─┼─> parse -> clean -> phases -> flags -> features ─> SQLite
    recipes   ─┤
    lab sheet ─┘
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from . import clean, features, phases, quality
from .ingest import SIGNALS, parse_raw_file

SCHEMA = Path(__file__).with_name("schema.sql")
STORE_DT_S = 30


def _none(v):
    if v is None:
        return None
    if isinstance(v, (float, np.floating)) and not np.isfinite(v):
        return None
    return float(v) if isinstance(v, (np.floating, np.integer)) else v


def process_run(path: Path, recipe: dict) -> dict:
    """Everything that happens to ONE raw file. Pure function -> easy to test."""
    raw = parse_raw_file(path)
    df = clean.to_regular_grid(raw.frame)
    gaps = clean.find_gaps(df)
    frozen = clean.find_frozen(df)
    df = clean.mask_frozen(clean.fill_short_gaps(df), frozen)
    ph = phases.detect_phases(df)
    n_arcs, arc_rate, arc_times = quality.detect_arcs(df, ph)
    flags = (quality.parse_flags(raw.report) + quality.gap_flags(gaps, ph) + quality.frozen_flags(frozen)
             + quality.arc_flags(n_arcs, arc_rate) + quality.setpoint_flags(df, ph, recipe)
             + quality.abort_flags(ph, recipe))
    feats = features.run_features(df, ph, arc_rate, gaps)
    status = "aborted" if any(f["code"] in ("ABORTED", "NO_COATING") for f in flags) else "ok"
    return {"meta": raw.meta, "report": raw.report, "frame": df, "phases": ph, "flags": flags,
            "features": feats, "status": status, "arc_times": arc_times}


def _downsample(df: pd.DataFrame, dt: int = STORE_DT_S) -> pd.DataFrame:
    b = (df["t_s"] // dt) * dt
    out = df.groupby(b)[SIGNALS].mean()  # NaN-aware: a window that is all missing stays NULL
    out.index.name = "t_s"
    return out.reset_index()


def build_database(sim_dir: Path, db_path: Path, verbose: bool = True) -> dict:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))

    plants = pd.read_csv(sim_dir / "plants.csv")
    targets = pd.read_csv(sim_dir / "targets.csv")
    recipes = pd.read_csv(sim_dir / "recipes.csv")
    sheet = pd.read_csv(sim_dir / "run_sheet.csv").set_index("run_id")
    lab = pd.read_csv(sim_dir / "lab_measurements.csv")

    plants.to_sql("plant", con, if_exists="append", index=False)
    targets.to_sql("target_set", con, if_exists="append", index=False)
    recipes.assign(is_reference=recipes["is_reference"].astype(int)).to_sql("recipe", con, if_exists="append", index=False)
    recipe_by_id = recipes.set_index("recipe_id").to_dict("index")

    stats = {"files": 0, "rows_read": 0, "duplicates": 0, "out_of_order": 0, "sentinels": 0,
             "flags": 0, "values_cleaned": 0, "aborted": 0}
    raw_files = sorted(p for p in (sim_dir / "raw").iterdir() if p.suffix in (".csv", ".tsv"))
    for path in raw_files:
        run_id = path.stem
        book = sheet.loc[run_id]  # the run sheet links log file <-> recipe <-> target set
        recipe = recipe_by_id[book["recipe_id"]]
        res = process_run(path, recipe)
        m, f = res["meta"], res["features"]
        if m["run_id"] != run_id:
            raise ValueError(f"Run-ID in Datei ({m['run_id']}) passt nicht zum Dateinamen ({run_id})")
        con.execute("INSERT INTO coating_run VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (run_id, book["plant_id"], book["recipe_id"], book["target_id"], m["started_at"],
                     m.get("operator") or book["operator"], f"raw/{path.name}", m["raw_sha256"],
                     res["status"], f["energy_kwh"]))
        con.executemany("INSERT INTO run_phase VALUES (?,?,?,?)",
                        [(run_id, p["phase"], p["start_s"], p["end_s"]) for p in res["phases"]])
        sig = _downsample(res["frame"]).round({"bias_v": 1, "power_kw": 2, "cathode_v": 1, "pressure_pa": 4,
                                                "ar_sccm": 1, "n2_sccm": 1, "temp_c": 1})
        con.executemany("INSERT INTO process_signal VALUES (?,?,?,?,?,?,?,?,?)",
                        [(run_id, *[_none(v) for v in row]) for row in sig[["t_s", *SIGNALS]].itertuples(index=False)])
        con.executemany("INSERT INTO quality_flag (run_id, code, severity, signal, start_s, end_s, message) "
                        "VALUES (?,?,?,?,?,?,?)",
                        [(run_id, fl["code"], fl["severity"], fl["signal"], fl["start_s"], fl["end_s"], fl["message"])
                         for fl in res["flags"]])
        con.execute("INSERT INTO run_features VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                    (run_id, *[_none(f[k]) for k in ["coat_h", "etch_min", "etch_bias_v", "bias_v", "power_kw",
                                                     "cathode_v", "pressure_pa", "n2_ratio", "temp_c",
                                                     "arc_rate_per_h", "gap_min"]]))
        rep = res["report"]
        stats["files"] += 1
        stats["rows_read"] += rep.get("rows_read", 0)
        stats["duplicates"] += rep.get("duplicate_rows", 0)
        stats["out_of_order"] += rep.get("out_of_order_rows", 0)
        stats["sentinels"] += rep.get("sentinel_values", 0)
        stats["values_cleaned"] += int(rep.get("rows_read", 0) * len(SIGNALS))
        stats["flags"] += len(res["flags"])
        stats["aborted"] += res["status"] == "aborted"

    # lab sheet -> sample + measurement
    samples = lab[["sample_id", "run_id", "substrate", "position"]].drop_duplicates()
    con.executemany("INSERT INTO sample VALUES (?,?,?,?)", samples.itertuples(index=False))
    con.executemany("INSERT INTO measurement (sample_id, quantity, value, unit, method, measured_on) "
                    "VALUES (?,?,?,?,?,?)",
                    lab[["sample_id", "quantity", "value", "unit", "method", "measured_on"]].itertuples(index=False))

    # target age comes from the database itself (window function in v_target_age)
    con.execute("""UPDATE run_features SET target_age_kwh =
                   (SELECT ROUND(target_age_kwh, 1) FROM v_target_age v WHERE v.run_id = run_features.run_id)""")
    con.commit()
    con.close()
    if verbose:
        print(f"  {stats['files']} Rohdateien, {stats['rows_read']:,} Zeilen, {stats['flags']} Qualitätsflags")
    return stats
