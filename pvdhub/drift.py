"""Golden-run comparison: is the plant still doing what it did with a fresh target?

Every few weeks the lab coats a *reference recipe* (same settings every time).
The first reference run on a new target set is the "golden run". Each later
reference run is compared against it:

    cathode voltage   -> changes as the target erodes
    deposition rate   -> thickness / coating time
    hardness          -> what the customer actually cares about

Thresholds (warning / alarm, in % deviation) are deliberately simple and
documented here so an engineer can change them in one place.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

THRESHOLDS = {"cathode_v": (4.0, 8.0), "rate_um_per_h": (5.0, 10.0), "hardness_gpa": (5.0, 10.0)}
METRIC_DE = {"cathode_v": "Kathodenspannung", "rate_um_per_h": "Beschichtungsrate", "hardness_gpa": "Härte"}

REFERENCE_RUNS_SQL = """
SELECT r.run_id, r.plant_id, r.target_id, r.started_at, f.target_age_kwh,
       f.cathode_v, p.hardness_gpa,
       p.thickness_um / f.coat_h AS rate_um_per_h
FROM coating_run r
JOIN recipe rc         ON rc.recipe_id = r.recipe_id AND rc.is_reference = 1
JOIN run_features f    ON f.run_id = r.run_id
LEFT JOIN v_run_properties p ON p.run_id = r.run_id
WHERE r.status = 'ok'
ORDER BY r.started_at
"""


def _status(dev: float, metric: str) -> str:
    warn, alarm = THRESHOLDS[metric]
    a = abs(dev)
    return "alarm" if a >= alarm else "warning" if a >= warn else "ok"


def run_drift_checks(con: sqlite3.Connection) -> pd.DataFrame:
    ref = pd.read_sql(REFERENCE_RUNS_SQL, con)
    rows = []
    for _, grp in ref.groupby(["plant_id", "target_id"], sort=False):
        golden = grp.iloc[0]
        for _, run in grp.iterrows():
            for metric in THRESHOLDS:
                v, g = run[metric], golden[metric]
                if pd.isna(v) or pd.isna(g):
                    continue
                dev = (v - g) / abs(g) * 100.0
                rows.append({"run_id": run["run_id"], "golden_run_id": golden["run_id"], "metric": metric,
                             "value": round(float(v), 3), "golden_value": round(float(g), 3),
                             "deviation_pct": round(float(dev), 2), "status": _status(dev, metric)})
    out = pd.DataFrame(rows)
    con.execute("DELETE FROM drift_check")
    out.to_sql("drift_check", con, if_exists="append", index=False)
    con.commit()
    return out


def forecast_alarm(con: sqlite3.Connection, plant_id: str, metric: str = "cathode_v") -> dict | None:
    """Linear trend of the deviation over target age -> at which age is the alarm limit reached?"""
    df = pd.read_sql("""
        SELECT d.deviation_pct, f.target_age_kwh, r.target_id
        FROM drift_check d JOIN coating_run r ON r.run_id = d.run_id
        JOIN run_features f ON f.run_id = d.run_id
        WHERE r.plant_id = ? AND d.metric = ?
        ORDER BY r.started_at""", con, params=(plant_id, metric))
    if df.empty:
        return None
    active = con.execute("SELECT target_id FROM coating_run WHERE plant_id = ? ORDER BY started_at DESC LIMIT 1",
                         (plant_id,)).fetchone()[0]
    cur = df[df["target_id"] == active]
    allp = df  # the slope is learned from all target sets of this plant
    slope, intercept = np.polyfit(allp["target_age_kwh"], allp["deviation_pct"], 1)
    alarm = THRESHOLDS[metric][1] * np.sign(slope) if slope != 0 else None
    age_now = con.execute("SELECT COALESCE(SUM(energy_kwh),0) FROM coating_run WHERE target_id = ?",
                          (active,)).fetchone()[0]
    mean_run_kwh = con.execute("SELECT AVG(energy_kwh) FROM coating_run WHERE plant_id = ?",
                               (plant_id,)).fetchone()[0]
    age_alarm = (alarm - intercept) / slope if alarm is not None else None
    return {"plant_id": plant_id, "metric": metric, "active_target": active,
            "slope_pct_per_100kwh": round(float(slope * 100), 3),
            "age_now_kwh": round(float(age_now), 1),
            "age_alarm_kwh": round(float(age_alarm), 1) if age_alarm is not None else None,
            "runs_left": (int(max(0, (age_alarm - age_now) // mean_run_kwh)) if age_alarm is not None else None),
            "n_points_active": int(len(cur))}
