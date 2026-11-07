const $ = (s, el = document) => el.querySelector(s);
const money = v => v == null ? "–" : (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = v => v == null ? "–" : (v * 100).toFixed(0) + "%";
const num = v => v == null ? "–" : Number(v).toLocaleString();
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ago = t => { if (!t) return "–"; const m = Math.round((Date.now() / 1000 - t) / 60);
  return m < 1 ? "just now" : m < 60 ? `${m}m ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`; };

let state = { plays: [], selected: null, status: null, domain: 1 };
const AMZ = { 1: "amazon.com", 2: "amazon.co.uk", 3: "amazon.de", 4: "amazon.fr", 6: "amazon.ca", 8: "amazon.it", 9: "amazon.es", 11: "amazon.com.mx" };

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

// ---------- status / KPIs ----------
async function loadStatus() {
  const s = await api("/api/status");
  state.status = s; state.domain = s.domain;
  const mode = $("#mode");
  mode.textContent = s.demo ? "DEMO DATA" : "LIVE · KEEPA";
  mode.className = "badge " + (s.demo ? "demo" : "live");
  $("#tokens").textContent = "Tokens: " + (s.tokens_left ?? "–");
  const ls = s.last_scan;
  $("#lastScan").textContent = ls ? `Last scan: ${ago(ls.started_at)} (${ls.status}${ls.message ? " · " + ls.message : ""})` : "Last scan: never";
  $("#nextScan").textContent = s.next_scan_at ? "Next: " + new Date(s.next_scan_at * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
  const btn = $("#scanBtn");
  btn.disabled = s.scanning; btn.textContent = s.scanning ? "Scanning…" : "Scan now";
  return s;
}

async function loadSummary() {
  const s = await api("/api/summary");
  $("#kPlays").textContent = num(s.n);
  $("#kRoi").textContent = s.avg_roi == null ? "–" : pct(s.avg_roi);
  $("#kProfit").textContent = money(s.total_profit);
  $("#kMonthly").textContent = money(s.monthly);
  $("#kChecked").textContent = num(s.checked_total);
  renderCategories(s.by_category);
}

function renderCategories(rows) {
  const el = $("#catChart");
  if (!rows.length) { el.innerHTML = '<p class="muted">No data yet.</p>'; return; }
  const max = Math.max(...rows.map(r => r.n));
  el.className = "bars";
  el.innerHTML = rows.map(r => `<div class="barrow" data-tip="${esc(r.category)}: ${r.n} plays">
      <span class="lbl" title="${esc(r.category)}">${esc(r.category)}</span>
      <span class="track"><span class="fill" style="display:block;width:${(r.n / max) * 100}%"></span></span>
      <span class="n">${r.n}</span></div>`).join("");
}

// ---------- plays table ----------
async function loadPlays() {
  const q = new URLSearchParams({ sort: $("#fSort").value, source: $("#fSource").value,
    status: $("#fStatus").value, all: $("#fAll").checked });
  state.plays = await api("/api/plays?" + q);
  const tb = $("#playsTable tbody");
  $("#emptyMsg").hidden = state.plays.length > 0;
  tb.innerHTML = state.plays.map((p, i) => {
    const d = p.data, risks = [...(d.rejects || []).map(r => `<span class="chip bad">${esc(r)}</span>`),
      ...(d.flags || []).filter(f => !/estimated|assumed/.test(f)).map(f => `<span class="chip">${esc(f)}</span>`)];
    return `<tr data-i="${i}" class="${p.passes ? "" : "rejected"} ${state.selected === p.source + p.asin ? "sel" : ""}">
      <td>${p.image ? `<img class="thumb" loading="lazy" src="${esc(p.image)}" alt="">` : '<div class="thumb ph"></div>'}</td>
      <td class="title"><div class="name">${esc(p.title)}</div><div class="sub">${esc(p.brand || "")} · ${esc(p.category || "")} · ${esc(p.asin)}${p.source === "leads" ? " · lead" : ""}</div></td>
      <td class="num"><span class="score"><span class="bar"><i style="width:${p.score}%"></i></span>${Math.round(p.score)}</span></td>
      <td class="num">${money(p.buy_price)}</td>
      <td class="num">${money(p.sell_price)}</td>
      <td class="num ${p.profit > 0 ? "pos" : "neg"}">${money(p.profit)}</td>
      <td class="num">${pct(p.roi)}</td>
      <td class="num">${num(p.sales_rank)}</td>
      <td class="num">${num(p.monthly_sold)}</td>
      <td class="num">${num(p.offer_count)}</td>
      <td>${risks.slice(0, 3).join("") || '<span class="muted">–</span>'}</td>
      <td><select data-status="${i}">${["new", "watching", "bought", "dismissed"].map(s => `<option ${s === p.status ? "selected" : ""}>${s}</option>`).join("")}</select></td>
    </tr>`;
  }).join("");
  if (!state.selected && state.plays[0]) showDetail(state.plays[0]);
}

$("#playsTable tbody").addEventListener("click", e => {
  if (e.target.closest("select")) return;
  const tr = e.target.closest("tr"); if (!tr) return;
  showDetail(state.plays[+tr.dataset.i]);
  document.querySelectorAll("#playsTable tr.sel").forEach(r => r.classList.remove("sel"));
  tr.classList.add("sel");
});
$("#playsTable tbody").addEventListener("change", async e => {
  const i = e.target.dataset.status; if (i == null) return;
  const p = state.plays[+i];
  await api(`/api/plays/${p.source}/${p.asin}`, { method: "PATCH", body: JSON.stringify({ status: e.target.value }) });
  refresh();
});

function showDetail(p) {
  if (!p) return;
  state.selected = p.source + p.asin;
  const d = p.data, f = d.fees || {};
  const host = AMZ[state.domain] || "amazon.com";
  $("#detail").innerHTML = `
    <div class="detail-head">${p.image ? `<img src="${esc(p.image)}" alt="">` : ""}
      <div><b>${esc(p.title)}</b><div class="muted">${esc(p.brand || "")} · rank ${num(p.sales_rank)} · ${num(p.monthly_sold)} sold/mo</div></div></div>
    <div class="links">
      <a href="${esc(p.buy_url || `https://www.${host}/dp/${p.asin}`)}" target="_blank" rel="noopener">Buy (${esc(p.store || "source")})</a>
      <a href="https://www.${host}/dp/${p.asin}" target="_blank" rel="noopener">Amazon listing</a>
      <a href="https://keepa.com/#!product/${state.domain}-${p.asin}" target="_blank" rel="noopener">Keepa chart</a>
      <a href="https://sellercentral.amazon.com/hz/approvalrequest/restrictions?asin=${p.asin}" target="_blank" rel="noopener">Can I sell it?</a>
    </div>
    <div class="kv">
      <span>Expected sale price</span><span>${money(d.sell_price)}</span>
      <span class="muted">Referral fee (${d.referral_pct ?? "est."}%)</span><span>-${money(f.referral)}</span>
      <span class="muted">FBA fulfillment</span><span>-${money(f.fba)}</span>
      <span class="muted">Inbound shipping (${d.weight_lb ?? "?"} lb)</span><span>-${money(f.inbound)}</span>
      <span class="muted">Prep + storage</span><span>-${money((f.prep || 0) + (f.storage || 0))}</span>
      <span class="muted">Your cost${f.tax ? " incl. tax" : ""}</span><span>-${money((d.buy_price || 0) + (f.tax || 0))}</span>
      <span class="total">Profit / unit</span><span class="total ${d.profit > 0 ? "pos" : "neg"}">${money(d.profit)} · ${pct(d.roi)} ROI</span>
    </div>
    ${d.proof ? `<p class="${d.verified ? "pos" : "muted"}">${d.verified ? "✓ Verified: " : ""}${esc(d.proof)}</p>` : ""}
    <h3>Buy box price, last 180 days</h3>
    <div class="chart" id="histChart"></div>
    ${(d.flags || []).length ? `<div>${d.flags.map(x => `<span class="chip">${esc(x)}</span>`).join("")}</div>` : ""}
    ${(d.rejects || []).length ? `<div>${d.rejects.map(x => `<span class="chip bad">${esc(x)}</span>`).join("")}</div>` : ""}
    <textarea class="notes" rows="2" placeholder="Notes…">${esc(p.notes || "")}</textarea>`;
  drawHistory($("#histChart"), d.history || [], d.buy_price, d.sell_price);
  $("#detail .notes").addEventListener("change", e =>
    api(`/api/plays/${p.source}/${p.asin}`, { method: "PATCH", body: JSON.stringify({ notes: e.target.value }) }));
}

// ---------- history line chart (single series, step line) ----------
function drawHistory(el, hist, buy, sell) {
  if (hist.length < 2) { el.innerHTML = '<p class="muted">Not enough price history.</p>'; return; }
  const W = 320, H = 150, L = 40, R = 8, T = 10, B = 22;
  const xs = hist.map(h => h[0]), now = Date.now() / 1000;
  const x0 = xs[0], x1 = Math.max(now, xs[xs.length - 1]);
  const vals = hist.map(h => h[1]).concat([buy, sell].filter(Boolean));
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.1 || 1; lo = Math.max(0, lo - pad); hi += pad;
  const sx = t => L + (t - x0) / (x1 - x0) * (W - L - R);
  const sy = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  let d = `M${sx(hist[0][0])},${sy(hist[0][1])}`;
  for (let i = 1; i < hist.length; i++) d += `H${sx(hist[i][0])}V${sy(hist[i][1])}`;
  d += `H${sx(x1)}`;
  const ticks = [lo, (lo + hi) / 2, hi];
  const ref = (v, label) => v ? `<line class="ref" x1="${L}" x2="${W - R}" y1="${sy(v)}" y2="${sy(v)}"/>
      <text class="reflabel" x="${W - R}" y="${sy(v) - 4}" text-anchor="end">${label} ${money(v)}</text>` : "";
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Buy box price history">
    ${ticks.map(v => `<line class="gridline" x1="${L}" x2="${W - R}" y1="${sy(v)}" y2="${sy(v)}"/>
      <text class="axis" x="${L - 6}" y="${sy(v) + 4}" text-anchor="end">$${v.toFixed(0)}</text>`).join("")}
    <text class="axis" x="${L}" y="${H - 4}">${new Date(x0 * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}</text>
    <text class="axis" x="${W - R}" y="${H - 4}" text-anchor="end">today</text>
    ${ref(sell, "sell")}${ref(buy, "buy")}
    <path class="line" d="${d}"/>
    <line class="cross" y1="${T}" y2="${H - B}" visibility="hidden"/>
    <circle class="dot" r="4" visibility="hidden"/>
    <rect x="${L}" y="0" width="${W - L - R}" height="${H}" fill="transparent"/></svg>`;
  const svg = el.querySelector("svg"), cross = svg.querySelector(".cross"), dot = svg.querySelector(".dot"), tip = $("#tooltip");
  svg.addEventListener("pointermove", e => {
    const r = svg.getBoundingClientRect(), t = x0 + ((e.clientX - r.left) * W / r.width - L) / (W - L - R) * (x1 - x0);
    let i = 0; while (i + 1 < hist.length && hist[i + 1][0] <= t) i++;
    const [ht, hv] = hist[i], cx = sx(Math.max(x0, Math.min(t, x1)));
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", cx); dot.setAttribute("cy", sy(hv)); dot.setAttribute("visibility", "visible");
    tip.hidden = false; tip.style.left = e.clientX + 12 + "px"; tip.style.top = e.clientY - 30 + "px";
    tip.innerHTML = `<b>${money(hv)}</b> · since ${new Date(ht * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}`;
  });
  svg.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.hidden = true; });
}

