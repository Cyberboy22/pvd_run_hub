// Run explorer: every chart here is fed by SQL against the in-browser database.
import { h, fmt, fmtDate, fmtHours, SIGNAL, SEV, POSITION_DE, QUANTITY_DE, sevIcon } from "./util.js";
import { drawLanes } from "./charts.js";

const LAB_HEAD = { thickness: "Dicke (µm)", hardness: "Härte (GPa)", youngs_modulus: "E-Modul (GPa)", adhesion_class: "Haftklasse" };
const LANE_KEYS = ["temp_c", "bias_v", "power_kw", "cathode_v", "pressure_pa", "ar_sccm", "n2_sccm"];

const SQL = {
  signals: `SELECT t_s, ${LANE_KEYS.join(", ")}
FROM process_signal WHERE run_id = :run ORDER BY t_s;`,
  phases: `SELECT phase, start_s, end_s
FROM run_phase WHERE run_id = :run ORDER BY start_s;`,
  flags: `SELECT flag_id, code, severity, signal, start_s, end_s, message
FROM quality_flag WHERE run_id = :run
ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, start_s;`,
  lab: `SELECT s.position, m.quantity, m.value, m.unit
FROM sample s JOIN measurement m ON m.sample_id = s.sample_id
WHERE s.run_id = :run;`,
  head: `SELECT r.*, rc.coating, rc.bias_v AS sp_bias_v, rc.n2_ratio AS sp_n2_ratio,
       rc.temp_c AS sp_temp_c, f.target_age_kwh, f.arc_rate_per_h
FROM coating_run r
JOIN recipe rc ON rc.recipe_id = r.recipe_id
JOIN run_features f ON f.run_id = r.run_id
WHERE r.run_id = :run;`,
};

function query(db, sql, run) {
  const st = db.prepare(sql);
  st.bind({ ":run": run });
  const rows = [];
  while (st.step()) rows.push(st.getAsObject());
  st.free();
  return rows;
}

