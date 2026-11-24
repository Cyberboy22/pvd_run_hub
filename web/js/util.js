// Small helpers shared by all views (German formatting, DOM/SVG builders, tooltip).

const nfCache = new Map();
export function fmt(v, d = 1) {
  if (v === null || v === undefined || !Number.isFinite(+v)) return "–";
  if (!nfCache.has(d)) nfCache.set(d, new Intl.NumberFormat("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d }));
  return nfCache.get(d).format(+v);
}
export const fmtInt = (v) => new Intl.NumberFormat("de-DE").format(v);
export const fmtSigned = (v, d = 1) => (v > 0 ? "+" : v < 0 ? "−" : "±") + fmt(Math.abs(v), d);

export function fmtDate(iso) {
  if (!iso) return "–";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}.${m}.${y}`;
}
export function fmtHours(sec) {
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return `${h}:${String(m).padStart(2, "0")}`;
}

export const PHASE = {
  heating: { de: "Heizen", color: "#c8553d" },
  etching: { de: "Ätzen", color: "#7a4fd0" },
  coating: { de: "Beschichten", color: "#c29a1e" },
  cooling: { de: "Abkühlen", color: "#2f7fbf" },
};

export const SIGNAL = {
  temp_c: { de: "Temperatur", short: "Temp.", unit: "°C", d: 0 },
  bias_v: { de: "Bias-Spannung", short: "Bias", unit: "V", d: 0 },
  power_kw: { de: "Kathodenleistung", short: "Leistung", unit: "kW", d: 1 },
  cathode_v: { de: "Kathodenspannung", short: "U Kathode", unit: "V", d: 1 },
  pressure_pa: { de: "Kammerdruck", short: "Druck", unit: "Pa", d: 3 },
  ar_sccm: { de: "Ar-Fluss", short: "Ar", unit: "sccm", d: 0 },
  n2_sccm: { de: "N₂-Fluss", short: "N₂", unit: "sccm", d: 0 },
};

export const PROCESS_DE = { "MSIP-1": "Magnetronsputtern", "AIP-1": "Arc-Ion-Plating", "HS-1": "High-Speed-PVD" };
export const POSITION_DE = { top: "oben", middle: "Mitte", bottom: "unten" };
export const QUANTITY_DE = {
  thickness: "Schichtdicke", hardness: "Härte", youngs_modulus: "E-Modul", adhesion_class: "Haftklasse",
};

export const SEV = {
  info: { de: "Hinweis" },
  warning: { de: "Warnung" },
  critical: { de: "Kritisch" },
};

// DOM builder: h("div", {class: "x", onclick: fn}, child, "text")
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const SVGNS = "http://www.w3.org/2000/svg";
export function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// Status icons: shape + colour, always shown next to a text label.
export function sevIcon(sev, cls = "ico") {
  if (sev === "critical")
    return s("svg", { viewBox: "0 0 20 20", class: cls, "aria-hidden": "true" },
      s("path", { d: "M6.2 1.5h7.6l4.7 4.7v7.6l-4.7 4.7H6.2l-4.7-4.7V6.2z", fill: "#d03b3b" }),
      s("path", { d: "M10 5.2v6", stroke: "#fff", "stroke-width": "2.2", "stroke-linecap": "round" }),
      s("circle", { cx: 10, cy: 14.6, r: 1.3, fill: "#fff" }));
  if (sev === "warning" || sev === "alarm-warning")
    return s("svg", { viewBox: "0 0 20 20", class: cls, "aria-hidden": "true" },
      s("path", { d: "M10 1.8 19 17.6H1z", fill: "#fab219", stroke: "#a87100", "stroke-width": "1", "stroke-linejoin": "round" }),
      s("path", { d: "M10 7.2v5", stroke: "#18212b", "stroke-width": "2", "stroke-linecap": "round" }),
      s("circle", { cx: 10, cy: 14.8, r: 1.15, fill: "#18212b" }));
  if (sev === "ok")
    return s("svg", { viewBox: "0 0 20 20", class: cls, "aria-hidden": "true" },
      s("circle", { cx: 10, cy: 10, r: 8.5, fill: "#0ca30c" }),
      s("path", { d: "m5.8 10.2 2.8 2.8 5.6-5.8", stroke: "#fff", "stroke-width": "2", fill: "none", "stroke-linecap": "round", "stroke-linejoin": "round" }));
  return s("svg", { viewBox: "0 0 20 20", class: cls, "aria-hidden": "true" },
    s("circle", { cx: 10, cy: 10, r: 8.2, fill: "none", stroke: "#5c6875", "stroke-width": "1.6" }),
    s("path", { d: "M10 9v5", stroke: "#5c6875", "stroke-width": "1.8", "stroke-linecap": "round" }),
    s("circle", { cx: 10, cy: 6.2, r: 1.1, fill: "#5c6875" }));
}

// one shared tooltip element
const tip = () => document.querySelector("[data-tooltip]");
export function showTip(html, x, y) {
  const t = tip();
  t.innerHTML = html;
  t.hidden = false;
  const r = t.getBoundingClientRect();
  let left = x + 14, top = y + 14;
  if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
  if (top + r.height > window.innerHeight - 8) top = y - r.height - 14;
  t.style.left = `${Math.max(8, left)}px`;
  t.style.top = `${Math.max(8, top)}px`;
}
export function hideTip() { const t = tip(); if (t) t.hidden = true; }

export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
