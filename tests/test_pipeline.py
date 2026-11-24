"""End-to-end checks against the simulator's ground truth.

The simulator knows which defects it injected; the pipeline does not. These tests
check that the pipeline finds them without being told.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from pvdhub import drift, model
from pvdhub.db import build_database, process_run
from pvdhub.ingest import parse_raw_file
from pvdhub.simulate import simulate


@pytest.fixture(scope="session")
def campaign(tmp_path_factory):
    d = tmp_path_factory.mktemp("sim")
    manifest = simulate(d, seed=7)
    db = d / "hub.sqlite"
    build_database(d, db, verbose=False)
    return d, manifest, db


@pytest.fixture(scope="session")
def processed(campaign):
    d, manifest, _ = campaign
    return {r["run_id"]: (r, process_run(d / r["raw_file"], {**r["recipe"]})) for r in manifest["runs"]}


def test_three_formats_end_in_one_schema(campaign):
    d, manifest, _ = campaign
    seen = set()
    for r in manifest["runs"]:
        raw = parse_raw_file(d / r["raw_file"])
        seen.add(raw.report["format"])
        f = raw.frame
        assert list(f.columns) == ["t_s", "bias_v", "power_kw", "cathode_v", "pressure_pa", "ar_sccm", "n2_sccm", "temp_c"]
        assert f["t_s"].is_monotonic_increasing and f["t_s"].is_unique
        assert f["temp_c"].max() < 600, "temperature must be in °C, not K"
        assert f["pressure_pa"].max() < 100, "pressure must be in Pa"
        assert raw.meta["started_at"] == r["started_at"], "time zone / start time handling"
    assert seen == {"msip_csv_de", "aip_csv_utc", "hs_tsv_rel"}


def test_duplicates_and_row_order_are_repaired(campaign):
    d, manifest, _ = campaign
    for r in manifest["runs"]:
        rep = parse_raw_file(d / r["raw_file"]).report
        assert rep["duplicate_rows"] == r["_truth"]["defects"].get("duplicates", 0)
        if r["_truth"]["defects"].get("out_of_order"):
            assert rep["out_of_order_rows"] > 0


def test_phases_match_truth(processed):
    for run_id, (r, res) in processed.items():
        dur = {p["phase"]: (p["end_s"] - p["start_s"]) / 60 for p in res["phases"]}
        expect_coat = r["recipe"]["coat_h"] * 60 * r["_truth"]["defects"].get("abort_at", 1.0)
        assert abs(dur["etching"] - r["recipe"]["etch_min"]) <= 1.0, run_id
        assert abs(dur["coating"] - expect_coat) <= 1.5, run_id


def test_injected_defects_are_flagged(processed):
    for run_id, (r, res) in processed.items():
        truth = r["_truth"]["defects"]
        codes = {(f["code"], f["signal"]) for f in res["flags"]}
        if "gap" in truth:
            assert ("GAP", truth["gap"]["signal"]) in codes, run_id
        assert (("FROZEN_SENSOR", "temp_c") in codes) == ("stuck_temp_start_s" in truth), run_id
        if "mfc_fault" in truth:
            assert ("SETPOINT", "n2_sccm") in codes, run_id
        assert (res["status"] == "aborted") == ("abort_at" in truth), run_id


def test_no_setpoint_false_alarms(processed):
    for run_id, (r, res) in processed.items():
        if "mfc_fault" not in r["_truth"]["defects"]:
            assert not any(f["code"] == "SETPOINT" for f in res["flags"]), run_id


def test_arc_rate_tracks_truth(processed):
    est = [res["features"]["arc_rate_per_h"] for _, res in processed.values()]
    true = [r["_truth"]["arc_rate_per_h"] for r, _ in processed.values()]
    assert np.corrcoef(est, true)[0, 1] > 0.9


def test_measured_n2_ratio_sees_the_mfc_fault(processed):
    for run_id, (r, res) in processed.items():
        assert res["features"]["n2_ratio"] == pytest.approx(r["_truth"]["coat_ratio"], abs=0.01), run_id


def test_database_integrity(campaign):
    _, manifest, db = campaign
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert con.execute("SELECT COUNT(*) FROM coating_run").fetchone()[0] == len(manifest["runs"])
    # aborted runs never reach the training table
    assert con.execute("""SELECT COUNT(*) FROM training_data t JOIN coating_run r USING (run_id)
                          WHERE r.status <> 'ok'""").fetchone()[0] == 0
    # target age restarts at 0 for every new target set
    first = con.execute("""SELECT f.target_age_kwh FROM run_features f JOIN coating_run r USING (run_id)
                           WHERE r.started_at = (SELECT MIN(started_at) FROM coating_run x
                                                 WHERE x.target_id = r.target_id)""").fetchall()
    assert all(a == 0 for (a,) in first)
    # every stored signal row belongs to a run and the run raw file is fingerprinted
    assert con.execute("SELECT COUNT(*) FROM coating_run WHERE LENGTH(raw_sha256) <> 64").fetchone()[0] == 0


def test_drift_monitor_sees_target_wear(campaign):
    _, _, db = campaign
    con = sqlite3.connect(db)
    d = drift.run_drift_checks(con)
    cv = d[d["metric"] == "cathode_v"]
    assert (cv["status"] == "alarm").any()
    assert (cv[cv["run_id"] == cv["golden_run_id"]]["deviation_pct"] == 0).all()


def test_model_beats_setpoints_and_mean(campaign):
    _, _, db = campaign
    res = model.train_and_evaluate(sqlite3.connect(db))
    loo = res["loo"]
    assert loo["metrics"]["r2"] > 0.6
    assert loo["metrics"]["mae"] < loo["metrics_setpoints"]["mae"]
    assert len(res["suggestions"]) == 3
    json.dumps(res)  # must be exportable


def test_closed_form_prediction_matches_sklearn():
    rng = np.random.default_rng(0)
    X = rng.random((20, 3))
    y = np.sin(3 * X[:, 0]) + X[:, 1] ** 2 + rng.normal(0, 0.05, 20)
    m = model.fit(X, y)
    sk = m.pop("_sk")
    Xq = rng.random((15, 3))
    Xs = (Xq - np.array(m["x_mean"])) / np.array(m["x_scale"])
    mean_sk, std_sk = sk.predict(Xs, return_std=True)
    mean, std = model.gp_predict(m, Xq)
    assert np.allclose(mean, mean_sk, atol=1e-6)
    noise = m["noise"] * m["y_std"] ** 2
    assert np.allclose(std ** 2 + noise, std_sk ** 2, atol=1e-6)
