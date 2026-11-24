// Model view: parity plot, interactive process map, next-run suggestions.
import { h, fmt } from "./util.js";
import { drawParity, drawMap } from "./charts.js";
import { prepare, predict } from "./gp.js";

export function initModel(data, onResize) {
  const M = data.model;
  const pm = prepare(M.model);
  const F = M.features; // bias_abs_v, n2_ratio, temp_c, power_kw, target_age_kwh
  const label = M.feature_labels, unit = M.feature_units;

  // ---- parity + comparison
  const parityEl = document.querySelector("[data-parity]");
  const drawP = () => drawParity(parityEl, M.loo);
  drawP();
  onResize(parityEl, drawP);
  const a = M.loo.metrics, b = M.loo.metrics_setpoints;
  const maxMae = Math.max(a.mae, b.mae);
  document.querySelector("[data-compare]").replaceChildren(
    h("div", { class: "row" }, h("span", {}, "Gemessene Ist-Werte + Targetalter aus der Datenbank"),
      h("span", { class: "val" }, `${fmt(a.mae, 2)} GPa`),
      h("span", { class: "bar", style: `width:${(a.mae / maxMae) * 100}%` })),
    h("div", { class: "row weak" }, h("span", {}, "Nur Sollwerte aus dem Rezept"),
      h("span", { class: "val" }, `${fmt(b.mae, 2)} GPa`),
      h("span", { class: "bar", style: `width:${(b.mae / maxMae) * 100}%` })),
    h("p", { class: "note" }, `Mittlerer absoluter Fehler über ${M.n_runs} Chargen (R² ${fmt(a.r2, 2)} gegenüber ${fmt(b.r2, 2)}). ` +
      "In der Simulation hängt die Härte auch vom Targetverschleiß und von tatsächlichen Gasflüssen ab. " +
      "Beides kennt das Modell nur, weil die Pipeline Log, Chargenbuch und Labor verknüpft."));

  // ---- sliders
  const best = M.suggestions[0];
  const state = Object.fromEntries(F.map((f) => [f, best[f]]));
  const range = {
    bias_abs_v: [M.bounds.bias_abs_v[0], M.bounds.bias_abs_v[1], 1],
    n2_ratio: [M.bounds.n2_ratio[0], M.bounds.n2_ratio[1], 0.005],
    temp_c: [M.bounds.temp_c[0], M.bounds.temp_c[1], 1],
    power_kw: [M.bounds.power_kw[0], M.bounds.power_kw[1], 0.1],
    target_age_kwh: [0, 900, 10],
  };
  const digits = { bias_abs_v: 0, n2_ratio: 2, temp_c: 0, power_kw: 1, target_age_kwh: 0 };
  const sliderBox = document.querySelector("[data-sliders]");
  const outputs = {};
  const inputs = {};
  for (const f of F) {
    const [lo, hi, step] = range[f];
    const id = `sl-${f}`;
    const out = h("output", { for: id });
    const inp = h("input", { type: "range", id, min: lo, max: hi, step, value: state[f],
      oninput: (e) => { state[f] = +e.target.value; update(); } });
    outputs[f] = out; inputs[f] = inp;
    sliderBox.append(h("div", { class: "slider" }, h("label", { for: id }, h("span", {}, label[f]), out), inp));
  }

  const ys = M.loo.measured;
  const zRange = [Math.floor(Math.min(...ys) - 1), Math.ceil(Math.max(...ys) + 1.5)];
  const mapEl = document.querySelector("[data-map]");
  const readout = document.querySelector("[data-readout]");
  const vec = () => F.map((f) => state[f]);

  // training points need their inputs: rebuild them from the GP training matrix
  const Xraw = M.model.X_train.map((r) => r.map((v, j) => v * M.model.x_scale[j] + M.model.x_mean[j]));
  const points = Xraw.map((r, i) => ({ x: r[0], y: r[1], z: ys[i], id: M.loo.run_id[i] }));

  function update() {
    for (const f of F) {
      outputs[f].textContent = `${fmt(state[f], digits[f])}${unit[f] ? " " + unit[f] : ""}`;
      if (+inputs[f].value !== state[f]) inputs[f].value = state[f];
    }
    const p = predict(pm, vec());
    readout.replaceChildren(
      h("span", { class: "big" }, `${fmt(p.mean, 1)} GPa`),
      h("span", { class: "pm" }, `± ${fmt(p.std, 2)} GPa Modellunsicherheit`),
      h("span", { class: "where" }, `bei ${fmt(state.bias_abs_v, 0)} V Bias und N₂-Anteil ${fmt(state.n2_ratio, 2)}`));
    const fixed = vec();
    drawMap(mapEl, {
      f: (bx, ny) => { fixed[0] = bx; fixed[1] = ny; return predict(pm, fixed).mean; },
      xRange: range.bias_abs_v.slice(0, 2), yRange: range.n2_ratio.slice(0, 2), zRange,
      xLabel: "Bias-Spannung (Betrag) in V", yLabel: "N₂-Anteil",
      points, suggestions: M.suggestions.map((sg) => ({ x: sg.bias_abs_v, y: sg.n2_ratio })),
      current: { x: state.bias_abs_v, y: state.n2_ratio },
      onPick: (bx, ny) => { state.bias_abs_v = Math.round(bx); state.n2_ratio = Math.round(ny * 200) / 200; update(); },
    });
  }
  update();
  onResize(mapEl, update);

  // ---- suggestions
  document.querySelector("[data-suggest-note]").textContent =
    `Ausgewählt nach Expected Improvement gegenüber der besten gemessenen Charge ${M.best_run.run_id} ` +
    `(${fmt(M.best_run.hardness_gpa, 2)} GPa). Geplant für ein frisches Target, weil der Drift-Monitor für ` +
    `${M.active_target} einen Wechsel empfiehlt. Werte sind Ist-Werte in der Kammer.`;
  document.querySelector("[data-suggest]").replaceChildren(h("table", { class: "suggest" },
    h("thead", {}, h("tr", {}, ["Rang", "Bias", "N₂-Anteil", "Temperatur", "Leistung", "Prognose", "Unsicherheit", "Erw. Verbesserung", ""]
      .map((c, i) => h("th", { scope: "col", class: i > 0 && i < 8 ? "num" : null }, c)))),
    h("tbody", {}, M.suggestions.map((sg, i) => h("tr", {},
      h("td", {}, h("span", { class: "rank" }, String(i + 1))),
      h("td", { class: "num" }, `${fmt(sg.bias_abs_v, 0)} V`),
      h("td", { class: "num" }, fmt(sg.n2_ratio, 2)),
      h("td", { class: "num" }, `${fmt(sg.temp_c, 0)} °C`),
      h("td", { class: "num" }, `${fmt(sg.power_kw, 1)} kW`),
      h("td", { class: "num" }, `${fmt(sg.pred_gpa, 1)} GPa`),
      h("td", { class: "num" }, `± ${fmt(sg.std_gpa, 2)} GPa`),
      h("td", { class: "num" }, `${fmt(sg.ei, 2)} GPa`),
      h("td", {}, h("button", { type: "button", class: "linkbtn", onclick: () => {
        for (const f of F) state[f] = sg[f];
        update();
        mapEl.scrollIntoView({ behavior: "smooth", block: "center" });
      }, "aria-label": `Vorschlag ${i + 1} auf der Karte zeigen` }, "Zeigen")))))));

  // ---- relevance
  const rel = M.relevance, maxRel = Math.max(...Object.values(rel));
  document.querySelector("[data-relevance]").replaceChildren(...F.slice().sort((p, q) => rel[q] - rel[p]).map((f) =>
    h("div", { class: "relbar" }, h("span", {}, label[f]),
      h("span", { class: "track" }, h("span", { class: "fill", style: `display:block;width:${(rel[f] / maxRel) * 100}%` })),
      h("span", { class: "v" }, fmt(rel[f], 2)))));
}
