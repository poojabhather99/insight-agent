"use strict";
const $ = (id) => document.getElementById(id);
const state = { ds: null, config: null, busy: false };

function el(tag, text, cls, attrs) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = text;
  if (cls) e.className = cls;
  if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}
function store(k, v) { try { if (v === undefined) return sessionStorage.getItem(k); sessionStorage.setItem(k, v); } catch (e) { return null; } }
function toast(msg) { const t = $("toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, 4000); }
function fmt(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(v);
}

/* ---- api ---- */
async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  const key = store("apikey"); if (key) headers["X-API-Key"] = key;
  if (opts.json) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const r = await fetch(path, Object.assign({}, opts, { headers }));
  if (r.status === 401) { askKey(); throw new Error("An API key is required."); }
  if (!r.ok && !opts.raw) {
    let m = r.statusText; try { const j = await r.json(); m = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (e) { /* ignore */ }
    throw new Error(m);
  }
  return opts.raw ? r : (r.status === 204 ? null : r.json());
}
function askKey() { $("keybtn").hidden = false; toast("This server needs an API key. Use the API key button."); }

/* ---- theme ---- */
(function () { const t = store("theme"); if (t) document.documentElement.dataset.theme = t;
  $("theme").onclick = () => { const d = document.documentElement; const dark = d.dataset.theme === "dark" || (!d.dataset.theme && matchMedia("(prefers-color-scheme:dark)").matches); d.dataset.theme = dark ? "light" : "dark"; store("theme", d.dataset.theme); };
  $("menu").onclick = () => $("side").classList.toggle("open"); })();

/* ---- tables and charts ---- */
function table(res, max) {
  const wrap = el("div", undefined, "tablewrap");
  const t = el("table"); const head = el("tr");
  res.columns.forEach((c) => head.append(el("th", c))); t.append(head);
  res.rows.slice(0, max || 50).forEach((r) => { const tr = el("tr"); r.forEach((v) => tr.append(el("td", fmt(v), typeof v === "number" ? "num" : ""))); t.append(tr); });
  wrap.append(t); return wrap;
}
function niceMax(v) { if (v <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))); const n = v / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }
const NS = "http://www.w3.org/2000/svg";
function s(tag, attrs, text) { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text !== undefined) e.textContent = text; return e; }
function chart(spec) {
  const W = 640, H = 300, L = 54, R = 16, T = 34, B = 56, pw = W - L - R, ph = H - T - B;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": spec.title || "chart" });
  svg.append(s("text", { x: L, y: 18, class: "title" }, spec.title || ""));
  const pts = spec.points.filter((p) => p[1] !== null);
  const ys = pts.map((p) => p[1]); const ymax = niceMax(Math.max(...ys, 0)); const ymin = Math.min(0, ...ys);
  const yv = (v) => T + ph - ((v - ymin) / (ymax - ymin || 1)) * ph;
  for (let i = 0; i <= 4; i++) {
    const v = ymin + ((ymax - ymin) * i) / 4, y = yv(v);
    svg.append(s("line", { x1: L, x2: W - R, y1: y, y2: y, stroke: "var(--line)", "stroke-width": 1 }));
    svg.append(s("text", { x: L - 8, y: y + 4, "text-anchor": "end" }, fmt(+v.toFixed(2))));
  }
  const n = pts.length;
  if (spec.type === "scatter") {
    const xs = pts.map((p) => Number(p[0])); const xmin = Math.min(...xs), xmax = Math.max(...xs);
    const xv = (v) => L + ((Number(v) - xmin) / (xmax - xmin || 1)) * pw;
    pts.forEach((p) => svg.append(s("circle", { cx: xv(p[0]), cy: yv(p[1]), r: 4, fill: "var(--c1)", opacity: 0.75 })));
    svg.append(s("text", { x: L, y: H - 12 }, fmt(xmin)), s("text", { x: W - R, y: H - 12, "text-anchor": "end" }, fmt(xmax)));
  } else {
    const step = pw / n;
    if (spec.type === "bar") {
      pts.forEach((p, i) => { const x = L + i * step + step * 0.15, w = step * 0.7, y = yv(p[1]), y0 = yv(0);
        svg.append(s("rect", { x, y: Math.min(y, y0), width: w, height: Math.abs(y0 - y), rx: 3, fill: "var(--c1)" }));
        if (n <= 12) svg.append(s("text", { x: x + w / 2, y: Math.min(y, y0) - 5, "text-anchor": "middle" }, fmt(p[1]))); });
    } else {
      const d = pts.map((p, i) => (i ? "L" : "M") + (L + i * step + step / 2) + " " + yv(p[1])).join(" ");
      svg.append(s("path", { d, fill: "none", stroke: "var(--c1)", "stroke-width": 2.5 }));
      pts.forEach((p, i) => svg.append(s("circle", { cx: L + i * step + step / 2, cy: yv(p[1]), r: 3.5, fill: "var(--c1)" })));
    }
    const every = Math.ceil(n / 10);
    pts.forEach((p, i) => { if (i % every) return; const x = L + i * step + step / 2;
      const label = String(p[0]).length > 12 ? String(p[0]).slice(0, 11) + "…" : String(p[0]);
      const t = s("text", { x, y: H - 30, "text-anchor": n > 6 ? "end" : "middle", transform: n > 6 ? `rotate(-30 ${x} ${H - 30})` : "" }, label); svg.append(t); });
  }
  svg.append(s("text", { x: W - R, y: 18, "text-anchor": "end" }, spec.y));
  const box = el("div", undefined, "chart"); box.append(svg); return box;
}

