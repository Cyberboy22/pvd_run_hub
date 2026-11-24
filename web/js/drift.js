// Drift monitor view (data comes from the drift_check table, exported to JSON).
import { h, fmt, fmtDate, sevIcon } from "./util.js";
import { drawDrift } from "./charts.js";

const UNIT = { cathode_v: "V", rate_um_per_h: "µm/h", hardness_gpa: "GPa" };
const STATUS_DE = { ok: "im Rahmen", warning: "Warnung", alarm: "Alarm" };

export function initDrift(data, onResize) {
  const chartEl = document.querySelector("[data-drift-chart]");
  const tableEl = document.querySelector("[data-drift-table]");
  const legendEl = document.querySelector("[data-drift-legend]");
  const alertEl = document.querySelector("[data-drift-alert]");
  const D = data.drift;
  let plant = "MSIP-1", metric = "cathode_v";

  function renderAlert() {
    const fc = D.forecast[plant];
    const rows = D.rows.filter((r) => r.plant_id === plant && r.metric === "cathode_v");
    const last = rows[rows.length - 1];
    const onActive = last && last.target_id === fc.active_target;
    let cls = "is-ok", sev = "ok", title, text;
    const trend = `Trend: ${fmt(fc.slope_pct_per_100kwh, 2)} % pro 100 kWh Targetenergie.`;
    if (onActive && last.status === "alarm") {
      cls = ""; sev = "critical";
      title = `${plant}: Targetwechsel empfohlen`;
      text = `Die letzte Referenzcharge ${last.run_id} liegt ${fmt(last.deviation_pct, 1)} % unter dem Golden Run ` +
        `${last.golden_run_id}. Das Target ${fc.active_target} hat ${fmt(fc.age_now_kwh, 0)} kWh gesehen, ` +
        `die Alarmgrenze wird laut Trend bei etwa ${fmt(fc.age_alarm_kwh, 0)} kWh erreicht. ${trend}`;
    } else if (onActive && last.status === "warning") {
      cls = "is-warning"; sev = "warning";
      title = `${plant}: Kathodenspannung driftet`;
      text = `Letzte Referenzcharge ${last.run_id}: ${fmt(last.deviation_pct, 1)} % gegenüber dem Golden Run. ${trend}`;
    } else {
      title = `${plant}: Target ${fc.active_target} ist neu`;
      text = `Seit dem Wechsel ${fmt(fc.age_now_kwh, 0)} kWh. Laut Trend wird die Alarmgrenze bei etwa ` +
        `${fmt(fc.age_alarm_kwh, 0)} kWh erreicht, das sind noch rund ${fc.runs_left} Chargen. ${trend}`;
    }
    alertEl.className = `alert ${cls}`;
    alertEl.replaceChildren(sevIcon(sev), h("div", {}, h("h3", {}, title), h("p", {}, text)));
  }

  function render() {
    const rows = D.rows.filter((r) => r.plant_id === plant && r.metric === metric);
    const targets = data.targets.filter((t) => t.plant_id === plant);
    drawDrift(chartEl, { rows, thresholds: D.thresholds[metric], label: D.labels[metric], unit: UNIT[metric], targets });
    legendEl.replaceChildren(
      h("span", {}, sevIcon("ok"), "im Rahmen"),
      h("span", {}, sevIcon("warning"), `Warnung ab ±${fmt(D.thresholds[metric][0], 0)} %`),
      h("span", {}, sevIcon("critical"), `Alarm ab ±${fmt(D.thresholds[metric][1], 0)} %`),
      h("span", {}, "Kreis in Gold: Golden Run (Bezug für alle späteren Referenzchargen auf diesem Target)"));
    tableEl.replaceChildren(h("table", {},
      h("thead", {}, h("tr", {}, ["Charge", "Datum", "Target", "Targetalter (kWh)", `${D.labels[metric]} (${UNIT[metric]})`, "Golden Run", "Abweichung (%)", "Status"]
        .map((c) => h("th", { scope: "col" }, c)))),
      h("tbody", {}, rows.map((r) => h("tr", {},
        h("td", {}, r.run_id), h("td", {}, fmtDate(r.started_at)), h("td", {}, r.target_id),
        h("td", { class: "num" }, fmt(r.target_age_kwh, 0)), h("td", { class: "num" }, fmt(r.value, 2)),
        h("td", {}, r.golden_run_id), h("td", { class: "num" }, fmt(r.deviation_pct, 1)), h("td", {}, STATUS_DE[r.status]))))));
    renderAlert();
  }

  document.querySelectorAll("[data-plant]").forEach((b) => b.addEventListener("click", () => {
    plant = b.dataset.plant;
    document.querySelectorAll("[data-plant]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    render();
  }));
  document.querySelectorAll("[data-metric]").forEach((b) => b.addEventListener("click", () => {
    metric = b.dataset.metric;
    document.querySelectorAll("[data-metric]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    render();
  }));
  render();
  onResize(chartEl, render);
}
