#!/usr/bin/env python3
"""PVD coating as an objective for github.com/Cyberboy22/Agentic-active-learning.

Copy this file into the `examples/` folder of that repository and run:

    python examples/pvd_coating_objective.py

The planner then decides which CrAlN coating run (bias voltage, N2 share,
temperature, cathode power) to do next in order to maximise hardness.

`evaluate` is where the real experiment would go: start a coating run, wait for
nanoindentation, return the measured hardness. Here it is the SAME simulated toy
response as in PVD Run Hub (`pvdhub.simulate.true_hardness`, MSIP-1 / CrAlN,
fresh target set) -- it is not a validated physical model.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from src.campaign import run_campaign  # noqa: E402
from src.objective import Objective, register_objective  # noqa: E402

# design space of the CrAlN study in PVD Run Hub
SPACE = [
    ("bias_abs_v", 50.0, 150.0, "V"),
    ("n2_ratio", 0.25, 0.60, "N2/(Ar+N2)"),
    ("temp_c", 320.0, 480.0, "°C"),
    ("power_kw", 9.0, 19.0, "kW"),
]


def simulated_crain_hardness(bias_abs_v: float, n2_ratio: float, temp_c: float, power_kw: float,
                             target_age_kwh: float = 0.0, arc_rate_per_h: float = 3.0) -> float:
    """Copy of pvdhub.simulate.true_hardness for ('MSIP-1', 'CrAlN'); kept in sync by a test."""
    h = 24.5
    h += 5.0 * np.exp(-(((bias_abs_v - 95.0) / 45.0) ** 2)) - 1.5
    h += 3.0 * np.exp(-(((n2_ratio - 0.40) / 0.13) ** 2)) - 1.0
    h += 0.012 * (temp_c - 400.0)
    h += -0.75 * (power_kw - 14.0) / 5.0
    h += -2.4 * target_age_kwh / 900.0
    h += -0.05 * arc_rate_per_h
    return float(h)


@register_objective
class PVDCrAlNHardness(Objective):
    """Maximise indentation hardness of a CrAlN coating on the (simulated) MSIP plant."""

    name = "pvd_craln"
    noise_std = 0.55  # GPa, scatter of a single nanoindentation series

    @property
    def var_names(self) -> list[str]:
        return [s[0] for s in SPACE]

    @property
    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return np.array([s[1] for s in SPACE]), np.array([s[2] for s in SPACE])

    @property
    def units(self) -> list[str]:
        return [s[3] for s in SPACE]

    @property
    def value_unit(self) -> str:
        return "GPa"

    def is_feasible(self, x: np.ndarray) -> bool:
        """Example process window: very high bias only at moderate power (overheating guard)."""
        bias, _, _, power = x
        return bool(bias <= 130.0 or power <= 15.0)

    def evaluate(self, x: np.ndarray) -> float:
        return simulated_crain_hardness(*x)


if __name__ == "__main__":
    result = run_campaign(objective="pvd_craln", planner="heuristic",
                          budget=30, n_init=8, n_rounds=8, seed=0)
    print(f"\nBeste gefundene Härte: {result.best_value:.2f} GPa")
    for name, value, unit in zip(result.var_names, result.best_x, result.units):
        print(f"  {name:<12} {value:8.3f} {unit}")
    print(f"Referenz-Optimum (bekannt, weil simuliert): {result.reference_optimum:.2f} GPa")
