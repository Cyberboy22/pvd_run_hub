// Bootstrap: load the JSON summary first (fast), the SQLite file in the background.
import { h, s, fmt, fmtInt, fmtHours, PROCESS_DE } from "./util.js";
import { drawLanes } from "./charts.js";
import { initExplorer } from "./explorer.js";
import { initConsole } from "./console.js";
import { initDrift } from "./drift.js";
import { initModel } from "./modelview.js";

const $ = (sel) => document.querySelector(sel);

async function loadDb() {
  const SQL = await window.initSqlJs({ locateFile: (f) => `vendor/${f}` });
  const buf = await fetch("data/pvd_run_hub.sqlite").then((r) => {
    if (!r.ok) throw new Error(`Datenbank nicht gefunden (${r.status})`);
    return r.arrayBuffer();
  });
  return new SQL.Database(new Uint8Array(buf));
}

function onResize(el, fn) {
  let w = el.clientWidth, timer;
  new ResizeObserver(() => {
    if (Math.abs(el.clientWidth - w) < 8) return;
    w = el.clientWidth;
    clearTimeout(timer);
    timer = setTimeout(fn, 120);
  }).observe(el);
}

/* hero ------------------------------------------------------------------ */
function initHero(data) {
  const hero = data.hero, sig = hero.signals;
  $("[data-hero=run]").textContent = `${hero.run_id} (CrAlN, Anlage MSIP-1)`;
  const el = $("#hero-chart");
  const clock = $("[data-hero=clock]");
  const lanes = [
    { key: "temp_c", label: "Temperatur", short: "Temp.", unit: "°C", d: 0, values: sig.temp_c },
    { key: "bias_v", label: "Bias-Spannung", short: "Bias", unit: "V", d: 0, values: sig.bias_v },
    { key: "power_kw", label: "Kathodenleistung", short: "Leistung", unit: "kW", d: 1, values: sig.power_kw },
    { key: "n2_sccm", label: "N₂-Fluss", short: "N₂", unit: "sccm", d: 0, values: sig.n2_sccm },
  ];
  const draw = (animate) => {
    const chart = drawLanes(el, { t: sig.t_s, lanes, phases: hero.phases, theme: "dark", laneH: 58 });
    const end = sig.t_s[sig.t_s.length - 1];
    if (animate) chart.animate((tt) => { clock.textContent = fmtHours(tt); });
    else clock.textContent = fmtHours(end);
  };
  // play once, when the viewport is actually visible
  const io = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) { draw(true); io.disconnect(); }
  }, { threshold: 0.35 });
  draw(false);
  io.observe(el);
  $("[data-hero=replay]").addEventListener("click", () => draw(true));
  onResize(el, () => draw(false));
}

function initFacts(data) {
  const k = data.kpis;
  const issues = (k.flags.warning || 0) + (k.flags.critical || 0) + (k.flags.info || 0);
  $("[data-facts]").innerHTML =
    `Die Demo enthält <strong>${k.runs} simulierte Chargen</strong> aus drei Anlagen: ` +
    `<strong>${fmtInt(k.rows_read)}</strong> Logzeilen, <strong>${fmtInt(k.measurements)}</strong> Labormesswerte und ` +
    `<strong>${issues}</strong> automatisch gefundene Datenprobleme. Das Modell sagt die Härte mit ` +
    `<strong>${fmt(data.model.loo.metrics.mae, 2)} GPa</strong> mittlerem Fehler voraus.`;
  $("[data-ingest-stats]").textContent =
    `Beim Einlesen aller ${k.runs} Dateien: ${k.duplicates} doppelte Zeilen entfernt, ${k.out_of_order} Zeilen ` +
    `in die richtige Reihenfolge gebracht, ${k.sentinels} Platzhalterwerte als fehlend markiert. ` +
    `Insgesamt ${fmtInt(k.values_cleaned)} Messwerte vereinheitlicht.`;
}