// category bar tooltips
document.addEventListener("pointermove", e => {
  const row = e.target.closest?.(".barrow"); const tip = $("#tooltip");
  if (row) { tip.hidden = false; tip.textContent = row.dataset.tip; tip.style.left = e.clientX + 12 + "px"; tip.style.top = e.clientY - 30 + "px"; }
  else if (!e.target.closest?.(".chart")) tip.hidden = true;
});

// ---------- scan ----------
$("#scanBtn").addEventListener("click", async () => {
  await api("/api/scan", { method: "POST" });
  await loadStatus();
  const poll = setInterval(async () => { const s = await loadStatus(); if (!s.scanning) { clearInterval(poll); refresh(); } }, 2500);
});

// ---------- leads ----------
$("#leadBtn").addEventListener("click", async () => {
  const btn = $("#leadBtn"); btn.disabled = true; $("#leadMsg").textContent = "Checking…";
  try {
    const rows = await api("/api/leads", { method: "POST", body: JSON.stringify({ csv: $("#leadCsv").value }) });
    $("#leadMsg").textContent = `${rows.filter(r => r.passes).length} of ${rows.length} look profitable`;
    $("#leadTable tbody").innerHTML = rows.map(r => r.error ? `<tr><td>${esc(r.code)}</td><td colspan="7" class="muted">${esc(r.error)}</td></tr>` :
      `<tr><td>${esc(r.lead_code)}</td><td class="title"><div class="name">${esc(r.title)}</div><div class="sub">${esc(r.asin)}</div></td>
       <td class="num">${money(r.buy_price)}</td><td class="num">${money(r.sell_price)}</td>
       <td class="num ${r.profit > 0 ? "pos" : "neg"}">${money(r.profit)}</td><td class="num">${pct(r.roi)}</td>
       <td class="num">${Math.round(r.score)}</td>
       <td>${r.passes ? '<span class="pos">✓ Buy</span>' : (r.rejects || []).map(x => `<span class="chip bad">${esc(x)}</span>`).join("")}</td></tr>`).join("");
    refresh();
  } catch (e) { $("#leadMsg").textContent = e.message; }
  btn.disabled = false;
});