/* ---- run rendering (live and replay) ---- */
function newTurn(question) {
  const turn = el("div", undefined, "turn"); turn.append(el("div", question, "bubble"));
  const card = el("div", undefined, "card"); turn.append(card); $("thread").append(turn);
  turn.scrollIntoView({ block: "end", behavior: "smooth" }); return { turn, card, pending: null, sql: "" };
}
function addEvent(t, ev) {
  const card = t.card;
  if (t.pending) { t.pending.remove(); t.pending = null; }
  if (ev.type === "step") {
    const row = el("div", undefined, "step"); row.append(el("span", undefined, "dot"));
    const body = el("div"); body.append(el("span", `Step ${ev.n} · ${ev.action}`, "tag"));
    if (ev.thought) body.append(el("div", ev.thought));
    if (ev.action === "run_sql" && ev.input && ev.input.sql) { t.sql = ev.input.sql; body.append(el("pre", ev.input.sql, "sql")); }
    row.append(body); card.append(row);
    if (ev.action !== "final") { t.pending = el("div", undefined, "step"); t.pending.append(el("span", undefined, "dot pulse"), el("span", "Running…", "muted")); card.append(t.pending); }
  } else if (ev.type === "observation") {
    const row = el("div", undefined, "step"); row.append(el("span", undefined, ev.ok ? "dot" : "dot bad"));
    const body = el("div");
    if (!ev.ok) body.append(el("div", ev.error, "err"));
    else if (ev.result.rows) body.append(el("span", `${ev.result.rows.length} row${ev.result.rows.length === 1 ? "" : "s"}${ev.result.truncated ? " (truncated)" : ""} in ${ev.result.elapsed_ms} ms`, "tag"));
    else body.append(el("span", "Chart prepared", "tag"));
    row.append(body); card.append(row);
  } else if (ev.type === "invalid") {
    const row = el("div", undefined, "step"); row.append(el("span", undefined, "dot bad"), el("span", "Model reply was invalid and is being retried: " + ev.message, "muted")); card.append(row);
  } else if (ev.type === "final") {
    card.append(el("div", ev.answer, ev.refused ? "answer refused" : "answer"));
    if (ev.chart) card.append(chart(ev.chart));
    if (ev.result && ev.result.rows && ev.result.rows.length > 1 && ev.result.columns.length > 1) { const d = el("details"); d.append(el("summary", `Data (${ev.result.rows.length} rows)`), table(ev.result, 50)); card.append(d); }
    if (ev.sql) { const d = el("details"); d.append(el("summary", "SQL"), el("pre", ev.sql, "sql")); card.append(d); }
  } else if (ev.type === "error") {
    card.append(el("div", errText(ev), "err"));
  }
}
function errText(ev) {
  const m = { llm_unavailable: "The model service is unavailable. ", invalid_output: "The model did not return valid actions. ", step_limit: "The agent ran out of steps. " };
  return (m[ev.code] || "") + ev.message;
}
function addMeta(t, runId, status, ms, fb) {
  const m = el("div", undefined, "meta"); m.append(el("span", `${status} · ${(ms / 1000).toFixed(1)} s`));
  [["👍", 1], ["👎", -1]].forEach(([label, val]) => { const b = el("button", label, undefined, { "aria-pressed": String(fb === val), "aria-label": val > 0 ? "Helpful" : "Not helpful" });
    b.onclick = async () => { try { await api(`/api/runs/${runId}/feedback`, { method: "POST", json: { value: val } }); m.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", "false")); b.setAttribute("aria-pressed", "true"); } catch (e) { toast(e.message); } };
    m.append(b); });
  m.append(el("span", "run " + runId));
  t.card.append(m);
}

/* ---- asking ---- */
async function ask(question) {
  if (state.busy || !state.ds) return; state.busy = true; $("go").disabled = true;
  const t = newTurn(question); t.pending = el("div", undefined, "step"); t.pending.append(el("span", undefined, "dot pulse"), el("span", "Thinking…", "muted")); t.card.append(t.pending);
  try {
    const r = await api(`/api/datasets/${state.ds.id}/ask/stream`, { method: "POST", json: { question }, raw: true });
    if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) { /* ignore */ } throw new Error(m); }
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { done, value } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true }); let i;
      while ((i = buf.indexOf("\n\n")) >= 0) { const line = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!line.startsWith("data: ")) continue; const ev = JSON.parse(line.slice(6));
        if (ev.type === "done") { if (t.pending) t.pending.remove(); addMeta(t, ev.run_id, ev.status, ev.latency_ms); loadHistory(); } else addEvent(t, ev); }
    }
  } catch (e) { if (t.pending) t.pending.remove(); t.card.append(el("div", e.message, "err")); }
  finally { state.busy = false; $("go").disabled = false; $("q").focus(); }
}
$("ask").onsubmit = (e) => { e.preventDefault(); const q = $("q").value.trim(); if (q.length < 3) return; $("q").value = ""; ask(q); };

