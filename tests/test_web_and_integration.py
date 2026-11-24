"""The browser and the integration file must compute exactly what Python computes."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from pvdhub.model import gp_predict
from pvdhub.simulate import true_hardness

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_browser_gp_matches_python(tmp_path):
    data = json.loads((ROOT / "web" / "data" / "dashboard.json").read_text(encoding="utf-8"))
    m, bounds, feats = data["model"]["model"], data["model"]["bounds"], data["model"]["features"]
    rng = np.random.default_rng(3)
    X = np.array([[rng.uniform(*bounds[f]) for f in feats] for _ in range(40)])
    mean, std = gp_predict(m, X)
    script = tmp_path / "check.mjs"
    script.write_text(
        f"import {{ prepare, predict }} from {json.dumps((ROOT / 'web' / 'js' / 'gp.js').as_uri())};\n"
        f"const m = prepare({json.dumps(m)});\n"
        f"const X = {json.dumps(X.tolist())};\n"
        "console.log(JSON.stringify(X.map((x) => { const p = predict(m, x); return [p.mean, p.std]; })));\n",
        encoding="utf-8")
    out = np.array(json.loads(subprocess.check_output(["node", str(script)])))
    assert np.allclose(out[:, 0], mean, atol=1e-9)
    assert np.allclose(out[:, 1], std, atol=1e-9)


def test_active_learning_objective_uses_the_same_toy_model():
    """integrations/pvd_coating_objective.py keeps its own copy (it lives in another repo)."""
    src = (ROOT / "integrations" / "pvd_coating_objective.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "simulated_crain_hardness")
    ns: dict = {"np": np}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "copy", "exec"), ns)
    rng = np.random.default_rng(0)
    for _ in range(50):
        b, r, t, p = rng.uniform([50, 0.25, 320, 9], [150, 0.6, 480, 19])
        age, arc = rng.uniform(0, 900), rng.uniform(0, 20)
        assert ns["simulated_crain_hardness"](b, r, t, p, age, arc) == pytest.approx(
            true_hardness("MSIP-1", "CrAlN", b, r, t, p, age, arc))
