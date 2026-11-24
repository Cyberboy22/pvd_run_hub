"""Write what the web dashboard needs: the SQLite file + one small JSON summary."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pandas as pd

from . import __version__
from .drift import METRIC_DE, THRESHOLDS, forecast_alarm

SQL_EXAMPLES = [
    {"title": "Härte je Anlage und Schichtsystem",
     "sql": """SELECT plant_id, coating,
       COUNT(*)                     AS chargen,
       ROUND(AVG(hardness_gpa), 1)  AS haerte_mittel_gpa,
       ROUND(MIN(hardness_gpa), 1)  AS min_gpa,
       ROUND(MAX(hardness_gpa), 1)  AS max_gpa
FROM training_data
GROUP BY plant_id, coating
ORDER BY haerte_mittel_gpa DESC;"""},
    {"title": "Targetalter per Window-Funktion",
     "sql": """-- Energie, die das Target VOR jeder Charge gesehen hat
SELECT run_id, target_id, started_at,
       ROUND(energy_kwh, 1) AS energie_kwh,
       ROUND(SUM(energy_kwh) OVER (
           PARTITION BY target_id ORDER BY started_at
           ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 1) AS targetalter_kwh
FROM coating_run
WHERE plant_id = 'MSIP-1'
ORDER BY started_at;"""},
    {"title": "Chargen mit Warnungen",
     "sql": """SELECT r.run_id, r.plant_id, q.severity, q.message
FROM quality_flag q
JOIN coating_run r USING (run_id)
WHERE q.severity IN ('warning', 'critical')
ORDER BY r.started_at DESC;"""},
    {"title": "Trainingstabelle (ML-ready)",
     "sql": """SELECT run_id, coating, bias_v, n2_ratio, temp_c, power_kw,
       target_age_kwh, hardness_gpa, youngs_modulus_gpa, rate_um_per_h
FROM training_data
WHERE plant_id = 'MSIP-1'
ORDER BY started_at
LIMIT 25;"""},
    {"title": "Noch nicht gemessene Chargen",
     "sql": """SELECT r.run_id, r.plant_id, r.started_at, r.status
FROM coating_run r
LEFT JOIN sample s ON s.run_id = r.run_id
WHERE s.sample_id IS NULL;"""},
    {"title": "Rückverfolgbarkeit: Messwert → Rohdatei",
     "sql": """-- Woher kommt dieser Härtewert? Probe → Charge → Rezept → Rohdatei (+ Prüfsumme)
SELECT m.value AS haerte_gpa, s.sample_id, s.position,
       r.run_id, r.recipe_id, r.raw_file, SUBSTR(r.raw_sha256, 1, 12) AS sha256
FROM measurement m
JOIN sample s      ON s.sample_id = m.sample_id
JOIN coating_run r ON r.run_id = s.run_id
WHERE m.quantity = 'hardness'
ORDER BY m.value DESC
LIMIT 10;"""},
]


def _np(o):
    """json fallback for numpy scalars."""
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(type(o))


def records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> list of dicts with None instead of NaN (valid JSON)."""
    return df.astype(object).where(df.notna(), None).to_dict("records")


def raw_preview(path: Path, n: int = 9) -> str:
    with path.open(encoding="utf-8") as fh:
        return "".join(next(fh, "") for _ in range(n))


def export_dashboard(sim_dir: Path, db_path: Path, model_result: dict, stats: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)

    runs = pd.read_sql("""
        SELECT r.run_id, r.plant_id, r.recipe_id, rc.coating, rc.is_reference, r.started_at, r.status,
               r.target_id, r.operator, r.raw_file, f.target_age_kwh,
               ROUND(p.hardness_gpa, 2) AS hardness_gpa, ROUND(p.thickness_um, 2) AS thickness_um,
               p.adhesion_class_worst,
               SUM(q.severity = 'info') AS n_info, SUM(q.severity = 'warning') AS n_warning,
               SUM(q.severity = 'critical') AS n_critical
        FROM coating_run r JOIN recipe rc USING (recipe_id) JOIN run_features f USING (run_id)
        LEFT JOIN v_run_properties p USING (run_id)
        LEFT JOIN quality_flag q USING (run_id)
        GROUP BY r.run_id ORDER BY r.started_at""", con)
    runs[["n_info", "n_warning", "n_critical"]] = runs[["n_info", "n_warning", "n_critical"]].fillna(0).astype(int)

    drift = pd.read_sql("""
        SELECT d.*, r.plant_id, r.target_id, r.started_at, f.target_age_kwh
        FROM drift_check d JOIN coating_run r USING (run_id) JOIN run_features f USING (run_id)
        ORDER BY r.started_at""", con)

    flags = pd.read_sql("SELECT severity, COUNT(*) AS n FROM quality_flag GROUP BY severity", con)
    n_train = con.execute("SELECT COUNT(*) FROM training_data").fetchone()[0]
    n_signal = con.execute("SELECT COUNT(*) FROM process_signal").fetchone()[0]
    n_meas = con.execute("SELECT COUNT(*) FROM measurement").fetchone()[0]
    plants = records(pd.read_sql("SELECT * FROM plant", con))
    targets = records(pd.read_sql("SELECT * FROM target_set ORDER BY installed_on", con))
    tables = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ["plant", "target_set", "recipe", "coating_run", "run_phase", "process_signal",
                        "quality_flag", "sample", "measurement", "run_features", "drift_check"]}
    # hero: the latest clean CrAlN run on the MSIP plant
    hero_id = runs[(runs.plant_id == "MSIP-1") & (runs.coating == "CrAlN") & (runs.status == "ok")
                   & (runs.n_warning == 0) & (runs.n_critical == 0)].iloc[-1]["run_id"]
    sig = pd.read_sql("SELECT t_s, temp_c, bias_v, power_kw, n2_sccm FROM process_signal WHERE run_id = ? "
                      "ORDER BY t_s", con, params=(hero_id,))
    hero = {"run_id": hero_id,
            "recipe_id": runs.set_index("run_id").loc[hero_id, "recipe_id"],
            "signals": {c: [None if pd.isna(v) else v for v in sig[c].tolist()] for c in sig.columns},
            "phases": pd.read_sql("SELECT phase, start_s, end_s FROM run_phase WHERE run_id = ? ORDER BY start_s", con,
                                  params=(hero_id,)).to_dict("records")}
    con2 = sqlite3.connect(db_path)
    forecasts = {p: forecast_alarm(con2, p) for p in ("MSIP-1", "AIP-1")}
    con.close()
    con2.close()

    data = {
        "meta": {"version": __version__, "generated_for": "2026-11-24",
                 "note": "Alle Daten sind simuliert – keine echten Anlagen- oder Messdaten."},
        "kpis": {
            "runs": int(len(runs)), "plants": len(plants), "rows_read": stats["rows_read"],
            "values_cleaned": stats["values_cleaned"], "signal_rows_db": n_signal, "measurements": n_meas,
            "flags": {r["severity"]: int(r["n"]) for r in flags.to_dict("records")},
            "runs_with_warning": int(((runs["n_warning"] + runs["n_critical"]) > 0).sum()),
            "training_rows": int(n_train), "duplicates": stats["duplicates"], "out_of_order": stats["out_of_order"],
            "sentinels": stats["sentinels"], "aborted": stats["aborted"],
            "pending": int(runs["hardness_gpa"].isna().sum() - stats["aborted"]),
        },
        "tables": tables,
        "plants": plants, "targets": targets,
        "runs": records(runs),
        "raw_preview": {r: raw_preview(sim_dir / f) for r, f in zip(runs["run_id"], runs["raw_file"])},
        "drift": {"rows": records(drift),
                  "thresholds": THRESHOLDS, "labels": METRIC_DE, "forecast": forecasts},
        "model": model_result,
        "sql_examples": SQL_EXAMPLES,
        "hero": hero,
    }
    (out_dir / "dashboard.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=_np),
                                           encoding="utf-8")
    shutil.copy(db_path, out_dir / "pvd_run_hub.sqlite")
    return out_dir / "dashboard.json"
