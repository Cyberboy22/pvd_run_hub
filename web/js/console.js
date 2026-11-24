// SQL console on the real database (sql.js = SQLite compiled to WebAssembly).
import { h, fmt } from "./util.js";

const numFmt = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 4, useGrouping: false });

export function initConsole(data, dbPromise) {
  const input = document.querySelector("[data-sql]");
  const btn = document.querySelector("[data-run-sql-btn]");
  const status = document.querySelector("[data-sql-status]");
  const out = document.querySelector("[data-sql-result]");
  const ex = document.querySelector("[data-examples]");
  let db = null;

  input.value = data.sql_examples[0].sql;
  btn.disabled = true;
  status.textContent = "Datenbank wird geladen …";

  data.sql_examples.forEach((q, i) => {
    ex.append(h("button", { type: "button", class: "chip", "aria-pressed": String(i === 0), onclick: (e) => {
      ex.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c === e.currentTarget)));
      input.value = q.sql;
      run();
    } }, q.title));
  });

  function cell(v) {
    if (v === null) return h("td", { class: "null" }, "NULL");
    if (typeof v === "number") return h("td", { class: "num" }, numFmt.format(v));
    return h("td", {}, String(v));
  }

  function run() {
    if (!db) return;
    const t0 = performance.now();
    try {
      const res = db.exec(input.value);
      const ms = performance.now() - t0;
      if (!res.length) {
        out.replaceChildren();
        status.className = "console-status";
        status.textContent = `Ausgeführt in ${fmt(ms, 1)} ms, keine Ergebnistabelle.`;
        return;
      }
      const { columns, values } = res[res.length - 1];
      const shown = values.slice(0, 500);
      out.replaceChildren(h("table", {},
        h("thead", {}, h("tr", {}, columns.map((c) => h("th", { scope: "col" }, c)))),
        h("tbody", {}, shown.map((row) => h("tr", {}, row.map(cell))))));
      status.className = "console-status";
      status.textContent = `${values.length} ${values.length === 1 ? "Zeile" : "Zeilen"} in ${fmt(ms, 1)} ms` +
        (values.length > shown.length ? `, die ersten ${shown.length} werden gezeigt` : "");
    } catch (err) {
      out.replaceChildren();
      status.className = "console-status error";
      status.textContent = `SQL-Fehler: ${err.message}`;
    }
  }

  btn.addEventListener("click", run);
  input.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); run(); }
  });

  dbPromise.then((database) => {
    db = database;
    btn.disabled = false;
    status.textContent = "";
    run();
  });
}
