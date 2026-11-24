// SVG charts, hand-built so every mark is deliberate.
import { s, fmt, fmtHours, fmtDate, PHASE, SIGNAL, showTip, hideTip, reducedMotion, escapeHtml, sevIcon } from "./util.js";

export function linear(d0, d1, r0, r1) {
  const span = d1 - d0 || 1;
  const f = (v) => r0 + ((v - d0) / span) * (r1 - r0);
  f.invert = (p) => d0 + ((p - r0) / (r1 - r0)) * span;
  return f;
}

export function niceTicks(min, max, count = 4) {
  const span = max - min || 1;
  const step0 = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((st) => span / st <= count) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

function extent(arr) {
  let lo = Infinity, hi = -Infinity;
  for (const v of arr) if (v !== null && Number.isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
  return lo === Infinity ? [0, 1] : [lo, hi];
}

function linePath(t, vals, x, y) {
  let d = "", pen = false;
  for (let i = 0; i < t.length; i++) {
    const v = vals[i];
    if (v === null || !Number.isFinite(v)) { pen = false; continue; }
    d += (pen ? "L" : "M") + x(t[i]).toFixed(1) + "," + y(v).toFixed(1);
    pen = true;
  }
  return d;
}

/* ------------------------------------------------------------------------
   Lanes: small multiples sharing one time axis, phase bands behind them.
   ------------------------------------------------------------------------ */
export function drawLanes(el, opt) {
  const dark = opt.theme === "dark";
  const W = Math.max(320, el.clientWidth);
  const narrow = W < 560;
  const left = narrow ? 104 : 172, right = 12, top = 26, bottom = 24;
  const laneH = opt.laneH || 54, gap = 8;
  const H = top + opt.lanes.length * (laneH + gap) - gap + bottom;
  const t = opt.t, tMax = t[t.length - 1];
  const x = linear(0, tMax, left, W - right);
  const ink = dark ? "#dce3ea" : "#18212b";
  const muted = dark ? "#93a1b0" : "#5c6875";
  const grid = dark ? "#2a3644" : "#e0e5ea";

  const svg = s("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: dark ? "dark" : "" });
  const defs = s("defs");
  const clipId = `clip-${Math.random().toString(36).slice(2, 8)}`;
  const clipRect = s("rect", { x: 0, y: 0, width: W, height: H });
  defs.append(s("clipPath", { id: clipId }, clipRect));
  defs.append(s("pattern", { id: `${clipId}-hatch`, width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" },
    s("rect", { width: 6, height: 6, fill: dark ? "rgba(208,59,59,0.10)" : "rgba(208,59,59,0.07)" }),
    s("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: "#d03b3b", "stroke-width": 1.4, opacity: 0.55 })));
  svg.append(defs);

  // phase bands + direct labels
  const bands = s("g");
  const phaseLabels = [];
  for (const p of opt.phases) {
    const meta = PHASE[p.phase];
    const x0 = x(p.start_s), x1 = x(Math.min(p.end_s, tMax));
    // on the dark "chamber window" the bands glow from the top, like plasma
    const gid = `${clipId}-g-${p.phase}`;
    if (dark) defs.append(s("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 },
      s("stop", { offset: "0", "stop-color": meta.color, "stop-opacity": 0.34 }),
      s("stop", { offset: "1", "stop-color": meta.color, "stop-opacity": 0.05 })));
    bands.append(s("rect", { x: x0, y: top - 6, width: Math.max(0, x1 - x0), height: H - top - bottom + 6,
      fill: dark ? `url(#${gid})` : meta.color, opacity: dark ? 1 : 0.09 }));
    bands.append(s("rect", { x: x0, y: top - 6, width: Math.max(0, x1 - x0 - 1), height: 3, fill: meta.color }));
    if (x1 - x0 > 38) {
      const lab = s("text", { x: x0 + 4, y: top - 11, fill: dark ? "#dce3ea" : "#18212b",
        style: "font: 600 12px var(--sans)" }, x1 - x0 < 76 && meta.de.length > 6 ? meta.de.slice(0, 5) + "." : meta.de);
      phaseLabels.push({ el: lab, t: p.start_s });
      bands.append(lab);
    }
  }
  svg.append(bands);

  // lanes
  const lanesG = s("g", { "clip-path": `url(#${clipId})` });
  const scales = [];
  opt.lanes.forEach((lane, i) => {
    const y0 = top + i * (laneH + gap);
    const [lo, hi] = extent(lane.values);
    const pad = (hi - lo) * 0.12 || Math.abs(hi) * 0.1 || 1;
    const y = linear(lo - pad, hi + pad, y0 + laneH, y0);
    scales.push(y);
    svg.append(s("line", { x1: left, x2: W - right, y1: y0 + laneH, y2: y0 + laneH, stroke: grid }));
    svg.append(s("text", { x: 0, y: y0 + 14, fill: ink, style: "font: 600 12.5px var(--sans)" }, narrow && lane.short ? lane.short : lane.label));
    svg.append(s("text", { x: 0, y: y0 + 30, fill: muted, style: "font: 400 11.5px var(--sans)" }, lane.unit));
    svg.append(s("text", { x: left - 6, y: y0 + 9, "text-anchor": "end", class: "tick" }, fmt(hi, lane.d ?? 0)));
    svg.append(s("text", { x: left - 6, y: y0 + laneH, "text-anchor": "end", class: "tick" }, fmt(lo, lane.d ?? 0)));
    // flagged spans of this signal
    for (const f of opt.marks || []) {
      if (f.signal !== lane.key || f.start_s === null || f.end_s === null) continue;
      lanesG.append(s("rect", { x: x(f.start_s), y: y0, width: Math.max(3, x(f.end_s) - x(f.start_s)), height: laneH,
        fill: `url(#${clipId}-hatch)`, "data-flag": f.id ?? "" }));
    }
    lanesG.append(s("path", { d: linePath(t, lane.values, x, y), fill: "none", stroke: ink, "stroke-width": 1.6,
      "stroke-linejoin": "round", "stroke-linecap": "round" }));
  });
  svg.append(lanesG);

  // time axis
  const axis = s("g", { class: "axis" });
  const hours = Math.floor(tMax / 3600);
  for (let hh = 0; hh <= hours; hh++) {
    const xx = x(hh * 3600);
    axis.append(s("line", { x1: xx, x2: xx, y1: H - bottom, y2: H - bottom + 4, stroke: muted }));
    axis.append(s("text", { x: xx, y: H - 6, "text-anchor": "middle" }, `${hh} h`));
  }
  svg.append(axis);

  // highlight layer (flag click) + cursor
  const hl = s("rect", { x: 0, y: top - 6, width: 0, height: H - top - bottom + 6, fill: "none",
    stroke: dark ? "#fff" : "#18212b", "stroke-width": 1.5, "stroke-dasharray": "4 3", opacity: 0 });
  svg.append(hl);
  const cursor = s("line", { x1: 0, x2: 0, y1: top - 6, y2: H - bottom, stroke: dark ? "#ffffff" : "#18212b",
    "stroke-width": 1, opacity: 0 });
  svg.append(cursor);
  const dots = opt.lanes.map(() => s("circle", { r: 3.5, fill: dark ? "#fff" : "#18212b", stroke: dark ? "#121a24" : "#fff",
    "stroke-width": 1.5, opacity: 0 }));
  dots.forEach((d) => svg.append(d));

  el.replaceChildren(svg);

  const indexAt = (tt) => {
    let lo = 0, hi = t.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (t[mid] < tt) lo = mid; else hi = mid; }
    return Math.abs(t[lo] - tt) < Math.abs(t[hi] - tt) ? lo : hi;
  };
  const phaseAt = (tt) => opt.phases.find((p) => tt >= p.start_s && tt < p.end_s);

  function setCursor(tt, evt) {
    const i = indexAt(tt), xx = x(t[i]);
    cursor.setAttribute("x1", xx); cursor.setAttribute("x2", xx); cursor.setAttribute("opacity", 0.7);
    let rows = "";
    opt.lanes.forEach((lane, k) => {
      const v = lane.values[i];
      const ok = v !== null && Number.isFinite(v);
      dots[k].setAttribute("cx", xx);
      dots[k].setAttribute("cy", ok ? scales[k](v) : -10);
      dots[k].setAttribute("opacity", ok ? 1 : 0);
      rows += `<div class="tt-row"><span>${lane.label}</span><span>${ok ? fmt(v, lane.d ?? 0) + " " + lane.unit : "fehlt"}</span></div>`;
    });
    const ph = phaseAt(t[i]);
    if (evt) showTip(`<b>${fmtHours(t[i])} h</b>${ph ? ", " + PHASE[ph.phase].de : ""}${rows}`, evt.clientX, evt.clientY);
  }
  function clearCursor() {
    cursor.setAttribute("opacity", 0);
    dots.forEach((d) => d.setAttribute("opacity", 0));
    hideTip();
  }

  if (opt.interactive !== false) {
    const hit = s("rect", { x: left, y: top - 6, width: W - left - right, height: H - top - bottom + 6, fill: "transparent",
      style: "cursor: crosshair" });
    hit.addEventListener("pointermove", (e) => {
      const r = svg.getBoundingClientRect();
      setCursor(x.invert(e.clientX - r.left), e);
    });
    hit.addEventListener("pointerleave", clearCursor);
    svg.append(hit);
  }

  // one orchestrated reveal (hero only)
  function animate(onClock) {
    if (reducedMotion()) { onClock?.(tMax); return; }
    const dur = 2800, start = performance.now();
    phaseLabels.forEach((p) => p.el.setAttribute("opacity", 0));
    cursor.setAttribute("opacity", 0.85);
    function frame(now) {
      const k = Math.min(1, (now - start) / dur);
      const e = 1 - Math.pow(1 - k, 2.2);
      const tt = e * tMax, xx = x(tt);
      clipRect.setAttribute("width", xx);
      cursor.setAttribute("x1", xx); cursor.setAttribute("x2", xx);
      phaseLabels.forEach((p) => { if (tt >= p.t) p.el.setAttribute("opacity", 1); });
      onClock?.(tt);
      if (k < 1) requestAnimationFrame(frame);
      else { clipRect.setAttribute("width", W); cursor.setAttribute("opacity", 0); }
    }
    clipRect.setAttribute("width", left);
    requestAnimationFrame(frame);
  }

  function highlight(start, end) {
    if (start === null || start === undefined) { hl.setAttribute("opacity", 0); return; }
    const x0 = x(start), x1 = x(end ?? tMax);
    hl.setAttribute("x", x0 - 2); hl.setAttribute("width", Math.max(6, x1 - x0 + 4)); hl.setAttribute("opacity", 1);
  }
  return { animate, highlight };
}

/* ------------------------------------------------------------------------
   Drift control chart: deviation from golden run over time.
   ------------------------------------------------------------------------ */
const STATUS_DE = { ok: "im Rahmen", warning: "Warnung", alarm: "Alarm" };

export function drawDrift(el, { rows, thresholds, label, unit, targets, onSelect }) {
  const W = Math.max(320, el.clientWidth), H = 340;
  const left = 52, right = 16, top = 30, bottom = 32;
  const [warn, alarm] = thresholds;
  const times = rows.map((r) => Date.parse(r.started_at));
  const tMin = Math.min(...times) - 6 * 864e5, tMax = Math.max(...times) + 6 * 864e5;
  const devs = rows.map((r) => r.deviation_pct);
  const lim = Math.max(alarm * 1.5, Math.max(...devs.map(Math.abs)) * 1.15);
  const x = linear(tMin, tMax, left, W - right);
  const y = linear(-lim, lim, H - bottom, top);
  const svg = s("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `Abweichung der ${label} vom Golden Run in Prozent über die Zeit` });

  // status zones (recessive)
  const zone = (a, b, fill) => s("rect", { x: left, width: W - left - right, y: y(b), height: y(a) - y(b), fill });
  svg.append(zone(alarm, lim, "#fbe9e8"), zone(-lim, -alarm, "#fbe9e8"), zone(warn, alarm, "#fff5dc"), zone(-alarm, -warn, "#fff5dc"));
  for (const v of niceTicks(-lim, lim, 6)) {
    svg.append(s("line", { x1: left, x2: W - right, y1: y(v), y2: y(v), stroke: v === 0 ? "#9aa6b2" : "#e0e5ea" }));
    svg.append(s("text", { x: left - 8, y: y(v) + 4, "text-anchor": "end", class: "tick" }, `${fmt(v, 0)} %`));
  }
  svg.append(s("text", { x: W - right - 4, y: y(alarm) - 5, "text-anchor": "end", class: "tick" }, `Alarm ab ±${fmt(alarm, 0)} %`));
  svg.append(s("text", { x: W - right - 4, y: y(warn) - 5, "text-anchor": "end", class: "tick" }, `Warnung ab ±${fmt(warn, 0)} %`));

  // month ticks
  const d0 = new Date(tMin);
  for (let d = new Date(d0.getFullYear(), d0.getMonth() + 1, 1); d.getTime() < tMax; d.setMonth(d.getMonth() + 1)) {
    const xx = x(d.getTime());
    svg.append(s("line", { x1: xx, x2: xx, y1: H - bottom, y2: H - bottom + 4, stroke: "#9aa6b2" }));
    svg.append(s("text", { x: xx, y: H - 10, "text-anchor": "middle", class: "tick" },
      d.toLocaleDateString("de-DE", { month: "short" })));
  }

  // target changes
  for (const tg of targets) {
    const tt = Date.parse(tg.installed_on);
    if (tt <= tMin + 6 * 864e5) continue;
    const xx = x(tt);
    svg.append(s("line", { x1: xx, x2: xx, y1: top - 14, y2: H - bottom, stroke: "#18212b", "stroke-dasharray": "3 3" }));
    const flip = xx > W - right - 110; // keep the label inside the chart near the right edge
    svg.append(s("text", { x: flip ? xx - 5 : xx + 5, y: top - 6, "text-anchor": flip ? "end" : "start",
      style: "font: 600 12px var(--sans)", fill: "#18212b" }, `Neues Target ${tg.target_id.split("-").pop()}`));
  }

  // one line per target set, then markers
  const byTarget = {};
  rows.forEach((r, i) => (byTarget[r.target_id] ||= []).push(i));
  for (const idx of Object.values(byTarget)) {
    const d = idx.map((i, k) => (k ? "L" : "M") + x(times[i]).toFixed(1) + "," + y(devs[i]).toFixed(1)).join("");
    svg.append(s("path", { d, fill: "none", stroke: "#46525f", "stroke-width": 2, "stroke-linejoin": "round" }));
  }
  rows.forEach((r, i) => {
    const cx = x(times[i]), cy = y(devs[i]);
    let mark;
    if (r.status === "alarm") mark = s("path", { d: `M${cx},${cy - 7}L${cx + 7},${cy}L${cx},${cy + 7}L${cx - 7},${cy}Z`, fill: "#d03b3b", stroke: "#fff", "stroke-width": 2 });
    else if (r.status === "warning") mark = s("path", { d: `M${cx},${cy - 7}L${cx + 7},${cy + 5.5}L${cx - 7},${cy + 5.5}Z`, fill: "#fab219", stroke: "#fff", "stroke-width": 2 });
    else mark = s("circle", { cx, cy, r: 5, fill: "#18212b", stroke: "#fff", "stroke-width": 2 });
    svg.append(mark);
    if (r.run_id === r.golden_run_id) {
      svg.append(s("circle", { cx, cy, r: 9, fill: "none", stroke: "#c29a1e", "stroke-width": 2 }));
      svg.append(s("text", { x: cx + 12, y: cy - 8, style: "font: 600 11.5px var(--sans)", fill: "#7d600b" }, "Golden Run"));
    }
    const hit = s("circle", { cx, cy, r: 13, fill: "transparent", style: "cursor: pointer", tabindex: 0,
      "aria-label": `${r.run_id}: ${fmt(r.deviation_pct, 1)} Prozent, ${STATUS_DE[r.status]}` });
    const tipHtml = `<b>${r.run_id}</b>, ${fmtDate(r.started_at)}
      <div class="tt-row"><span>${label}</span><span>${fmt(r.value, 2)} ${unit}</span></div>
      <div class="tt-row"><span>Golden Run</span><span>${fmt(r.golden_value, 2)} ${unit}</span></div>
      <div class="tt-row"><span>Abweichung</span><span>${fmt(r.deviation_pct, 1)} %</span></div>
      <div class="tt-row"><span>Targetalter</span><span>${fmt(r.target_age_kwh, 0)} kWh</span></div>
      <div class="tt-row"><span>Status</span><span>${STATUS_DE[r.status]}</span></div>`;
    hit.addEventListener("pointermove", (e) => showTip(tipHtml, e.clientX, e.clientY));
    hit.addEventListener("pointerleave", hideTip);
    hit.addEventListener("focus", () => { const b = hit.getBoundingClientRect(); showTip(tipHtml, b.right, b.bottom); });
    hit.addEventListener("blur", hideTip);
    if (onSelect) hit.addEventListener("click", () => onSelect(r.run_id));
    svg.append(hit);
  });
  el.replaceChildren(svg);
}

/* ------------------------------------------------------------------------
   Parity plot (leave-one-out): measured vs predicted, ±σ whiskers.
   ------------------------------------------------------------------------ */
export function drawParity(el, loo) {
  const W = Math.max(280, el.clientWidth), H = Math.min(W, 380);
  const left = 50, right = 14, top = 12, bottom = 40;
  const all = [...loo.measured, ...loo.predicted];
  const lo = Math.floor(Math.min(...all) - 0.6), hi = Math.ceil(Math.max(...all) + 0.6);
  const x = linear(lo, hi, left, W - right), y = linear(lo, hi, H - bottom, top);
  const svg = s("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": "Gemessene gegen vorhergesagte Härte, Leave-one-out" });
  for (const v of niceTicks(lo, hi, 5)) {
    svg.append(s("line", { x1: x(v), x2: x(v), y1: top, y2: H - bottom, stroke: "#eef1f4" }));
    svg.append(s("line", { x1: left, x2: W - right, y1: y(v), y2: y(v), stroke: "#eef1f4" }));
    svg.append(s("text", { x: x(v), y: H - bottom + 16, "text-anchor": "middle", class: "tick" }, fmt(v, 0)));
    svg.append(s("text", { x: left - 8, y: y(v) + 4, "text-anchor": "end", class: "tick" }, fmt(v, 0)));
  }
  svg.append(s("text", { x: (left + W - right) / 2, y: H - 4, "text-anchor": "middle", class: "tick" }, "gemessene Härte in GPa"));
  svg.append(s("text", { x: 12, y: (top + H - bottom) / 2, "text-anchor": "middle", class: "tick",
    transform: `rotate(-90 12 ${(top + H - bottom) / 2})` }, "vorhergesagt in GPa"));
  svg.append(s("line", { x1: x(lo), y1: y(lo), x2: x(hi), y2: y(hi), stroke: "#9aa6b2", "stroke-dasharray": "5 4" }));
  loo.measured.forEach((m, i) => {
    const p = loo.predicted[i], sd = loo.std[i];
    svg.append(s("line", { x1: x(m), x2: x(m), y1: y(p - sd), y2: y(p + sd), stroke: "#b8a46a", "stroke-width": 2 }));
  });
  loo.measured.forEach((m, i) => {
    const p = loo.predicted[i];
    const c = s("circle", { cx: x(m), cy: y(p), r: 4.5, fill: "#18212b", stroke: "#fff", "stroke-width": 1.5 });
    svg.append(c);
    const hit = s("circle", { cx: x(m), cy: y(p), r: 10, fill: "transparent" });
    hit.addEventListener("pointermove", (e) => showTip(`<b>${loo.run_id[i]}</b>
      <div class="tt-row"><span>gemessen</span><span>${fmt(m, 2)} GPa</span></div>
      <div class="tt-row"><span>vorhergesagt</span><span>${fmt(p, 2)} ± ${fmt(loo.std[i], 2)} GPa</span></div>`, e.clientX, e.clientY));
    hit.addEventListener("pointerleave", hideTip);
    svg.append(hit);
  });
  el.replaceChildren(svg);
}

/* ------------------------------------------------------------------------
   Process map: predicted hardness over two inputs (canvas heatmap + SVG marks).
   ------------------------------------------------------------------------ */
const RAMP = ["#fbf4dc", "#f1df9e", "#e2c25c", "#c99f2a", "#a07c17", "#735810", "#45340a"];
function rampColor(k) {
  k = Math.max(0, Math.min(1, k));
  const pos = k * (RAMP.length - 1), i = Math.min(RAMP.length - 2, Math.floor(pos)), f = pos - i;
  const a = RAMP[i], b = RAMP[i + 1];
  const ch = (h, o) => parseInt(h.slice(o, o + 2), 16);
  const mix = (o) => Math.round(ch(a, o) + (ch(b, o) - ch(a, o)) * f);
  return [mix(1), mix(3), mix(5)];
}
export const rampCss = (k) => { const [r, g, b] = rampColor(k); return `rgb(${r},${g},${b})`; };

export function drawMap(el, o) {
  const W = Math.max(300, el.clientWidth), H = Math.round(Math.min(420, Math.max(280, W * 0.66)));
  const left = 52, right = 14, top = 10, bottom = 40, legendW = 0;
  const pw = W - left - right - legendW, ph = H - top - bottom;
  const [x0, x1] = o.xRange, [y0, y1] = o.yRange;
  const x = linear(x0, x1, left, left + pw), y = linear(y0, y1, top + ph, top);
  const [zLo, zHi] = o.zRange;

  const wrap = document.createElement("div");
  wrap.style.position = "relative";
  wrap.style.width = W + "px";
  wrap.style.maxWidth = "100%";
  const canvas = document.createElement("canvas");
  const nx = 64, ny = 44;
  canvas.width = nx; canvas.height = ny;
  Object.assign(canvas.style, { position: "absolute", left: left + "px", top: top + "px", width: pw + "px", height: ph + "px",
    imageRendering: "auto", borderRadius: "2px" });
  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(nx, ny);
  for (let j = 0; j < ny; j++) {
    const yv = y1 - ((j + 0.5) / ny) * (y1 - y0);
    for (let i = 0; i < nx; i++) {
      const xv = x0 + ((i + 0.5) / nx) * (x1 - x0);
      const z = o.f(xv, yv);
      const [r, g, b] = rampColor((z - zLo) / (zHi - zLo));
      const p = (j * nx + i) * 4;
      img.data[p] = r; img.data[p + 1] = g; img.data[p + 2] = b; img.data[p + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  wrap.append(canvas);

  const svg = s("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, style: "position: relative" });
  for (const v of niceTicks(x0, x1, 5)) {
    svg.append(s("line", { x1: x(v), x2: x(v), y1: top + ph, y2: top + ph + 4, stroke: "#9aa6b2" }));
    svg.append(s("text", { x: x(v), y: top + ph + 16, "text-anchor": "middle", class: "tick" }, fmt(v, 0)));
  }
  for (const v of niceTicks(y0, y1, 5)) {
    svg.append(s("line", { x1: left - 4, x2: left, y1: y(v), y2: y(v), stroke: "#9aa6b2" }));
    svg.append(s("text", { x: left - 7, y: y(v) + 4, "text-anchor": "end", class: "tick" }, fmt(v, 2)));
  }
  svg.append(s("text", { x: left + pw / 2, y: H - 4, "text-anchor": "middle", class: "tick" }, o.xLabel));
  svg.append(s("text", { x: 12, y: top + ph / 2, "text-anchor": "middle", class: "tick", transform: `rotate(-90 12 ${top + ph / 2})` }, o.yLabel));

  for (const p of o.points) {
    if (p.x < x0 || p.x > x1 || p.y < y0 || p.y > y1) continue;
    const c = s("circle", { cx: x(p.x), cy: y(p.y), r: 4.5, fill: rampCss((p.z - zLo) / (zHi - zLo)), stroke: "#18212b", "stroke-width": 1.2 });
    svg.append(c);
    const hit = s("circle", { cx: x(p.x), cy: y(p.y), r: 9, fill: "transparent" });
    hit.addEventListener("pointermove", (e) => { e.stopPropagation(); showTip(`<b>${p.id}</b><div class="tt-row"><span>gemessen</span><span>${fmt(p.z, 2)} GPa</span></div>`, e.clientX, e.clientY); });
    hit.addEventListener("pointerleave", hideTip);
    svg.append(hit);
  }
  // suggestions that land on the same spot of this 2-D slice share one marker
  const groups = [];
  o.suggestions.forEach((sg, i) => {
    const px = x(sg.x), py = y(sg.y);
    const g = groups.find((gr) => Math.hypot(gr.px - px, gr.py - py) < 18);
    if (g) g.labels.push(i + 1); else groups.push({ px, py, labels: [i + 1] });
  });
  for (const g of groups) {
    const text = g.labels.join(", ");
    const w = Math.max(20, 10 + text.length * 6.4);
    const lx = g.px + 14, ly = g.py - 26;
    svg.append(s("line", { x1: g.px, y1: g.py, x2: lx + 4, y2: ly + 10, stroke: "#18212b", "stroke-width": 1.5 }));
    svg.append(s("rect", { x: lx, y: ly, width: w, height: 20, rx: 10, fill: "#18212b", stroke: "#fff", "stroke-width": 1.5 }));
    svg.append(s("text", { x: lx + w / 2, y: ly + 14.5, "text-anchor": "middle", fill: "#fff", style: "font: 600 12.5px var(--display)" }, text));
    svg.append(s("circle", { cx: g.px, cy: g.py, r: 3, fill: "#18212b" }));
  }
  // current point
  const cur = s("g");
  cur.append(s("circle", { cx: x(o.current.x), cy: y(o.current.y), r: 9, fill: "none", stroke: "#fff", "stroke-width": 4 }));
  cur.append(s("circle", { cx: x(o.current.x), cy: y(o.current.y), r: 9, fill: "none", stroke: "#5b35b8", "stroke-width": 2.5 }));
  svg.append(cur);

  const hitArea = s("rect", { x: left, y: top, width: pw, height: ph, fill: "transparent", style: "cursor: crosshair" });
  hitArea.addEventListener("click", (e) => {
    const r = svg.getBoundingClientRect();
    const sx = (e.clientX - r.left) * (W / r.width), sy = (e.clientY - r.top) * (H / r.height);
    o.onPick?.(x.invert(sx), y.invert(sy));
  });
  hitArea.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect();
    const sx = (e.clientX - r.left) * (W / r.width), sy = (e.clientY - r.top) * (H / r.height);
    const xv = x.invert(sx), yv = y.invert(sy);
    showTip(`<div class="tt-row"><span>Bias-Spannung</span><span>${fmt(xv, 0)} V</span></div>
      <div class="tt-row"><span>N₂-Anteil</span><span>${fmt(yv, 2)}</span></div>
      <div class="tt-row"><span>Prognose</span><span>${fmt(o.f(xv, yv), 1)} GPa</span></div>`, e.clientX, e.clientY);
  });
  hitArea.addEventListener("pointerleave", hideTip);
  svg.insertBefore(hitArea, svg.firstChild);
  wrap.append(svg);

  // continuous legend
  const leg = document.createElement("div");
  leg.className = "legend";
  const grad = RAMP.map((c, i) => `${c} ${(i / (RAMP.length - 1)) * 100}%`).join(",");
  leg.innerHTML = `<span>Härte</span><span style="display:inline-block;width:160px;height:10px;border-radius:2px;background:linear-gradient(90deg,${grad})"></span>
    <span>${fmt(zLo, 0)} bis ${fmt(zHi, 0)} GPa</span>
    <span><svg width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="4.5" fill="#e2c25c" stroke="#18212b" stroke-width="1.2"/></svg>durchgeführte Charge</span>
    <span><svg width="22" height="16" aria-hidden="true"><rect x="1" y="2" width="20" height="12" rx="6" fill="#18212b"/></svg>Vorschlag (Rang)</span>
    <span><svg width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="6" fill="none" stroke="#5b35b8" stroke-width="2.5"/></svg>eingestellter Punkt</span>`;
  el.replaceChildren(wrap, leg);
}

export { sevIcon, escapeHtml, SIGNAL };