// ---------- scan log ----------
async function loadLog() {
  const rows = await api("/api/scans");
  $("#logTable tbody").innerHTML = rows.map(r => `<tr><td>${r.id}</td><td>${esc(r.source)}</td>
    <td>${new Date(r.started_at * 1000).toLocaleString()}</td><td class="${r.status === "error" ? "neg" : ""}">${esc(r.status)}</td>
    <td class="num">${num(r.checked)}</td><td class="num">${num(r.found)}</td><td class="num">${num(r.tokens_left)}</td>
    <td>${esc(r.message || "")}</td></tr>`).join("");
}

// ---------- settings ----------
const SETTINGS_UI = [
  ["What counts as a play", [
    ["min_profit", "Min profit per unit ($)"], ["min_roi", "Min ROI (0.30 = 30%)"],
    ["max_sales_rank", "Max sales rank"], ["min_monthly_sold", "Min units sold / month"],
    ["min_buy_price", "Min buy price ($)"], ["max_buy_price", "Max buy price ($)"],
    ["max_offer_count", "Max competing sellers"], ["skip_hazmat_and_adult", "Skip hazmat & adult items"]]],
  ["Proof it's a real flip", [
    ["min_sales_at_resale", "Min sales at the resale price (last 180 days)"],
    ["min_time_at_resale", "Min share of time at the resale price (0.4 = 40%)"],
    ["min_history_days", "Min days of price history"],
    ["max_dip_days", "Max days the low price has lasted"],
    ["max_price_swing", "Max normal-price swing (1.6 = 60%)"],
    ["max_oos_sales_share", "Max share of 'sales' while out of stock"],
    ["max_drop_percent", "Drops deeper than this % are pricing errors"],
    ["allow_variations", "Allow size/color variations (unverifiable)"]]],
  ["Keepa deal scan", [
    ["min_drop_percent", "Min price drop (%)"],
    ["deal_window", "Drop measured vs (0 = last day, 1 = week, 2 = month, 3 = 90 days)"],
    ["deal_price_types", "Buy from (0 = Amazon, 1 = lowest 3rd-party new)"],
    ["include_categories", "Only these Keepa category ids (comma separated)"],
    ["exclude_categories", "Exclude category ids"],
    ["max_products_per_scan", "Max products per scan (token budget)"], ["use_buybox", "Use buy box data (+2 tokens/item)"]]],
  ["Costs", [
    ["sales_tax_rate", "Sales tax you pay (0.07 = 7%)"], ["inbound_ship_per_lb", "Inbound shipping $/lb"],
    ["prep_cost_per_unit", "Prep $/unit"], ["storage_per_unit", "Storage $/unit"],
    ["default_referral_pct", "Default referral fee %"], ["resale_haircut", "Resale haircut (0.05 = sell 5% under)"]]],
  ["Automation", [["auto_scan", "Auto-scan on a schedule"], ["scan_interval_minutes", "Scan every N minutes"]]],
];
async function loadSettings() {
  const s = await api("/api/settings");
  $("#settingsForm").innerHTML = SETTINGS_UI.map(([title, items]) => `<fieldset><legend>${title}</legend>${items.map(([k, label]) => {
    const v = s[k];
    if (typeof v === "boolean") return `<label class="inline"><input type="checkbox" name="${k}" ${v ? "checked" : ""}> ${label}</label>`;
    if (Array.isArray(v)) return `<label>${label}<input type="text" name="${k}" value="${esc(v.join(","))}"></label>`;
    return `<label>${label}<input type="number" step="any" name="${k}" value="${v}"></label>`;
  }).join("")}</fieldset>`).join("") + `<div class="actions"><button class="primary" type="submit">Save settings</button><span id="setMsg" class="muted"></span></div>`;
}
$("#settingsForm").addEventListener("submit", async e => {
  e.preventDefault();
  const body = {};
  e.target.querySelectorAll("input").forEach(i => body[i.name] = i.type === "checkbox" ? i.checked : i.value);
  await api("/api/settings", { method: "PUT", body: JSON.stringify(body) });
  $("#setMsg").textContent = "Saved. Applies to the next scan.";
});

// ---------- tabs / wiring ----------
document.querySelectorAll(".tabs button").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll(".tabs button, .tab").forEach(x => x.classList.remove("active"));
  b.classList.add("active"); $("#tab-" + b.dataset.tab).classList.add("active");
  if (b.dataset.tab === "log") loadLog();
  if (b.dataset.tab === "settings") loadSettings();
}));
["#fSort", "#fSource", "#fStatus", "#fAll"].forEach(s => $(s).addEventListener("change", loadPlays));

async function refresh() { await Promise.all([loadStatus(), loadSummary(), loadPlays()]); }
refresh();
setInterval(() => { loadStatus(); }, 30000);
setInterval(() => { if (!state.status?.scanning) { loadSummary(); loadPlays(); } }, 120000);