/* ---- datasets ---- */
function suggestions(cols) {
  const out = ["How many rows are there?"]; const nums = cols.filter((c) => /INT|DOUBLE|DECIMAL|FLOAT/.test(c.type) && !/id$/.test(c.name) && c.distinct > 2);
  const cats = cols.filter((c) => c.type === "VARCHAR" && c.distinct > 1 && c.distinct <= 20 && !/id$/.test(c.name));
  const dates = cols.filter((c) => /^(DATE|TIMESTAMP)/.test(c.type)); const bools = cols.filter((c) => c.type === "BOOLEAN");
  if (cats[0]) out.push(`How many records per ${cats[0].name}?`);
  if (nums[0] && cats[0]) out.push(`Average ${nums[0].name} by ${cats[0].name}`);
  if (bools[0] && cats[0]) out.push(`What is the ${bools[0].name} rate by ${cats[0].name}?`);
  if (dates[0]) out.push("Monthly trend of records");
  return out.slice(0, 5);
}
function openDataset(ds) {
  state.ds = ds; $("empty").hidden = true; $("work").hidden = false; $("thread").replaceChildren(); $("preview").hidden = true; $("preview").replaceChildren();
  $("dsname").textContent = ds.name; $("dsmeta").textContent = `${ds.rows.toLocaleString()} rows · ${ds.columns.length} columns`;
  $("schema").replaceChildren(); ds.columns.forEach((c) => { const d = el("span", c.name, "col"); d.append(el("i", c.type.toLowerCase())); d.title = `${c.distinct} distinct, ${c.nulls} empty`; $("schema").append(d); });
  $("chips").replaceChildren(); suggestions(ds.columns).forEach((q) => { const b = el("button", q, "chip", { type: "button" }); b.onclick = () => ask(q); $("chips").append(b); });
  document.querySelectorAll(".dsitem").forEach((b) => b.setAttribute("aria-current", String(b.dataset.id === ds.id)));
  $("side").classList.remove("open"); loadHistory(); $("q").focus();
}
async function loadDatasets(openId) {
  const list = await api("/api/datasets"); $("dslist").replaceChildren();
  list.forEach((ds) => { const li = el("li"); const b = el("button", undefined, "dsitem", { type: "button", "data-id": ds.id }); b.append(el("b", ds.name), el("span", `${ds.rows.toLocaleString()} rows`)); b.onclick = () => openDataset(ds); li.append(b); $("dslist").append(li); });
  const target = list.find((d) => d.id === (openId || (state.ds && state.ds.id)));
  if (target) openDataset(target); else if (!list.length) { state.ds = null; $("empty").hidden = false; $("work").hidden = true; }
}
async function loadHistory() {
  $("history").replaceChildren(); if (!state.ds) return;
  try { const runs = await api(`/api/datasets/${state.ds.id}/runs`);
    runs.slice(0, 12).forEach((r) => { const li = el("li"); const b = el("button", undefined, "hitem", { type: "button" }); b.append(el("b", r.question), el("span", `${r.status} · ${r.steps} steps`));
      b.onclick = async () => { const full = await api(`/api/runs/${r.id}`); const t = newTurn(full.question); full.trace.forEach((ev) => addEvent(t, ev)); if (t.pending) t.pending.remove(); addMeta(t, full.id, full.status, full.latency_ms, full.feedback); $("side").classList.remove("open"); };
      li.append(b); $("history").append(li); }); } catch (e) { /* history is optional */ }
}
async function upload(file) {
  if (!file) return; const fd = new FormData(); fd.append("file", file);
  try { toast("Uploading…"); const ds = await api("/api/datasets", { method: "POST", body: fd }); toast("Loaded " + ds.name); await loadDatasets(ds.id); } catch (e) { toast(e.message); }
}
$("file").onchange = (e) => upload(e.target.files[0]);
const drop = $("drop");
["dragenter", "dragover"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => upload(e.dataTransfer.files[0]));
$("sample").onclick = async () => { try { const ds = await api("/api/datasets/sample", { method: "POST" }); await loadDatasets(ds.id); } catch (e) { toast(e.message); } };
$("delete").onclick = async () => { if (!state.ds) return; if ($("delete").dataset.armed !== "1") { $("delete").dataset.armed = "1"; $("delete").textContent = "Click again to delete"; setTimeout(() => { $("delete").dataset.armed = ""; $("delete").textContent = "Delete"; }, 3000); return; }
  $("delete").dataset.armed = ""; $("delete").textContent = "Delete"; try { await api(`/api/datasets/${state.ds.id}`, { method: "DELETE" }); state.ds = null; await loadDatasets(); } catch (e) { toast(e.message); } };
$("toggleprev").onclick = async () => { const p = $("preview"); if (!p.hidden) { p.hidden = true; return; } try { const r = await api(`/api/datasets/${state.ds.id}/preview`); p.replaceChildren(table(r, 15)); p.hidden = false; } catch (e) { toast(e.message); } };
$("keybtn").onclick = () => { const k = window.prompt("API key"); if (k) { store("apikey", k); init(); } };

async function init() {
  try { state.config = await api("/api/config"); } catch (e) { toast(e.message); return; }
  $("llm").textContent = state.config.llm === "offline" ? "offline planner" : state.config.llm;
  $("droplimit").textContent = `max ${state.config.limits.max_upload_mb} MB, ${state.config.limits.max_rows.toLocaleString()} rows`;
  if (state.config.auth_required) $("keybtn").hidden = false;
  if (state.config.auth_required && !store("apikey")) { toast("This server needs an API key."); return; }
  try { await loadDatasets(); } catch (e) { toast(e.message); }
}
init();