/* raw formats ----------------------------------------------------------- */
const RAW_NOTES = {
  "MSIP-1": ["Semikolon als Trennzeichen, Dezimalkomma", "Datum als TT.MM.JJJJ", "Druck in mPa", "Leeres Feld heißt: Wert fehlt", "Logger schreibt manche Zeilen doppelt"],
  "AIP-1": ["Zeitstempel in UTC statt Ortszeit", "Leistung nur als Strom und Spannung", "Druck in Pa", "Fehlende Werte als nan", "Keine Chargennummer in der Datei"],
  "HS-1": ["Tabulator als Trennzeichen", "Zeit relativ in Sekunden, Start im Kopf", "Temperatur in Kelvin, Druck in mbar, Leistung in W", "-9999 heißt: Wert fehlt"],
};
function initRaw(data) {
  const pick = { "MSIP-1": null, "AIP-1": null, "HS-1": null };
  for (const r of data.runs) if (!pick[r.plant_id]) pick[r.plant_id] = r.run_id;
  const grid = $("[data-raw]");
  for (const [plant, runId] of Object.entries(pick)) {
    const pre = h("pre", { tabindex: 0, "aria-label": `Erste Zeilen der Rohdatei ${runId}` }, data.raw_preview[runId]);
    grid.append(h("article", { class: "rawcard" },
      h("header", {}, h("h3", {}, plant), h("p", { class: "proc" }, `${PROCESS_DE[plant]}, Datei ${runId}${plant === "HS-1" ? ".tsv" : ".csv"}`)),
      pre,
      h("ul", {}, RAW_NOTES[plant].map((n) => h("li", {}, n)))));
  }
}

