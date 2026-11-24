"""A first data-driven model on top of the database: Gaussian-process regression.

Why a Gaussian process?
* coating studies have tens of runs, not thousands -> GPs work well with little data
* it returns an uncertainty with every prediction -> the basis of active learning
  ("which run should we do next?")

The model predicts the mean indentation hardness of the MSIP CrAlN study from the
*measured* process values of the coating step plus the target age (both only
available because the pipeline linked logs, run sheet and lab sheet).

The exported parameters let the web dashboard evaluate the exact same model in
the browser (see `gp_predict`, which mirrors web/js/gp.js line by line).
"""

from __future__ import annotations

import sqlite3
import warnings

import numpy as np
import pandas as pd
from scipy.linalg import solve_triangular
from scipy.stats import norm
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel, WhiteKernel

FEATURES = ["bias_abs_v", "n2_ratio", "temp_c", "power_kw", "target_age_kwh"]
FEATURE_DE = {"bias_abs_v": "Bias-Spannung", "n2_ratio": "N₂-Anteil", "temp_c": "Temperatur",
              "power_kw": "Kathodenleistung", "target_age_kwh": "Targetalter"}
FEATURE_UNIT = {"bias_abs_v": "V", "n2_ratio": "", "temp_c": "°C", "power_kw": "kW", "target_age_kwh": "kWh"}
SETPOINT_FEATURES = ["bias_abs_v", "n2_ratio", "temp_c", "power_kw"]

STUDY_SQL = """
SELECT t.run_id, t.started_at, -t.bias_v AS bias_abs_v, t.n2_ratio, t.temp_c, t.power_kw,
       t.target_age_kwh, t.hardness_gpa,
       -rc.bias_v AS sp_bias_abs_v, rc.n2_ratio AS sp_n2_ratio, rc.temp_c AS sp_temp_c, rc.power_kw AS sp_power_kw
FROM training_data t JOIN recipe rc ON rc.recipe_id = t.recipe_id
WHERE t.plant_id = 'MSIP-1' AND t.coating = 'CrAlN'
ORDER BY t.started_at
"""