export function initExplorer(data, dbPromise, onResize) {
  const list = document.querySelector("[data-runlist]");
  const lanesEl = document.querySelector("[data-lanes]");
  const headEl = document.querySelector("[data-run-head]");
  const flagsEl = document.querySelector("[data-flags]");
  const labEl = document.querySelector("[data-lab]");
  const rawEl = document.querySelector("[data-raw-run]");
  const sqlEl = document.querySelector("[data-run-sql]");
  const runs = [...data.runs].reverse(); // newest first
  let filter = "all", current = null, db = null, chart = null, lastDraw = null;

  // first selection: a run that shows what the pipeline catches
  const showcase = runs.find((r) => r.n_warning >= 2) || runs.find((r) => r.n_warning > 0) || runs[0];

  function visibleRuns() {
    return runs.filter((r) => filter === "all" ? true
      : filter === "issues" ? r.n_warning + r.n_critical > 0 : r.plant_id === filter);
  }

  function renderList() {
    const rows = visibleRuns();
    list.replaceChildren(...rows.map((r) => {
      const issues = r.n_warning + r.n_critical;
      const sev = r.n_critical ? "critical" : r.n_warning ? "warning" : null;
      const row = h("div", { class: "runrow", role: "option", id: `run-${r.run_id}`, "aria-selected": String(r.run_id === current),
        "data-run": r.run_id, onclick: () => select(r.run_id) },
        h("span", { class: "rid" }, r.run_id),
        h("span", { class: "rflags" }, sev ? [sevIcon(sev), h("span", {}, String(issues))] : ""),
        h("span", { class: "rmeta" }, `${r.plant_id}, ${r.coating}, ${fmtDate(r.started_at)}`));
      return row;
    }));
    if (!rows.length) list.append(h("p", { class: "empty" }, "Keine Chargen für diesen Filter."));
    list.setAttribute("aria-activedescendant", current ? `run-${current}` : "");
  }

  document.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => {
    filter = b.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    renderList();
  }));

  list.addEventListener("keydown", (e) => {
    if (!["ArrowDown", "ArrowUp"].includes(e.key)) return;
    e.preventDefault();
    const rows = visibleRuns();
    const i = rows.findIndex((r) => r.run_id === current);
    const next = rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))];
    if (next) { select(next.run_id); document.getElementById(`run-${next.run_id}`)?.scrollIntoView({ block: "nearest" }); }
  });

  function renderHead(r, meta) {
    const st = meta.status === "aborted"
      ? h("span", { class: "status-aborted" }, "abgebrochen") : "abgeschlossen";
    headEl.replaceChildren(
      h("h3", {}, r.run_id),
      h("dl", {},
        h("div", {}, h("dt", {}, "Anlage"), h("dd", {}, r.plant_id)),
        h("div", {}, h("dt", {}, "Rezept"), h("dd", {}, r.recipe_id)),
        h("div", {}, h("dt", {}, "Start"), h("dd", {}, `${fmtDate(r.started_at)}, ${r.started_at.slice(11, 16)} Uhr`)),
        h("div", {}, h("dt", {}, "Target"), h("dd", {}, `${r.target_id}, ${fmt(meta.target_age_kwh, 0)} kWh`)),
        h("div", {}, h("dt", {}, "Arcs"), h("dd", {}, `${fmt(meta.arc_rate_per_h, 1)} pro h`)),
        h("div", {}, h("dt", {}, "Status"), h("dd", {}, st))));
  }

  function renderFlags(flags) {
    if (!flags.length) {
      flagsEl.replaceChildren(h("li", {}, h("span", { class: "none" }, "Keine Befunde. Die Charge ist vollständig und plausibel.")));
      return;
    }
    flagsEl.replaceChildren(...flags.map((f) => {
      const where = f.start_s !== null ? ` bei ${fmtHours(f.start_s)} h` : "";
      const body = h("span", {}, h("span", { class: "msg" }, f.message),
        h("span", { class: "sev" }, `${SEV[f.severity].de}${where}`));
      const inner = f.start_s !== null
        ? h("button", { type: "button", onclick: () => chart?.highlight(f.start_s, f.end_s),
            "aria-label": `${f.message}, im Diagramm markieren` }, body)
        : body;
      return h("li", {}, sevIcon(f.severity), inner);
    }));
  }

  function renderLab(rows, status) {
    if (!rows.length) {
      labEl.replaceChildren(h("p", { class: "note" }, status === "aborted"
        ? "Abgebrochene Chargen werden nicht gemessen und gehen nicht ins Training."
        : "Noch nicht gemessen. Die Charge erscheint in der Trainingstabelle, sobald das Labor die Werte einträgt."));
      return;
    }
    const pos = ["top", "middle", "bottom"].filter((p) => rows.some((r) => r.position === p));
    const qs = ["thickness", "hardness", "youngs_modulus", "adhesion_class"];
    const val = (p, q) => rows.find((r) => r.position === p && r.quantity === q);
    // transposed: one row per quantity, one column per sample position (fits a narrow column)
    labEl.replaceChildren(h("div", { class: "tablewrap lab" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Größe"), pos.map((p) => h("th", { class: "num" }, POSITION_DE[p])))),
      h("tbody", {}, qs.map((q) => h("tr", {}, h("th", { scope: "row" }, LAB_HEAD[q]),
        pos.map((p) => { const v = val(p, q); return h("td", { class: "num" }, v ? (q === "adhesion_class" ? `HF ${fmt(v.value, 0)}` : fmt(v.value, q === "youngs_modulus" ? 0 : 2)) : "–"); })))))));
  }

  function drawRun(sig, phases, flags) {
    const t = sig.map((r) => r.t_s);
    const lanes = LANE_KEYS.map((k) => ({ key: k, label: SIGNAL[k].de, short: SIGNAL[k].short, unit: SIGNAL[k].unit, d: SIGNAL[k].d,
      values: sig.map((r) => r[k]) }));
    chart = drawLanes(lanesEl, { t, lanes, phases, marks: flags, theme: "light", laneH: 50 });
  }

  function select(runId) {
    current = runId;
    list.querySelectorAll(".runrow").forEach((el) => el.setAttribute("aria-selected", String(el.dataset.run === runId)));
    list.setAttribute("aria-activedescendant", `run-${runId}`);
    const r = runs.find((x) => x.run_id === runId);
    rawEl.textContent = data.raw_preview[runId] + "…";
    sqlEl.textContent = Object.values(SQL).join("\n\n").replaceAll(":run", `'${runId}'`);
    if (!db) return;
    const sig = query(db, SQL.signals, runId);
    const phases = query(db, SQL.phases, runId);
    const flags = query(db, SQL.flags, runId);
    const meta = query(db, SQL.head, runId)[0];
    renderHead(r, meta);
    renderFlags(flags);
    renderLab(query(db, SQL.lab, runId), meta.status);
    lastDraw = () => drawRun(sig, phases, flags);
    lastDraw();
  }

  current = showcase.run_id;
  renderList();
  dbPromise.then((database) => {
    db = database;
    select(current);
    document.getElementById(`run-${current}`)?.scrollIntoView({ block: "nearest" });
    onResize(lanesEl, () => lastDraw?.());
  });
}
