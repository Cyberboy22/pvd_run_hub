"""Command line entry point.

    python -m pvdhub build            # simulate -> database -> drift -> model -> web data
    python -m pvdhub build --seed 11  # a different simulated campaign
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import time
from pathlib import Path

from . import drift, model
from .db import build_database
from .export import export_dashboard
from .simulate import simulate

ROOT = Path(__file__).resolve().parent.parent


def build(seed: int, data_dir: Path, web_data: Path) -> None:
    t0 = time.time()
    print("1/5 Simuliere Anlagenlogs und Labormessungen …")
    if data_dir.exists():
        shutil.rmtree(data_dir)
    simulate(data_dir, seed=seed)

    print("2/5 Einlesen, bereinigen, Phasen erkennen, in SQLite laden …")
    db_path = data_dir / "pvd_run_hub.sqlite"
    stats = build_database(data_dir, db_path)

    print("3/5 Golden-Run-Vergleich (Drift-Monitor) …")
    con = sqlite3.connect(db_path)
    d = drift.run_drift_checks(con)
    print(f"  {len(d)} Prüfungen, {int((d['status'] == 'alarm').sum())} Alarme")

    print("4/5 Gauß-Prozess-Modell + Leave-one-out + nächste Versuche …")
    result = model.train_and_evaluate(con)
    con.close()
    m = result["loo"]["metrics"]
    print(f"  LOO: MAE {m['mae']} GPa, R² {m['r2']} ({result['n_runs']} Chargen)")

    print("5/5 Exportiere Daten für das Web-Dashboard …")
    export_dashboard(data_dir, db_path, result, stats, web_data)
    print(f"Fertig in {time.time() - t0:.1f} s → {web_data}")


def main() -> None:
    p = argparse.ArgumentParser(prog="pvdhub", description="PVD Run Hub – Pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="komplette Pipeline ausführen")
    b.add_argument("--seed", type=int, default=7)
    b.add_argument("--data-dir", type=Path, default=ROOT / "data")
    b.add_argument("--web-data", type=Path, default=ROOT / "web" / "data")
    args = p.parse_args()
    if args.cmd == "build":
        build(args.seed, args.data_dir, args.web_data)


if __name__ == "__main__":
    main()