def load_study(con: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql(STUDY_SQL, con)


def _make_gp(dim: int, seed: int = 0) -> GaussianProcessRegressor:
    kernel = (ConstantKernel(1.0, (1e-2, 1e2)) * RBF(np.ones(dim), (5e-2, 1e2))
              + WhiteKernel(0.1, (1e-4, 1.0)))
    return GaussianProcessRegressor(kernel=kernel, normalize_y=True, n_restarts_optimizer=6, random_state=seed)


def fit(X: np.ndarray, y: np.ndarray, seed: int = 0) -> dict:
    """Fit on standardised inputs; return everything needed to predict anywhere."""
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    gp = _make_gp(X.shape[1], seed)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        gp.fit((X - mu) / sd, y)
    k = gp.kernel_
    return {
        "x_mean": mu.tolist(), "x_scale": sd.tolist(),
        "y_mean": float(gp._y_train_mean), "y_std": float(gp._y_train_std),
        "const": float(k.k1.k1.constant_value),
        "length_scales": np.atleast_1d(k.k1.k2.length_scale).astype(float).tolist(),
        "noise": float(k.k2.noise_level),
        "X_train": gp.X_train_.tolist(), "alpha": gp.alpha_.ravel().tolist(), "L": gp.L_.tolist(),
        "_sk": gp,
    }


def gp_predict(m: dict, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mean and *latent* std (model uncertainty, without measurement noise), in GPa."""
    Xs = (np.atleast_2d(X) - np.array(m["x_mean"])) / np.array(m["x_scale"])
    Xt, ls = np.array(m["X_train"]), np.array(m["length_scales"])
    d2 = (((Xs[:, None, :] - Xt[None, :, :]) / ls) ** 2).sum(-1)
    Ks = m["const"] * np.exp(-0.5 * d2)
    mean = Ks @ np.array(m["alpha"]) * m["y_std"] + m["y_mean"]
    v = solve_triangular(np.array(m["L"]), Ks.T, lower=True)
    var = np.clip(m["const"] - (v ** 2).sum(0), 1e-12, None) * m["y_std"] ** 2
    return mean, np.sqrt(var)


def leave_one_out(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    pred, std = np.zeros(len(y)), np.zeros(len(y))
    for i in range(len(y)):
        keep = np.arange(len(y)) != i
        m = fit(X[keep], y[keep])
        p, s = gp_predict(m, X[i:i + 1])
        pred[i], std[i] = p[0], s[0]
    return pred, std


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    ss_res = float(((y - p) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {"mae": round(float(np.abs(y - p).mean()), 3), "rmse": round(float(np.sqrt(ss_res / len(y))), 3),
            "r2": round(1 - ss_res / ss_tot, 3)}


def expected_improvement(mean: np.ndarray, std: np.ndarray, best: float, xi: float = 0.05) -> np.ndarray:
    z = (mean - best - xi) / std
    return (mean - best - xi) * norm.cdf(z) + std * norm.pdf(z)


def suggest_next(m: dict, bounds: dict, target_age_kwh: float, best: float, X_seen: np.ndarray,
                 n: int = 3, n_cand: int = 40000, seed: int = 1) -> list[dict]:
    """Top-n expected-improvement candidates that are not too close to each other or to past runs."""
    rng = np.random.default_rng(seed)
    lo = np.array([bounds[f][0] for f in SETPOINT_FEATURES])
    hi = np.array([bounds[f][1] for f in SETPOINT_FEATURES])
    cand4 = lo + rng.random((n_cand, 4)) * (hi - lo)
    cand = np.column_stack([cand4, np.full(n_cand, target_age_kwh)])
    mean, std = gp_predict(m, cand)
    ei = expected_improvement(mean, std, best)
    order = np.argsort(-ei)
    span = hi - lo
    seen4 = X_seen[:, :4]
    picked: list[int] = []
    for i in order:
        d_seen = np.min(np.linalg.norm((seen4 - cand4[i]) / span, axis=1))
        d_pick = min((np.linalg.norm((cand4[j] - cand4[i]) / span) for j in picked), default=9)
        if d_seen > 0.08 and d_pick > 0.25:
            picked.append(i)
        if len(picked) == n:
            break
    out = []
    for i in picked:
        out.append({**{f: round(float(v), 3 if f == "n2_ratio" else 1) for f, v in zip(FEATURES, cand[i])},
                    "pred_gpa": round(float(mean[i]), 2), "std_gpa": round(float(std[i]), 2),
                    "ei": round(float(ei[i]), 3)})
    return out


def train_and_evaluate(con: sqlite3.Connection) -> dict:
    df = load_study(con)
    X = df[FEATURES].to_numpy(float)
    y = df["hardness_gpa"].to_numpy(float)
    Xsp = df[["sp_" + f for f in SETPOINT_FEATURES]].to_numpy(float)

    loo_pred, loo_std = leave_one_out(X, y)
    loo_sp, _ = leave_one_out(Xsp, y)
    model = fit(X, y)

    bounds = {f: (float(df[f].min()), float(df[f].max())) for f in FEATURES}
    active = con.execute("""SELECT target_id FROM coating_run WHERE plant_id='MSIP-1'
                            ORDER BY started_at DESC LIMIT 1""").fetchone()[0]
    age_now = con.execute("SELECT COALESCE(SUM(energy_kwh),0) FROM coating_run WHERE target_id=?",
                          (active,)).fetchone()[0]
    # The drift monitor recommends a target change for MSIP-1, so the next runs are
    # planned for a fresh target set (age 0 kWh).
    suggestions = suggest_next(model, bounds, 0.0, float(y.max()), X)

    sk = model.pop("_sk")
    # self-check: our closed-form mean must equal scikit-learn's
    Xs = (X - np.array(model["x_mean"])) / np.array(model["x_scale"])
    assert np.allclose(sk.predict(Xs), gp_predict(model, X)[0], atol=1e-6)

    return {
        "features": FEATURES,
        "feature_labels": FEATURE_DE,
        "feature_units": FEATURE_UNIT,
        "n_runs": int(len(y)),
        "bounds": bounds,
        "model": model,
        "relevance": {f: round(1.0 / ls, 3) for f, ls in zip(FEATURES, model["length_scales"])},
        "loo": {
            "run_id": df["run_id"].tolist(), "measured": y.round(2).tolist(),
            "predicted": loo_pred.round(2).tolist(), "std": loo_std.round(2).tolist(),
            "metrics": metrics(y, loo_pred), "metrics_setpoints": metrics(y, loo_sp),
        },
        "best_run": {"run_id": df.loc[int(np.argmax(y)), "run_id"], "hardness_gpa": round(float(y.max()), 2)},
        "active_target": active, "target_age_now_kwh": round(float(age_now), 1),
        "suggestions": suggestions, "suggestions_target_age_kwh": 0.0,
    }