/* ER diagram ------------------------------------------------------------ */
const ER = [
  { t: "plant", x: 10, y: 24, rows: ["plant_id PK", "process", "log_format"] },
  { t: "target_set", x: 10, y: 146, rows: ["target_id PK", "plant_id FK", "installed_on"] },
  { t: "recipe", x: 10, y: 268, rows: ["recipe_id PK", "coating", "bias_v, n2_ratio, temp_c", "is_reference"] },
  { t: "coating_run", x: 340, y: 92, w: 230, rows: ["run_id PK", "plant_id FK", "recipe_id FK", "target_id FK", "started_at", "raw_file, raw_sha256", "status"] },
  { t: "sample", x: 340, y: 330, w: 230, rows: ["sample_id PK", "run_id FK", "substrate, position"] },
  { t: "measurement", x: 340, y: 446, w: 230, rows: ["measurement_id PK", "sample_id FK", "quantity, value, unit", "method, measured_on"] },
  { t: "run_phase", x: 740, y: 10, rows: ["run_id FK", "phase", "start_s, end_s"] },
  { t: "process_signal", x: 740, y: 112, rows: ["run_id FK, t_s", "bias_v, power_kw, …", "pressure_pa, temp_c"] },
  { t: "quality_flag", x: 740, y: 214, rows: ["flag_id PK", "run_id FK", "severity, message"] },
  { t: "run_features", x: 740, y: 316, rows: ["run_id PK FK", "bias_v, n2_ratio, temp_c", "target_age_kwh"] },
  { t: "drift_check", x: 740, y: 418, rows: ["run_id FK", "golden_run_id FK", "metric, deviation_pct"] },
  { t: "training_data", x: 10, y: 446, view: true, rows: ["View: Merkmale + Messwerte", "eine Zeile pro Charge", "nur vollständige Chargen"] },
];
function initEr(data) {
  const W = 980, H = 572, bw = 210, head = 26, rh = 18;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}` });
  const box = {};
  for (const b of ER) {
    const w = b.w || bw, hgt = head + b.rows.length * rh + 10;
    box[b.t] = { ...b, w, h: hgt };
  }
  const line = (d, dashed) => svg.append(s("path", { d, fill: "none", stroke: "#9aa6b2", "stroke-width": 1.5, "stroke-dasharray": dashed ? "4 4" : null }));
  const cr = box.coating_run;
  const rowY = (b, i) => b.y + head + i * rh + 12;
  // master data -> coating_run
  [["plant", 1], ["recipe", 2], ["target_set", 3]].forEach(([t, row]) => {
    const b = box[t], y1 = b.y + 13, y2 = rowY(cr, row);
    line(`M${b.x + b.w},${y1} H${b.x + b.w + 50} V${y2} H${cr.x}`);
  });
  line(`M${box.target_set.x + 105},${box.target_set.y} V${box.plant.y + box.plant.h}`);
  // coating_run -> derived tables
  const trunkX = cr.x + cr.w + 70;
  for (const t of ["run_phase", "process_signal", "quality_flag", "run_features", "drift_check"]) {
    const b = box[t];
    line(`M${cr.x + cr.w},${rowY(cr, 0)} H${trunkX} V${b.y + 13} H${b.x}`);
  }
  line(`M${cr.x + cr.w / 2},${cr.y + cr.h} V${box.sample.y}`);
  line(`M${box.sample.x + box.sample.w / 2},${box.sample.y + box.sample.h} V${box.measurement.y}`);
  line(`M${box.measurement.x},${box.measurement.y + 40} H${box.training_data.x + box.training_data.w}`, true);

  for (const b of Object.values(box)) {
    const g = s("g");
    g.append(s("rect", { x: b.x, y: b.y, width: b.w, height: b.h, rx: 6, fill: b.view ? "#f3f5f8" : "#fff",
      stroke: b.t === "coating_run" ? "#18212b" : "#cdd5dd", "stroke-width": b.t === "coating_run" ? 2 : 1.2,
      "stroke-dasharray": b.view ? "5 4" : null }));
    g.append(s("text", { x: b.x + 10, y: b.y + 18, style: "font: 500 12.5px var(--mono)", fill: "#18212b" }, b.t));
    const n = data.tables[b.t];
    if (n !== undefined)
      g.append(s("text", { x: b.x + b.w - 10, y: b.y + 18, "text-anchor": "end", class: "tick" }, `${fmtInt(n)} Zeilen`));
    g.append(s("line", { x1: b.x, x2: b.x + b.w, y1: b.y + head, y2: b.y + head, stroke: "#e0e5ea" }));
    b.rows.forEach((r, i) => {
      const key = / PK| FK/.exec(r);
      g.append(s("text", { x: b.x + 10, y: rowY(b, i) + 4, style: `font: ${key ? 500 : 400} 11.5px var(--mono)`,
        fill: key ? "#46525f" : "#5c6875" }, r));
    });
    svg.append(g);
  }
  $("[data-er]").replaceChildren(svg);
}

/* rail: highlight the chapter in view ---------------------------------- */
function initRail() {
  const links = [...document.querySelectorAll(".rail ol a")];
  const map = new Map(links.map((a) => [a.getAttribute("href").slice(1), a]));
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      links.forEach((a) => a.removeAttribute("aria-current"));
      map.get(e.target.id)?.setAttribute("aria-current", "true");
    }
  }, { rootMargin: "-40% 0px -55% 0px" });
  for (const id of map.keys()) io.observe(document.getElementById(id));
}

/* go -------------------------------------------------------------------- */
async function main() {
  const data = await fetch("data/dashboard.json").then((r) => r.json());
  initHero(data);
  initFacts(data);
  initRaw(data);
  initEr(data);
  initRail();
  initDrift(data, onResize);
  initModel(data, onResize);

  const dbPromise = loadDb();
  initExplorer(data, dbPromise, onResize);
  initConsole(data, dbPromise);
  dbPromise.catch((err) => {
    for (const el of document.querySelectorAll("[data-lanes], [data-sql-status]"))
      el.textContent = `Die Datenbank konnte nicht geladen werden: ${err.message}. Die Seite muss über einen Webserver geöffnet werden, nicht als Datei.`;
  });
}

main().catch((err) => {
  console.error(err);
  document.querySelector("[data-facts]").textContent =
    "Die Daten konnten nicht geladen werden. Öffnen Sie die Seite über einen Webserver (z. B. python -m http.server), nicht direkt als Datei.";
});
