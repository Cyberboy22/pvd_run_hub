"""Synthetic PVD plant logs and lab measurements.

EVERYTHING IN THIS MODULE IS SIMULATED.
The plants, recipes and response functions are plausible-looking toy models
chosen so the data pipeline has something realistic to work on. They are not
validated physical models of any real coating process or of any real plant.

What gets simulated
-------------------
* three fictional plants (MSIP, AIP, HS-PVD), each writing its machine log in a
  *different* export format (delimiter, locale, units, time stamps, sentinels)
* typical data problems: sensor dropouts, duplicated rows, rows out of order,
  a frozen temperature sensor, a mass-flow-controller fault, arc events and an
  aborted run
* target wear: the cathode voltage drifts and the hardness slowly drops as the
  target set accumulates energy, until the target set is replaced
* lab measurements done days after a run (thickness, hardness, Young's modulus,
  adhesion class) on 3 samples per run; the newest runs are not measured yet
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

LOGGER_DT_S = 10  # the (simulated) plant loggers write one row every 10 s
BERLIN = ZoneInfo("Europe/Berlin")

# --------------------------------------------------------------------------
# Plants, recipes, ground truth
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Plant:
    plant_id: str
    name: str
    process: str          # MSIP | AIP | HS-PVD
    log_format: str       # which parser handles its export
    total_flow_sccm: float
    pa_per_sccm: float    # crude pressure model: p = flow * pa_per_sccm
    rate_um_h_per_kw: float
    cathode_v0: float     # cathode / discharge voltage of a fresh target set
    drift_v_per_kwh: float
    target_life_kwh: float
    target_material: str


PLANTS: dict[str, Plant] = {
    p.plant_id: p
    for p in [
        Plant("MSIP-1", "Magnetronsputteranlage (dcMS/HPPMS)", "MSIP", "msip_csv_de",
              400.0, 0.00115, 0.092, 520.0, -0.066, 900.0, "Cr / CrAl"),
        Plant("AIP-1", "Arc-Ion-Plating-Anlage", "AIP", "aip_csv_utc",
              320.0, 0.0095, 0.62, 23.5, -0.020, 75.0, "TiAl"),
        Plant("HS-1", "High-Speed-PVD-Anlage (Hohlkathode)", "HS-PVD", "hs_tsv_rel",
              900.0, 0.045, 0.48, 410.0, -0.060, 400.0, "CrAl"),
    ]
}

# hardness of each coating system at "neutral" process conditions [GPa]
BASE_HARDNESS = {("MSIP-1", "CrN"): 16.5, ("MSIP-1", "CrAlN"): 24.5,
                 ("AIP-1", "TiAlN"): 27.0, ("HS-1", "CrAlN"): 22.5}
# nominal power and span per plant, to normalise the power effect
POWER_NOM = {"MSIP-1": (14.0, 5.0), "AIP-1": (6.5, 2.0), "HS-1": (15.0, 4.0)}

# design space of the MSIP CrAlN study (also used by the active-learning adapter)
CRALN_SPACE = {
    "bias_abs_v": (50.0, 150.0),
    "n2_ratio": (0.25, 0.60),
    "temp_c": (320.0, 480.0),
    "power_kw": (9.0, 19.0),
}


def true_hardness(plant_id: str, coating: str, bias_abs_v: float, n2_ratio: float,
                  temp_c: float, power_kw: float, target_age_kwh: float,
                  arc_rate_per_h: float) -> float:
    """Noise-free toy response surface for indentation hardness [GPa].

    Shape (not physics!): an optimum at moderate ion bombardment (~95 V bias) and
    at a medium nitrogen share, a mild benefit of higher temperature, a mild
    penalty for high deposition power, a slow loss with target wear and a small
    penalty for arcing.
    """
    p_nom, p_span = POWER_NOM[plant_id]
    n2_opt = 0.40 if plant_id != "AIP-1" else 0.85
    h = BASE_HARDNESS[(plant_id, coating)]
    h += 5.0 * np.exp(-(((bias_abs_v - 95.0) / 45.0) ** 2)) - 1.5
    h += 3.0 * np.exp(-(((n2_ratio - n2_opt) / 0.13) ** 2)) - 1.0
    h += 0.012 * (temp_c - 400.0)
    h += -0.75 * (power_kw - p_nom) / p_span
    h += -2.4 * target_age_kwh / PLANTS[plant_id].target_life_kwh  # target wear
    h += -0.05 * arc_rate_per_h
    return float(h)


@dataclass
class Recipe:
    recipe_id: str
    coating: str
    bias_v: float        # negative, as written on the plant HMI
    power_kw: float
    n2_ratio: float      # N2 / (Ar + N2)
    temp_c: float
    coat_h: float
    etch_min: float
    etch_bias_v: float
    is_reference: bool = False
    total_flow_sccm: float = 0.0   # Ar + N2 during coating; filled from the plant


@dataclass
class RunPlan:
    run_id: str
    plant_id: str
    recipe: Recipe
    start_local: datetime
    target_id: str
    operator: str
    arc_rate_per_h: float
    defects: dict = field(default_factory=dict)
    # filled while simulating
    target_age_kwh: float = 0.0
    energy_kwh: float = 0.0
    status: str = "ok"


# --------------------------------------------------------------------------
# Campaign: which runs happen when
# --------------------------------------------------------------------------

OPERATORS = ["MK", "JS", "LW", "TB"]


def _lhs(n: int, dims: int, rng: np.random.Generator) -> np.ndarray:
    """Latin hypercube in [0,1]^dims (what a DoE-minded lab would plan)."""
    cut = (np.arange(n)[:, None] + rng.random((n, dims))) / n
    for d in range(dims):
        cut[:, d] = cut[rng.permutation(n), d]
    return cut


def plan_campaign(seed: int = 7) -> list[RunPlan]:
    rng = np.random.default_rng(seed)
    plans: list[RunPlan] = []

    # ---- MSIP-1: CrN reference runs + CrAlN design-of-experiments study ----
    lo = np.array([v[0] for v in CRALN_SPACE.values()])
    hi = np.array([v[1] for v in CRALN_SPACE.values()])
    doe = lo + _lhs(32, 4, rng) * (hi - lo)
    msip_recipes: list[Recipe] = []
    ref_slots = set(np.linspace(0, 43, 12).round().astype(int))  # a reference run every ~4th run
    doe_iter = iter(range(32))
    for i in range(44):
        if i in ref_slots:
            msip_recipes.append(Recipe("CrN_REF", "CrN", -80.0, 12.0, 0.45, 400.0, 3.0, 30.0, -500.0, True))
        else:
            k = next(doe_iter)
            b, r, t, p = doe[k]
            msip_recipes.append(Recipe(f"CrAlN_DoE_{k + 1:02d}", "CrAlN", -round(b), round(p, 1),
                                       round(r, 3), round(t), round(rng.uniform(2.0, 3.5), 2),
                                       round(rng.uniform(16, 34)), -round(rng.uniform(400, 600)), False))

    # ---- AIP-1: TiAlN, 6 reference runs + 12 varied ----
    aip_recipes = []
    for i in range(18):
        if i % 3 == 0:
            aip_recipes.append(Recipe("TiAlN_REF", "TiAlN", -60.0, 6.5, 0.88, 450.0, 1.2, 25.0, -800.0, True))
        else:
            aip_recipes.append(Recipe(f"TiAlN_V{i:02d}", "TiAlN", -round(rng.uniform(40, 140)),
                                      round(rng.uniform(4.5, 8.5), 1), round(rng.uniform(0.75, 0.95), 3),
                                      round(rng.uniform(380, 520)), round(rng.uniform(0.9, 1.6), 2),
                                      round(rng.uniform(18, 30)), -800.0, False))

    # ---- HS-1: CrAlN high rate, 10 varied runs ----
    hs_recipes = [Recipe(f"CrAlN_HS_{i + 1:02d}", "CrAlN", -round(rng.uniform(50, 130)),
                         round(rng.uniform(11, 19), 1), round(rng.uniform(0.28, 0.55), 3),
                         round(rng.uniform(330, 470)), round(rng.uniform(0.6, 1.0), 2),
                         round(rng.uniform(18, 30)), -400.0, False) for i in range(10)]

    def dates(n: int, first: str, last: str) -> list[datetime]:
        d0 = datetime.fromisoformat(first)
        span = (datetime.fromisoformat(last) - d0).days
        days = np.sort(rng.choice(np.arange(span + 1), size=n, replace=False))
        out, used = [], set()
        for d in days:
            day = d0 + timedelta(days=int(d))
            # one run per plant and day, never on a weekend
            while day.weekday() >= 5 or day.date() in used:
                day += timedelta(days=1)
            used.add(day.date())
            out.append(day.replace(hour=int(rng.integers(7, 10)), minute=int(rng.choice([0, 15, 30, 45]))))
        return sorted(out)

    for plant_id, recipes, first, last, prefix in [
        ("MSIP-1", msip_recipes, "2026-03-02", "2026-11-19", "M"),
        ("AIP-1", aip_recipes, "2026-03-09", "2026-11-17", "A"),
        ("HS-1", hs_recipes, "2026-05-04", "2026-11-12", "H"),
    ]:
        for i, (rec, start) in enumerate(zip(recipes, dates(len(recipes), first, last))):
            arc = float(rng.gamma(2.0, 1.6))
            if rng.random() < 0.10:
                arc = float(rng.uniform(14, 24))  # contaminated / instable run
            plans.append(RunPlan(
                run_id=f"{prefix}{start:%y%m%d}-{i + 1:02d}", plant_id=plant_id, recipe=rec,
                start_local=start, target_id="", operator=str(rng.choice(OPERATORS)),
                arc_rate_per_h=round(arc, 2)))

    plans.sort(key=lambda p: p.start_local)
    for p in plans:
        p.recipe.total_flow_sccm = PLANTS[p.plant_id].total_flow_sccm

    # ---- injected data problems (ground truth kept for the tests) ----
    for p in plans:
        d = p.defects
        if rng.random() < 0.20:
            d["gap"] = {"signal": str(rng.choice(["temp_c", "pressure_pa", "bias_v", "n2_sccm"])),
                        "minutes": int(rng.integers(3, 13))}
        if p.plant_id == "MSIP-1" and rng.random() < 0.35:
            d["duplicates"] = int(rng.integers(3, 9))
        if p.plant_id == "AIP-1" and rng.random() < 0.30:
            d["out_of_order"] = int(rng.integers(2, 6))
        if p.plant_id == "HS-1" and rng.random() < 0.35:
            d["stuck_temp_min"] = int(rng.integers(15, 26))
    msip_doe = [p for p in plans if p.plant_id == "MSIP-1" and not p.recipe.is_reference]
    msip_doe[9].defects["mfc_fault"] = 0.78     # N2 flow only 78 % of setpoint
    msip_doe[21].defects["mfc_fault"] = 0.82
    [p for p in plans if p.plant_id == "AIP-1" and not p.recipe.is_reference][5].defects["mfc_fault"] = 0.85
    msip_doe[15].defects["abort_at"] = 0.35     # power supply trip after 35 % of coating time
    return plans


# --------------------------------------------------------------------------
# Time series of one run (in canonical units)
# --------------------------------------------------------------------------


def simulate_signals(plan: RunPlan, rng: np.random.Generator) -> tuple[pd.DataFrame, float]:
    """Return the true machine signals (canonical units) and the mean N2 ratio."""
    plant = PLANTS[plan.plant_id]
    r = plan.recipe
    heat_min = float(rng.uniform(70, 95))
    etch_min = r.etch_min
    coat_min = r.coat_h * 60.0
    if "abort_at" in plan.defects:
        coat_min *= plan.defects["abort_at"]
        plan.status = "aborted"
    cool_min = float(rng.uniform(50, 70))
    edges = np.cumsum([0, heat_min, etch_min, coat_min, cool_min]) * 60.0
    t = np.arange(0, edges[-1], LOGGER_DT_S, dtype=float)
    n = len(t)
    phase = np.searchsorted(edges, t, side="right") - 1  # 0 heat, 1 etch, 2 coat, 3 cool

    heat, etch, coat, cool = (phase == k for k in range(4))
    noise = lambda s: rng.normal(0.0, s, n)  # noqa: E731

    total = plant.total_flow_sccm
    n2_set = r.n2_ratio * total
    ar = np.where(etch, 0.5 * total, 0.0) + np.where(coat, total - n2_set, 0.0)
    n2 = np.where(coat, n2_set, 0.0)
    if "mfc_fault" in plan.defects:  # fault in the second half of the coating step
        coat_idx = np.flatnonzero(coat)
        bad = coat_idx[len(coat_idx) // 2:]
        n2[bad] *= plan.defects["mfc_fault"]
    ar = np.clip(ar * (1 + noise(0.006)), 0, None)
    n2 = np.clip(n2 * (1 + noise(0.008)), 0, None)

    base_p = 0.002 + 0.03 * np.exp(-t / 1500.0)  # pump-down tail
    pressure = base_p + (ar + n2) * plant.pa_per_sccm * (1 + noise(0.01))

    bias = np.where(etch, r.etch_bias_v, 0.0) + np.where(coat, r.bias_v, 0.0)
    bias = bias + np.where(etch | coat, noise(1.2), 0.0)

    etch_power = {"MSIP-1": 2.0, "AIP-1": 3.0, "HS-1": 3.0}[plan.plant_id]
    power = np.where(etch, etch_power, 0.0) + np.where(coat, r.power_kw, 0.0)
    power = power * (1 + noise(0.008))

    ratio = n2 / np.maximum(ar + n2, 1e-9)
    cv = plant.cathode_v0 + plant.drift_v_per_kwh * plan.target_age_kwh
    if plan.plant_id != "AIP-1":
        cv += 8.0 * (r.power_kw - POWER_NOM[plan.plant_id][0]) / POWER_NOM[plan.plant_id][1]
        cv_t = cv - 40.0 * (ratio - 0.4)
    else:
        cv_t = cv + 2.0 * (ratio - 0.85)
    cathode_v = np.where(coat, cv_t, 0.0) + np.where(etch, cv * (1.15 if plan.plant_id != "AIP-1" else 1.0), 0.0)
    cathode_v = np.where(etch | coat, cathode_v + noise(1.5 if plan.plant_id != "AIP-1" else 0.15), 0.0)

    # temperature: heat up, hold (+ a bit of plasma heating), cool down
    t_heat_end = edges[1]
    temp = np.empty(n)
    tau = 20 * 60.0
    temp[heat] = r.temp_c - (r.temp_c - 22.0) * np.exp(-t[heat] / tau)
    hold = etch | coat
    temp[hold] = r.temp_c + 6.0 * (1 - np.exp(-(t[hold] - t_heat_end) / 1800.0))
    t_cool0 = edges[3]
    t_at_cool = r.temp_c + 6.0
    temp[cool] = 60.0 + (t_at_cool - 60.0) * np.exp(-(t[cool] - t_cool0) / (25 * 60.0))
    temp = temp + noise(1.0)

    # arcs: short voltage collapses during coating
    n_arcs = rng.poisson(plan.arc_rate_per_h * coat_min / 60.0)
    coat_idx = np.flatnonzero(coat)
    if n_arcs and len(coat_idx):
        hits = rng.choice(coat_idx, size=min(n_arcs, len(coat_idx)), replace=False)
        bias[hits] *= rng.uniform(0.05, 0.35, len(hits))
        power[hits] *= rng.uniform(0.3, 0.7, len(hits))

    df = pd.DataFrame({
        "t_s": t, "bias_v": bias, "power_kw": np.clip(power, 0, None), "cathode_v": cathode_v,
        "pressure_pa": pressure, "ar_sccm": ar, "n2_sccm": n2, "temp_c": temp,
    })
    plan.energy_kwh = float(r.power_kw * coat_min / 60.0 + etch_power * etch_min / 60.0)
    coat_ratio = float(np.mean(ratio[coat])) if coat.any() else float("nan")
    return df, coat_ratio


# --------------------------------------------------------------------------
# Writing raw files in three different "vendor" formats
# --------------------------------------------------------------------------


def _apply_gap(df: pd.DataFrame, plan: RunPlan, rng: np.random.Generator) -> pd.DataFrame:
    g = plan.defects.get("gap")
    if not g:
        return df
    n = int(g["minutes"] * 60 / LOGGER_DT_S)
    start = int(rng.integers(len(df) // 5, len(df) - n - len(df) // 5))
    df.loc[df.index[start:start + n], g["signal"]] = np.nan
    g["start_s"] = float(df["t_s"].iloc[start])
    return df


def write_msip(df: pd.DataFrame, plan: RunPlan, path: Path, rng: np.random.Generator) -> None:
    """German HMI export: ';' separated, decimal comma, dd.mm.yyyy, mPa, blank = missing."""
    ts = [plan.start_local + timedelta(seconds=s) for s in df["t_s"]]
    out = pd.DataFrame({
        "Zeit": [f"{x:%d.%m.%Y %H:%M:%S}" for x in ts],
        "Bias [V]": df["bias_v"].round(1),
        "Leistung [kW]": df["power_kw"].round(2),
        "Kathodenspannung [V]": df["cathode_v"].round(1),
        "Druck [mPa]": (df["pressure_pa"] * 1000).round(1),
        "Ar [sccm]": df["ar_sccm"].round(1),
        "N2 [sccm]": df["n2_sccm"].round(1),
        "Temperatur [°C]": df["temp_c"].round(1),
    })
    if plan.defects.get("duplicates"):
        idx = rng.choice(out.index[10:-10], size=plan.defects["duplicates"], replace=False)
        out = pd.concat([out, out.loc[idx]]).sort_index(kind="stable")
    body = out.to_csv(sep=";", index=False, decimal=",", na_rep="")
    header = (f"# Anlage: {plan.plant_id}\n# Charge: {plan.run_id}\n# Rezept: {plan.recipe.recipe_id}\n"
              f"# Bediener: {plan.operator}\n# Export: {ts[-1] + timedelta(minutes=3):%d.%m.%Y %H:%M:%S}\n")
    path.write_text(header + body, encoding="utf-8")


def write_aip(df: pd.DataFrame, plan: RunPlan, path: Path, rng: np.random.Generator) -> None:
    """English controller export: ',' separated, ISO-8601 UTC, Pa, arc current/voltage, 'nan'."""
    start_utc = plan.start_local.replace(tzinfo=BERLIN).astimezone(timezone.utc)
    ts = [start_utc + timedelta(seconds=s) for s in df["t_s"]]
    arc_v = df["cathode_v"].where(df["cathode_v"] > 0, 0.0)
    arc_a = np.where(arc_v > 0, df["power_kw"] * 1000 / arc_v.replace(0, np.nan), 0.0)
    out = pd.DataFrame({
        "timestamp": [x.strftime("%Y-%m-%dT%H:%M:%SZ") for x in ts],
        "bias_voltage_V": df["bias_v"].round(1),
        "arc_current_A": np.round(np.nan_to_num(arc_a), 1),
        "arc_voltage_V": arc_v.round(2),
        "chamber_pressure_Pa": df["pressure_pa"].round(4),
        "Ar_flow_sccm": df["ar_sccm"].round(1),
        "N2_flow_sccm": df["n2_sccm"].round(1),
        "substrate_temp_C": df["temp_c"].round(1),
    })
    if plan.defects.get("out_of_order"):
        k = plan.defects["out_of_order"]
        order = np.arange(len(out))
        for i in rng.choice(np.arange(20, len(out) - 20, 3), size=k, replace=False):
            order[i], order[i + 1] = order[i + 1], order[i]
        out = out.iloc[order].reset_index(drop=True)
    path.write_text(out.to_csv(index=False, na_rep="nan"), encoding="utf-8")


def write_hs(df: pd.DataFrame, plan: RunPlan, path: Path, rng: np.random.Generator) -> None:
    """Lab-built logger: tab separated, relative seconds, W, mbar, Kelvin, -9999 = missing."""
    out = pd.DataFrame({
        "t_rel[s]": df["t_s"].astype(int),
        "U_bias[V]": df["bias_v"].round(1),
        "P_kat[W]": (df["power_kw"] * 1000).round(0),
        "U_kat[V]": df["cathode_v"].round(1),
        "p[mbar]": (df["pressure_pa"] / 100).round(6),
        "Q_Ar[sccm]": df["ar_sccm"].round(1),
        "Q_N2[sccm]": df["n2_sccm"].round(1),
        "T[K]": (df["temp_c"] + 273.15).round(2),
    })
    if plan.defects.get("stuck_temp_min"):
        n = int(plan.defects["stuck_temp_min"] * 60 / LOGGER_DT_S)
        coat_rows = np.flatnonzero(df["n2_sccm"].to_numpy() > 1)
        if len(coat_rows) > n + 10:
            s = int(coat_rows[len(coat_rows) // 3])
            out.loc[s:s + n, "T[K]"] = out.loc[s, "T[K]"]
            plan.defects["stuck_temp_start_s"] = float(df["t_s"].iloc[s])
    out = out.fillna(-9999)
    header = (f"START={plan.start_local:%Y-%m-%d %H:%M:%S}\nRUN={plan.run_id}\n"
              f"RECIPE={plan.recipe.recipe_id}\nOP={plan.operator}\n")
    path.write_text(header + out.to_csv(sep="\t", index=False), encoding="utf-8")


WRITERS = {"msip_csv_de": (write_msip, ".csv"), "aip_csv_utc": (write_aip, ".csv"),
           "hs_tsv_rel": (write_hs, ".tsv")}


# --------------------------------------------------------------------------
# Lab measurements
# --------------------------------------------------------------------------

POSITIONS = ["top", "middle", "bottom"]
SUBSTRATES = {"MSIP-1": "1.3343 HSS", "AIP-1": "WC-Co K10", "HS-1": "1.2379"}


def simulate_measurements(plan: RunPlan, coat_ratio: float, rng: np.random.Generator,
                          today: datetime) -> list[dict]:
    if plan.status != "ok":
        return []
    measured_on = plan.start_local + timedelta(days=int(rng.integers(3, 10)))
    if measured_on.date() > today.date():
        return []  # lab has not measured this run yet
    r, plant = plan.recipe, PLANTS[plan.plant_id]
    h_true = true_hardness(plan.plant_id, r.coating, abs(r.bias_v), coat_ratio, r.temp_c + 4,
                           r.power_kw, plan.target_age_kwh, plan.arc_rate_per_h)
    rate = plant.rate_um_h_per_kw * r.power_kw * (1 - 0.00012 * plan.target_age_kwh)
    adhesion_score = (1.2 + 0.09 * plan.arc_rate_per_h + max(0.0, 22 - r.etch_min) * 0.12
                      + max(0.0, 450 - abs(r.etch_bias_v)) * 0.004)
    rows = []
    for pos, f_pos in zip(POSITIONS, [0.93, 1.0, 0.95]):
        sid = f"{plan.run_id}-{pos[0].upper()}"
        h = h_true + rng.normal(0, 0.55)
        rows += [{**row, "substrate": SUBSTRATES[plan.plant_id]} for row in [
            dict(sample_id=sid, position=pos, quantity="thickness", value=round(rate * r.coat_h * f_pos * rng.normal(1, 0.03), 2),
                 unit="µm", method="Kalottenschliff", measured_on=measured_on.date().isoformat()),
            dict(sample_id=sid, position=pos, quantity="hardness", value=round(h, 2),
                 unit="GPa", method="Nanoindentation", measured_on=measured_on.date().isoformat()),
            dict(sample_id=sid, position=pos, quantity="youngs_modulus", value=round(12.5 * h + 60 + rng.normal(0, 12), 1),
                 unit="GPa", method="Nanoindentation", measured_on=measured_on.date().isoformat()),
            dict(sample_id=sid, position=pos, quantity="adhesion_class",
                 value=float(int(np.clip(round(adhesion_score + rng.normal(0, 0.45)), 1, 6))),
                 unit="HF", method="Rockwell C (VDI 3198)", measured_on=measured_on.date().isoformat()),
        ]]
    return rows


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


def simulate(out_dir: Path, seed: int = 7, today: str = "2026-11-24") -> dict:
    """Write raw logs + lab sheet + metadata into `out_dir`. Returns the manifest."""
    rng = np.random.default_rng(seed + 1)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    plans = plan_campaign(seed)
    today_dt = datetime.fromisoformat(today)

    target_state: dict[str, dict] = {}
    targets: list[dict] = []
    lab_rows: list[dict] = []
    manifest_runs = []
    for plan in plans:
        plant = PLANTS[plan.plant_id]
        st = target_state.get(plan.plant_id)
        if st is None or st["age"] >= plant.target_life_kwh:
            if st is not None:
                targets[st["index"]]["removed_on"] = plan.start_local.date().isoformat()
            letter = chr(ord("A") + sum(t["plant_id"] == plan.plant_id for t in targets))
            targets.append(dict(target_id=f"TS-{plan.plant_id}-{letter}", plant_id=plan.plant_id,
                                material=plant.target_material, installed_on=plan.start_local.date().isoformat(),
                                removed_on=None))
            st = target_state[plan.plant_id] = {"age": 0.0, "index": len(targets) - 1}
        plan.target_id = targets[st["index"]]["target_id"]
        plan.target_age_kwh = st["age"]

        df, coat_ratio = simulate_signals(plan, rng)
        st["age"] += plan.energy_kwh
        df = _apply_gap(df, plan, rng)
        writer, ext = WRITERS[plant.log_format]
        path = raw_dir / f"{plan.run_id}{ext}"
        writer(df, plan, path, rng)

        for m in simulate_measurements(plan, coat_ratio, rng, today_dt):
            lab_rows.append({"run_id": plan.run_id, **m})
        manifest_runs.append({
            "run_id": plan.run_id, "plant_id": plan.plant_id, "raw_file": f"raw/{path.name}",
            "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "recipe": asdict(plan.recipe), "started_at": plan.start_local.isoformat(timespec="minutes"),
            "operator": plan.operator, "target_id": plan.target_id,
            # ground truth for tests only -- the pipeline never reads these keys
            "_truth": {"defects": plan.defects, "status": plan.status, "arc_rate_per_h": plan.arc_rate_per_h,
                       "target_age_kwh": round(plan.target_age_kwh, 2), "coat_ratio": coat_ratio},
        })

    # what a lab keeps next to the machine logs: lab sheet, run book, recipe list
    pd.DataFrame(lab_rows).to_csv(out_dir / "lab_measurements.csv", index=False)
    pd.DataFrame([{"run_id": p.run_id, "plant_id": p.plant_id, "recipe_id": p.recipe.recipe_id,
                   "target_id": p.target_id, "operator": p.operator} for p in plans]
                 ).to_csv(out_dir / "run_sheet.csv", index=False)
    recipes = {p.recipe.recipe_id: asdict(p.recipe) for p in plans}
    pd.DataFrame(list(recipes.values())).to_csv(out_dir / "recipes.csv", index=False)
    pd.DataFrame(targets).to_csv(out_dir / "targets.csv", index=False)
    pd.DataFrame([{"plant_id": p.plant_id, "name": p.name, "process": p.process, "log_format": p.log_format}
                  for p in PLANTS.values()]).to_csv(out_dir / "plants.csv", index=False)
    manifest = {"generated_for": today, "seed": seed, "logger_dt_s": LOGGER_DT_S,
                "plants": [asdict(p) for p in PLANTS.values()], "targets": targets, "runs": manifest_runs,
                "note": "SIMULIERTE DATEN - keine echten Anlagen- oder Messdaten."}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False, default=str),
                                           encoding="utf-8")
    return manifest
