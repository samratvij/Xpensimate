"use strict";
// Xpensimate web app: the Android app's screens for a phone browser on the tailnet. No build step:
// plain JS against /api/v1 with the person's own token (stored in this browser only).

// Shared with /import, so signing in here also signs in there.
const TOKEN_KEY = "xpensimate_token";
const TREND_MONTHS = 6;
const MAX_EDGE_PX = 2000;
const ICONS = {
  "Grocery": "🛒", "Eating Out": "🍽️", "Household": "🏠", "Gas & Parking": "⛽", "Rent + Parking": "🏢", "Hydro": "⚡",
  "Wifi + 5G": "📶", "Insurance": "🛡️", "Laundry": "🧺", "Loan": "🏦", "Car": "🚗", "Entertainment": "🎬", "Travel": "✈️",
  "Gifting": "🎁", "Health & Fitness": "💪", "Personal Care": "💆", "Shopping": "🛍️", "Subscriptions": "🔁",
  "Government & Fees": "🏛️", "Transfer": "🔄", "One-time": "⭐", "Misc": "🏷️",
};
// Person and family budget lines come from /taxonomy (person_lines: line -> one of these kinds).
const PERSON_ICONS = { person: "👤", abroad: "🌏", family: "👪" };
const personLines = () => state.taxonomy?.person_lines ?? {};

// ---------- small helpers ----------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const enc = encodeURIComponent;
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cad = new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD" });
const money = n => cad.format(Number(n) || 0);
const pct = n => `${Math.round(n)}%`;
const signedPct = change => `${change >= 0 ? "+" : "−"}${Math.abs(Math.round(change))}%`;
const clamp01 = x => Math.max(0, Math.min(1, x || 0));
const badge = (name, size = "") => `<span class="badge ${size}" aria-hidden="true">${ICONS[name] ?? PERSON_ICONS[personLines()[name]] ?? (name ? "❔" : "↔️")}</span>`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const merchant = t => t.merchant_normalized || t.merchant_raw;
/** A guess worth a glance: picked by Claude (or nothing) with low confidence, never confirmed. Same rule as Android. */
const needsCheck = t => !t.excluded && !t.category_is_manual && !t.purpose_is_manual &&
  (t.categorized_by === "none" || (t.categorized_by === "llm" && Number(t.inference_confidence) < 0.6));

// Months are "YYYY-MM" in the browser's time zone.
const ym = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
const thisMonth = () => ym(new Date());
const monthDate = m => { const [y, mo] = m.split("-").map(Number); return new Date(y, mo - 1, 1); };
const shiftMonth = (m, by) => { const d = monthDate(m); return ym(new Date(d.getFullYear(), d.getMonth() + by, 1)); };
const monthTitle = m => monthDate(m).toLocaleDateString("en-CA", { month: "long", year: "numeric" });
const monthShort = m => monthDate(m).toLocaleDateString("en-CA", { month: "short" });
const monthStart = m => monthDate(m).toISOString();
const monthEnd = m => monthDate(shiftMonth(m, 1)).toISOString();
const localDay = iso => { const d = new Date(iso); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const parseDay = s => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
const today = () => localDay(new Date().toISOString());
const fmtDay = s => parseDay(s).toLocaleDateString("en-CA", { weekday: "long", month: "short", day: "numeric" });
const fmtShortDay = s => parseDay(s).toLocaleDateString("en-CA", { weekday: "short", month: "short", day: "numeric" });
const fmtWhen = iso => new Date(iso).toLocaleString("en-CA", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const fmtFull = iso => new Date(iso).toLocaleString("en-CA", { weekday: "short", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
const fmtDate = iso => new Date(iso).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" });

/** "12.5", "$12.50" or "1,200" as cents; null unless a whole number of cents. */
function toCents(text) {
  const s = String(text).trim().replace(/^\$/, "").replace(/,/g, "");
  if (!/^-?\d+(\.\d{0,2})?$|^-?\.\d{1,2}$/.test(s)) return null;
  const [whole, frac = ""] = s.replace("-", "").split(".");
  const cents = Number(whole || 0) * 100 + Number((frac + "00").slice(0, 2));
  return s.startsWith("-") ? -cents : cents;
}
const centsText = c => `${c < 0 ? "-" : ""}${Math.floor(Math.abs(c) / 100)}.${String(Math.abs(c) % 100).padStart(2, "0")}`;

// ---------- API ----------
class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
const token = () => localStorage.getItem(TOKEN_KEY);

function errorDetail(body) {
  const d = body?.detail;
  if (!d) return null;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map(e => e.msg).join("; ");
  return JSON.stringify(d);
}

async function api(path, { method = "GET", body, form, raw = false } = {}) {
  const headers = { Authorization: `Bearer ${token()}` };
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  let resp;
  try {
    resp = await fetch(`/api/v1${path}`, { method, headers, body: payload });
  } catch {
    throw new ApiError(0, "Can't reach the server. Is Tailscale on?");
  }
  if (resp.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    state.me = null;
    setTimeout(route);
    throw new ApiError(401, "Your access key was rejected. Sign in again.");
  }
  if (raw && resp.ok) return resp;
  const data = resp.status === 204 ? null
    : (resp.headers.get("content-type") || "").includes("json") ? await resp.json() : null;
  if (!resp.ok) throw new ApiError(resp.status, errorDetail(data) || `Server error ${resp.status}.`);
  return data;
}

const state = {
  me: null,
  taxonomy: null,
  homeMonth: thisMonth(),
  purpose: null,
  txMonth: thisMonth(),
  billsMonth: thisMonth(),
  // Month-end review defaults to the month that just ended.
  reviewMonth: shiftMonth(thisMonth(), -1),
  tab: "home",
};

async function taxonomy(fresh = false) {
  if (!state.taxonomy || fresh) state.taxonomy = await api("/taxonomy");
  return state.taxonomy;
}

// ---------- UI pieces ----------
let toastTimer;
function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
}

/** Bottom sheet. Returns the sheet element; `locked` sheets can't be dismissed by tapping outside. */
function openSheet(html, { locked = false } = {}) {
  const root = $("#sheet-root");
  root.innerHTML = `<div class="sheet-backdrop"><div class="sheet" role="dialog" aria-modal="true"><div class="grabber"></div>${html}</div></div>`;
  const backdrop = root.firstElementChild;
  if (!locked) backdrop.addEventListener("click", e => { if (e.target === backdrop) closeSheet(); });
  $$("[data-close]", backdrop).forEach(b => b.addEventListener("click", closeSheet));
  return $(".sheet", backdrop);
}
function closeSheet() { $("#sheet-root").innerHTML = ""; }

function confirmSheet(title, text, action, { danger = true } = {}) {
  return new Promise(resolve => {
    const sheet = openSheet(`<h2>${esc(title)}</h2><p class="muted">${esc(text)}</p>
      <div class="stack"><button class="btn ${danger ? "danger" : ""}" data-yes>${esc(action)}</button>
      <button class="btn text" data-close>Cancel</button></div>`);
    $("[data-yes]", sheet).onclick = () => { closeSheet(); resolve(true); };
    $$("[data-close]", sheet).forEach(b => b.addEventListener("click", () => resolve(false)));
  });
}

function monthBar(month) {
  const max = thisMonth();
  return `<div class="month-bar">
    <button class="icon-btn" data-prev aria-label="Previous month"><svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7"/></svg></button>
    <h2>${esc(monthTitle(month))}</h2>
    <button class="icon-btn" data-next aria-label="Next month" ${month >= max ? "disabled" : ""}><svg viewBox="0 0 24 24"><path d="M9 5l7 7-7 7"/></svg></button>
  </div>`;
}
function bindMonthBar(view, key) {
  $("[data-prev]", view).onclick = () => { state[key] = shiftMonth(state[key], -1); route(); };
  $("[data-next]", view).onclick = () => { state[key] = shiftMonth(state[key], 1); route(); };
}

/** Spent as a solid bar, bills still due as stripes after it, against a limit. */
function progress(spent, projected, limit, { over = false, warn = false } = {}) {
  const s = clamp01(spent / limit), p = Math.max(s, clamp01(projected / limit));
  return `<div class="progress ${over ? "bar-over" : warn ? "bar-warn" : ""}" role="img"
      aria-label="${esc(money(spent))} of ${esc(money(limit))}">
    <div class="due" style="left:${s * 100}%;width:${(p - s) * 100}%"></div><div class="spent" style="width:${s * 100}%"></div></div>`;
}

function txRow(t) {
  const sub = t.excluded ? "Not an expense" : t.is_itemized ? `${t.budget_line} · itemized` : t.budget_line;
  const amount = Number(t.amount) < 0
    ? `<span class="amt refund">+${money(-t.amount)}</span>` : `<span class="amt">${money(t.amount)}</span>`;
  return `<a class="item ${t.excluded ? "dim" : ""}" href="#/txn/${t.id}">
    ${badge(t.excluded ? "" : t.category)}
    <div class="grow"><div class="ellipsis">${esc(merchant(t))}</div><div class="sub ellipsis">${esc(sub)}</div>
      ${t.note ? `<div class="sub ellipsis note-line">📝 ${esc(t.note)}</div>` : ""}</div>
    ${needsCheck(t) ? `<span class="pill check">Check</span>` : ""}${amount}</a>`;
}

function txByDay(txns, { totals = true } = {}) {
  const days = new Map();
  for (const t of txns) {
    const day = localDay(t.timestamp);
    if (!days.has(day)) days.set(day, []);
    days.get(day).push(t);
  }
  return [...days].map(([day, list]) => {
    const total = list.filter(t => !t.excluded).reduce((sum, t) => sum + Number(t.amount), 0);
    return `<div class="section-label"><span>${esc(fmtDay(day))}</span>${totals ? `<span class="muted">${money(total)}</span>` : ""}</div>
      <div class="card flush list">${list.map(txRow).join("")}</div>`;
  }).join("");
}

const loadingHtml = `<div class="spinner" aria-label="Loading"></div>`;
const NOTE_MAX = 1000;
const NOTE_HINT = "Where exactly, what for, who it was for… Helps you remember and plan later.";

/** A note box with its own Save button (shown once the text changes). onSave(text) returns a promise. */
function noteBox(container, value, onSave, { placeholder = NOTE_HINT } = {}) {
  container.innerHTML = `<textarea class="input note-input" rows="2" maxlength="${NOTE_MAX}" placeholder="${esc(placeholder)}">${esc(value ?? "")}</textarea>
    <div class="row note-actions" hidden><span class="grow small muted" data-state></span>
      <button class="link" data-cancel>Cancel</button><button class="link" data-save>Save note</button></div>`;
  const area = $("textarea", container), actions = $(".note-actions", container), stateEl = $("[data-state]", container);
  const grow = () => { area.style.height = "auto"; area.style.height = `${area.scrollHeight + 2}px`; };
  const dirty = () => area.value.trim() !== (value ?? "").trim();
  area.oninput = () => { actions.hidden = !dirty(); stateEl.textContent = ""; stateEl.classList.remove("err"); grow(); };
  $("[data-cancel]", container).onclick = () => { area.value = value ?? ""; actions.hidden = true; grow(); };
  $("[data-save]", container).onclick = async () => {
    $("[data-save]", container).disabled = true;
    stateEl.textContent = "Saving…";
    try {
      await onSave(area.value.trim());
    } catch (e) {
      stateEl.textContent = e.message;
      stateEl.classList.add("err");
      $("[data-save]", container).disabled = false;
    }
  };
  setTimeout(grow);
}

// ---------- screens ----------
async function homeScreen(ctx) {
  const m = state.homeMonth, who = state.purpose;
  const q = who ? `&purpose=${enc(who)}` : "";
  const [summary, trend, tax] = await Promise.all([
    api(`/summary?month=${m}${q}`),
    api(`/trends?months=${TREND_MONTHS}${q}`).catch(() => []),
    taxonomy(),
  ]);
  if (!ctx.alive()) return;

  const projected = summary.total_spent + summary.total_expected;
  const budget = summary.categories.reduce((s, c) => s + (c.monthly_limit || 0), 0);
  const previous = trend.find(t => t.month === shiftMonth(m, -1))?.spent;
  const current = m === thisMonth();
  let compare = "";
  if (previous > 0) {
    // Mid-month, compare where the month is heading, not a few days against a whole month.
    const change = ((current ? projected : summary.total_spent) - previous) / previous * 100;
    compare = `<div class="stat"><div class="k">${current ? "Projected vs last month" : "vs last month"}</div>
      <div class="v">${change >= 0 ? "↗" : "↘"} ${signedPct(change)}</div></div>`;
  }
  const hero = `<div class="card hero">
    <div class="label">${current ? "Spent so far this month" : `Spent in ${esc(monthTitle(m))}`}</div>
    <div><span class="big">${money(summary.total_spent)}</span>${budget > 0 ? `<span class="of"> of ${money(budget)}</span>` : ""}</div>
    ${budget > 0 ? progress(summary.total_spent, projected, budget, { over: summary.total_spent > budget, warn: projected > budget }) : ""}
    <div class="stats">
      ${summary.total_expected > 0 ? `<div class="stat"><div class="k">Bills still due</div><div class="v">${money(summary.total_expected)}</div></div>
        <div class="stat"><div class="k">Projected</div><div class="v">${money(projected)}</div></div>` : ""}
      ${compare}
    </div></div>`;

  let trendCard = "";
  if (trend.length) {
    const peak = Math.max(1, ...trend.map(t => t.spent + t.expected));
    const shown = trend.find(t => t.month === m);
    trendCard = `<div class="card"><h3>Last ${TREND_MONTHS} months</h3>
      <div class="small muted">${shown ? `${esc(monthTitle(m))}: ${money(shown.spent)} spent${shown.expected > 0 ? ` + ${money(shown.expected)} bills due` : ""}` : "Tap a month to open it"}</div>
      <div class="trend" role="img" aria-label="${esc(trend.map(t => `${monthShort(t.month)} ${money(t.spent)}`).join(", "))}">
        ${trend.map(t => `<button data-month="${t.month}" aria-label="${esc(monthTitle(t.month))}">
          ${t.expected > 0 ? `<span class="bar due" style="height:${t.expected / peak * 100}%"></span>` : ""}
          ${t.spent > 0 ? `<span class="bar" style="height:${t.spent / peak * 100}%"></span>` : ""}</button>`).join("")}
      </div>
      <div class="trend-labels">${trend.map(t => `<span class="${t.month === m ? "on" : ""}">${esc(monthShort(t.month))}</span>`).join("")}</div>
      ${trend.some(t => t.expected > 0) ? `<div class="legend"><span><i></i>Spent</span><span><i class="due"></i>Bills still due</span></div>` : ""}
    </div>`;
  }

  const chips = `<div class="chips scroll">
    <button class="chip ${who ? "" : "on"}" data-who="">Everyone</button>
    ${tax.purposes.map(p => `<button class="chip ${who === p ? "on" : ""}" data-who="${esc(p)}">${esc(p)}</button>`).join("")}</div>`;

  // Budgeted lines first (closest to their limit on top), then everything else by size.
  const lines = [...summary.categories].sort((a, b) =>
    (b.monthly_limit != null) - (a.monthly_limit != null) ||
    (b.monthly_limit ? b.projected / b.monthly_limit : 0) - (a.monthly_limit ? a.projected / a.monthly_limit : 0) ||
    b.projected - a.projected);
  const lineCards = lines.length ? lines.map(l => {
    const notes = [];
    if (l.expected > 0) notes.push(`+${money(l.expected)} bills due`);
    if (l.monthly_limit) notes.push(`${pct(l.spent / l.monthly_limit * 100)} of ${money(l.monthly_limit)}`);
    let status = "";
    if (l.monthly_limit) {
      if (l.over_limit) status = `<div class="note over">Over budget by ${money(l.spent - l.monthly_limit)}</div>`;
      else if (l.projected_over_limit) status = `<div class="note">On track to go over with bills still due</div>`;
      else if (l.over_warning) status = `<div class="note">Nearing the budget</div>`;
    }
    const href = `#/line/${enc(l.category)}/${m}${who ? `?purpose=${enc(who)}` : ""}`;
    return `<a class="card" href="${href}" style="display:block;color:inherit">
      <div class="row">${badge(l.category)}<div class="grow"><div style="font-weight:600">${esc(l.category)}</div>
        ${notes.length ? `<div class="small muted">${esc(notes.join(" · "))}</div>` : ""}</div>
        <span class="amt">${money(l.spent)}</span></div>
      ${l.monthly_limit ? progress(l.spent, l.projected, l.monthly_limit, { over: l.over_limit, warn: l.over_warning || l.projected_over_limit }) : ""}
      ${status}</a>`;
  }).join("") : `<p class="muted center">No spending this month yet.</p>`;

  ctx.view.innerHTML = monthBar(m) + `<div class="stack">${hero}${trendCard}${chips}${lineCards}</div>`;
  bindMonthBar(ctx.view, "homeMonth");
  $$("[data-month]", ctx.view).forEach(b => b.onclick = () => { state.homeMonth = b.dataset.month; route(); });
  $$("[data-who]", ctx.view).forEach(b => b.onclick = () => { state.purpose = b.dataset.who || null; route(); });
}

async function lineScreen(ctx, line, m) {
  const who = ctx.query.get("purpose");
  ctx.title(line);
  const q = who ? `&purpose=${enc(who)}` : "";
  const [txns, summary, expected] = await Promise.all([
    api(`/transactions?start=${enc(monthStart(m))}&end=${enc(monthEnd(m))}&budget_line=${enc(line)}&limit=500${q}`),
    api(`/summary?month=${m}${q}`),
    api(`/recurring/expected?month=${m}`),
  ]);
  if (!ctx.alive()) return;
  const spend = summary.categories.find(c => c.category === line);
  const due = expected.filter(p => p.budget_line === line && p.status !== "paid" && (!who || p.recurring.purpose === who));
  const header = `<div class="card">
    <div class="row">${badge(line, "lg")}<div><div style="font-size:20px;font-weight:600">${esc(line)}</div>
      <div class="small muted">${esc(monthTitle(m))} · ${plural(txns.length, "charge")}${who ? ` · ${esc(who)}` : ""}</div></div></div>
    <div style="margin-top:10px"><span class="big" style="font-size:28px;font-weight:700">${money(spend?.spent ?? 0)}</span>
      ${spend?.monthly_limit ? `<span class="muted"> of ${money(spend.monthly_limit)}</span>` : ""}</div>
    ${spend?.monthly_limit ? progress(spend.spent, spend.projected, spend.monthly_limit, { over: spend.over_limit, warn: spend.over_warning || spend.projected_over_limit }) : ""}
    ${spend?.expected > 0 ? `<div class="small muted" style="margin-top:8px">+${money(spend.expected)} in bills still due · ${money(spend.projected)} projected</div>` : ""}
  </div>`;
  const dueHtml = due.length ? `<div class="section-label">Bills still due</div><div class="card flush list">${due.map(p =>
    `<div class="item static"><div class="grow"><div>${esc(p.recurring.name)}</div>
      <div class="sub ${p.status === "overdue" ? "err" : ""}">${esc(fmtShortDay(p.due))} · ${p.status === "overdue" ? "overdue" : "due"}</div></div>
      <span class="amt muted">${money(p.recurring.amount)}</span></div>`).join("")}</div>` : "";
  ctx.view.innerHTML = header + dueHtml +
    (txns.length ? txByDay(txns, { totals: false }) : `<p class="muted center">Nothing spent here in ${esc(monthTitle(m))}.</p>`);
}

async function transactionsScreen(ctx) {
  const m = state.txMonth;
  const txns = await api(`/transactions?start=${enc(monthStart(m))}&end=${enc(monthEnd(m))}&limit=500`);
  if (!ctx.alive()) return;
  ctx.view.innerHTML = monthBar(m) + (txns.length ? txByDay(txns) : `<p class="muted center">No transactions this month.</p>`);
  bindMonthBar(ctx.view, "txMonth");
}

async function transactionScreen(ctx, id) {
  let [txn, tax] = await Promise.all([api(`/transactions/${id}`), taxonomy()]);
  if (!ctx.alive()) return;
  let remember = false, saving = false, error = null;

  const save = async (patch, message) => {
    saving = true; error = null; draw();
    try {
      txn = await api(`/transactions/${id}`, { method: "PATCH", body: { ...patch, remember } });
      if (message) toast(message);
    } catch (e) { error = e.message; }
    saving = false;
    if (ctx.alive()) draw();
  };

  function draw() {
    ctx.title(merchant(txn));
    const items = txn.items || [];
    const itemsTitle = txn.receipt_id ? "Receipt lines" : items.length ? `Split into ${items.length} items` : "Items";
    const itemsAction = txn.receipt_id ? `<a class="link" href="#/receipt/${txn.receipt_id}">Edit on receipt</a>`
      : `<button class="link" data-split ${saving ? "disabled" : ""}>${items.length ? "Edit split" : "Split"}</button>`;
    ctx.view.innerHTML = `<div class="stack">
      <div class="detail-head">
        <div class="merchant">${esc(merchant(txn))}</div>
        <div class="total ${Number(txn.amount) < 0 ? "refund" : ""}">${Number(txn.amount) < 0 ? "+" + money(-txn.amount) : money(txn.amount)}</div>
        <div class="muted">${esc(fmtFull(txn.timestamp))}${txn.account_id ? ` · ${esc(txn.account_id)}` : ""}</div>
        ${txn.merchant_raw !== merchant(txn) ? `<div class="small muted">${esc(txn.merchant_raw)}</div>` : ""}
        ${txn.edited_by ? `<div class="small muted">Last changed by ${esc(txn.edited_by)}</div>` : ""}
      </div>
      ${txn.excluded ? `<div class="err" style="font-weight:600">Not counted as an expense</div>`
        : `<div><span class="chip" style="cursor:default">Budget line: ${esc(txn.budget_line)}</span></div>`}
      ${error ? `<div class="err">${esc(error)}</div>` : ""}
      <div class="card"><h3 style="margin-bottom:8px">Note</h3><div data-note></div></div>
      <div class="card stack">
        <div class="row"><h3>What</h3><span class="tiny muted">${txn.category_is_manual ? "Manual" : `Auto · ${esc(txn.categorized_by)}`}</span></div>
        <div class="chips">${tax.categories.map(c =>
          `<button class="chip ${txn.category === c ? "on" : ""}" data-cat="${esc(c)}" ${saving ? "disabled" : ""}>${esc(c)}</button>`).join("")}</div>
        <div class="row"><h3>Who</h3><span class="tiny muted">${txn.purpose_is_manual ? "Manual" : "Auto"}</span></div>
        <div class="chips">${tax.purposes.map(p =>
          `<button class="chip ${txn.purpose === p ? "on" : ""}" data-who="${esc(p)}" ${saving ? "disabled" : ""}>${esc(p)}</button>`).join("")}</div>
        <label class="row" style="cursor:pointer">
          <div class="grow"><div>Remember for ${esc(merchant(txn))}</div>
            <div class="small muted">Future charges from this merchant get the same choice</div></div>
          <span class="switch"><input type="checkbox" data-remember ${remember ? "checked" : ""}><span></span></span>
        </label>
      </div>
      <button class="btn ${txn.excluded ? "secondary" : "danger"}" data-exclude ${saving ? "disabled" : ""}>
        ${txn.excluded ? "Count as an expense again" : "Not an expense (transfer, card payment…)"}</button>
      <div class="card">
        <div class="row"><h3 class="grow">${itemsTitle}</h3>${itemsAction}</div>
        ${!items.length && !txn.receipt_id ? `<div class="small muted">Bought several things for different people or budgets? Split the charge so each counts on its own.</div>` : ""}
        ${items.length ? `<div class="list" style="margin:8px -16px -16px">${items.map(i => `<div class="item static">
          <div class="grow"><div>${esc(i.sku_name)}</div><div class="sub">${esc(i.budget_line)}</div>
            ${i.note ? `<div class="sub note-line">📝 ${esc(i.note)}</div>` : ""}</div>
          <span class="amt">${money(i.price)}</span></div>`).join("")}</div>` : ""}
      </div></div>`;

    $$("[data-cat]", ctx.view).forEach(b => b.onclick = () => {
      if (txn.category !== b.dataset.cat) save({ category: b.dataset.cat }, remember ? `Saved, and remembered for ${merchant(txn)}` : null);
    });
    $$("[data-who]", ctx.view).forEach(b => b.onclick = () => {
      if (txn.purpose !== b.dataset.who) save({ purpose: b.dataset.who }, remember ? `Saved, and remembered for ${merchant(txn)}` : null);
    });
    $("[data-remember]", ctx.view).onchange = e => { remember = e.target.checked; };
    noteBox($("[data-note]", ctx.view), txn.note, async text => {
      txn = await api(`/transactions/${id}`, { method: "PATCH", body: { note: text } });
      toast(text ? "Note saved" : "Note removed");
      if (ctx.alive()) draw();
    });
    $("[data-exclude]", ctx.view).onclick = () => save({ excluded: !txn.excluded });
    const split = $("[data-split]", ctx.view);
    if (split) split.onclick = () => splitEditor(txn, tax, updated => { txn = updated; if (ctx.alive()) draw(); });
  }
  draw();
}

/** Split a charge into lines with their own what and who; the lines must add up to the charge. */
function splitEditor(txn, tax, onSaved) {
  const total = toCents(Number(txn.amount).toFixed(2));
  const lines = txn.items?.length
    ? txn.items.map(i => ({ name: i.sku_name, amount: Number(i.price).toFixed(2), category: i.category, purpose: i.purpose, note: i.note ?? "" }))
    : [{ name: "", amount: centsText(total), category: txn.category, purpose: txn.purpose, note: "" },
       { name: "", amount: "", category: txn.category, purpose: txn.purpose, note: "" }];
  let busy = false, error = null;
  const sheet = openSheet(`<div id="split"></div>`);
  const box = $("#split", sheet);
  const options = (list, value) => list.map(o => `<option ${o === value ? "selected" : ""}>${esc(o)}</option>`).join("");

  function status() {
    const parsed = lines.map(l => toCents(l.amount));
    const left = total - parsed.reduce((s, c) => s + (c ?? 0), 0);
    const problem = lines.length < 2 ? "Add at least two lines"
      : lines.some(l => !l.name.trim()) ? "Give every line a name"
      : parsed.some(c => c === null || c === 0) ? "Give every line an amount" : null;
    return { left, problem, ok: !busy && !problem && left === 0 };
  }
  function refreshStatus() {
    const s = status();
    const leftEl = $("[data-left]", box);
    leftEl.textContent = s.left === 0 ? "All assigned" : s.left > 0 ? `${money(s.left / 100)} left to assign` : `${money(-s.left / 100)} too much`;
    leftEl.className = `amt ${s.left === 0 ? "ok" : "err"}`;
    $("[data-problem]", box).textContent = s.problem ?? "";
    $("[data-save]", box).disabled = !s.ok;
    $$("[data-amount]", box).forEach(input => {
      const v = lines[input.dataset.amount].amount;
      input.classList.toggle("bad", v.trim() !== "" && toCents(v) === null);
    });
  }
  function draw() {
    box.innerHTML = `<h2>Split ${money(txn.amount)}</h2>
      <p class="small muted" style="margin-top:0">Give each item its own what and who. The lines must add up to the charge.</p>
      <div class="stack">${lines.map((l, i) => `<div class="split-line">
        <div class="row" style="gap:8px">
          <input class="input grow" style="margin:0" placeholder="Item" data-name="${i}" value="${esc(l.name)}" autocomplete="off">
          <input class="input" style="margin:0;width:96px" placeholder="0.00" inputmode="decimal" data-amount="${i}" value="${esc(l.amount)}">
          <button class="icon-btn" data-remove="${i}" aria-label="Remove line" ${lines.length <= 1 ? "disabled" : ""}>
            <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
        </div>
        <div class="two">
          <label class="field">What<select data-category="${i}">${options(tax.categories, l.category)}</select></label>
          <label class="field">Who<select data-purpose="${i}">${options(tax.purposes, l.purpose)}</select></label>
        </div>
        <input class="input" style="margin-top:8px" placeholder="Note (optional)" maxlength="${NOTE_MAX}" data-line-note="${i}" value="${esc(l.note)}" autocomplete="off">
        </div>`).join("")}
      </div>
      <div class="row" style="margin:12px 0"><button class="link" data-add>+ Add line</button><span class="grow"></span><span data-left></span></div>
      <div class="small muted" data-problem></div>
      ${error ? `<p class="err">${esc(error)}</p>` : ""}
      <div class="stack" style="margin-top:12px">
        <button class="btn" data-save>Save split</button>
        ${txn.items?.length ? `<button class="btn text danger" data-unsplit ${busy ? "disabled" : ""}>Remove split</button>` : ""}
        <button class="btn text" data-close>Cancel</button>
      </div>`;
    $$("[data-name]", box).forEach(el => el.oninput = () => { lines[el.dataset.name].name = el.value; refreshStatus(); });
    $$("[data-amount]", box).forEach(el => el.oninput = () => { lines[el.dataset.amount].amount = el.value; refreshStatus(); });
    $$("[data-category]", box).forEach(el => el.onchange = () => { lines[el.dataset.category].category = el.value; });
    $$("[data-purpose]", box).forEach(el => el.onchange = () => { lines[el.dataset.purpose].purpose = el.value; });
    $$("[data-line-note]", box).forEach(el => el.oninput = () => { lines[el.dataset.lineNote].note = el.value; });
    $$("[data-remove]", box).forEach(el => el.onclick = () => { lines.splice(Number(el.dataset.remove), 1); draw(); });
    $("[data-add]", box).onclick = () => {
      const left = status().left, last = lines[lines.length - 1];
      lines.push({ name: "", amount: left > 0 ? centsText(left) : "", category: last?.category ?? txn.category, purpose: last?.purpose ?? txn.purpose, note: "" });
      draw();
      $(`[data-name="${lines.length - 1}"]`, box)?.focus();
    };
    $("[data-close]", box).onclick = closeSheet;
    $("[data-save]", box).onclick = () => submit(lines.map(l => ({
      sku_name: l.name.trim(), price: centsText(toCents(l.amount)), category: l.category, purpose: l.purpose,
      note: l.note.trim() || null,
    })));
    const unsplit = $("[data-unsplit]", box);
    if (unsplit) unsplit.onclick = () => submit([]);
    refreshStatus();
  }
  async function submit(items) {
    busy = true; error = null; refreshStatus();
    try {
      const updated = await api(`/transactions/${txn.id}/items`, { method: "PUT", body: { items } });
      closeSheet();
      toast(items.length ? "Split saved" : "Split removed");
      onSaved(updated);
    } catch (e) {
      busy = false; error = e.message; draw();
    }
  }
  draw();
}

async function receiptsScreen(ctx) {
  const list = await api("/receipts");
  if (!ctx.alive()) return;
  ctx.view.innerHTML = `<div class="stack">
    <label class="btn" style="cursor:pointer">📷 Scan or choose a receipt photo
      <input type="file" accept="image/*" data-file hidden></label>
    ${list.length ? `<div class="card flush list">${list.map(r => {
      const status = (r.matched_transaction_id ? "Matched" : "Waiting for the card charge") + (r.needs_review ? " · check totals" : "");
      return `<a class="item" href="#/receipt/${r.id}"><div class="grow"><div class="ellipsis">${esc(r.merchant)}</div>
        <div class="sub ${r.needs_review ? "err" : ""}">${esc(fmtDate(r.timestamp))} · ${esc(status)}</div></div>
        <span class="amt">${money(r.total)}</span></a>`;
    }).join("")}</div>` : `<p class="muted center">No receipts yet. Scan one to split a purchase into its lines.</p>`}
  </div>`;
  $("[data-file]", ctx.view).onchange = e => { const f = e.target.files[0]; e.target.value = ""; if (f) uploadReceipt(f); };
}

/** Downscale to at most 2000 px and re-encode as JPEG, like the Android app (also turns HEIC into JPEG). */
async function compressImage(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((resolve, reject) => {
      const i = new Image();
      i.onload = () => resolve(i);
      i.onerror = () => reject(new Error("That image couldn't be read."));
      i.src = url;
    });
    const scale = Math.min(1, MAX_EDGE_PX / Math.max(img.naturalWidth, img.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(img.naturalWidth * scale);
    canvas.height = Math.round(img.naturalHeight * scale);
    canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
    return await new Promise((resolve, reject) =>
      canvas.toBlob(b => b ? resolve(b) : reject(new Error("That image couldn't be read.")), "image/jpeg", 0.85));
  } finally {
    URL.revokeObjectURL(url);
  }
}

const UPLOAD_ERRORS = {
  422: "That doesn't look like a receipt. Try again with the whole receipt in frame.",
  415: "That image couldn't be read.",
  413: "That photo is too large.",
  502: "Claude couldn't read the receipt right now. Try again in a minute.",
  503: "Receipt reading isn't set up on the server (no Claude API key).",
};

async function uploadReceipt(file) {
  openSheet(`<h2>Reading receipt…</h2><div class="row"><div class="spinner" style="margin:12px 0"></div>
    <p class="muted">Claude is reading the lines. This can take up to half a minute.</p></div>`, { locked: true });
  try {
    const form = new FormData();
    form.append("file", await compressImage(file), "receipt.jpg");
    const receipt = await api("/receipts/ocr", { method: "POST", form });
    closeSheet();
    location.hash = `#/receipt/${receipt.id}`;
  } catch (e) {
    const sheet = openSheet(`<h2>Couldn't add the receipt</h2><p>${esc(UPLOAD_ERRORS[e.status] ?? e.message)}</p>
      <button class="btn" data-close>OK</button>`);
    $("[data-close]", sheet).focus();
  }
}

async function receiptScreen(ctx, id) {
  let [receipt, tax] = await Promise.all([api(`/receipts/${id}`), taxonomy()]);
  let candidates = [], busy = false, error = null, imageUrl = null;
  const loadCandidates = async () => {
    candidates = receipt.matched_transaction_id ? [] : await api(`/receipts/${id}/candidates`).catch(() => []);
  };
  await loadCandidates();
  if (!ctx.alive()) return;
  ctx.onLeave(() => imageUrl && URL.revokeObjectURL(imageUrl));

  const act = async (fn, message) => {
    busy = true; error = null; draw();
    try {
      const updated = await fn();
      if (updated) { receipt = updated; await loadCandidates(); }
      if (message) toast(message);
    } catch (e) { error = e.message; }
    busy = false;
    if (ctx.alive()) draw();
  };

  function matchCard() {
    if (receipt.matched_transaction_id) {
      return `<div class="card"><h3>Matched to a card charge</h3><div class="row">
        <a class="link" href="#/txn/${receipt.matched_transaction_id}">Open charge</a>
        <button class="link" data-unmatch ${busy ? "disabled" : ""}>Unmatch</button></div></div>`;
    }
    if (!candidates.length) {
      return `<div class="card"><h3>No card charge matched yet</h3>
        <div class="small muted">It will match automatically when the bank alert for this purchase arrives.</div></div>`;
    }
    return `<div class="card flush"><h3 style="padding:16px 16px 4px">Which charge is this?</h3><div class="list">
      ${candidates.map((c, i) => `<button class="item" style="width:100%;border-left:0;border-right:0;border-bottom:0;background:none;text-align:left" data-match="${i}" ${busy ? "disabled" : ""}>
        <div class="grow"><div>${esc(merchant(c.transaction))}</div>
        <div class="sub">${esc(fmtWhen(c.transaction.timestamp))}${c.tip ? " · includes tip" : ""}</div></div>
        <span class="amt">${money(c.transaction.amount)}</span></button>`).join("")}</div></div>`;
  }

  function draw() {
    ctx.title(receipt.merchant);
    ctx.view.innerHTML = `<div class="stack">
      <div class="detail-head"><div class="merchant">${esc(receipt.merchant)}</div>
        <div class="total">${money(receipt.total)}</div>
        <div class="muted">${esc(fmtWhen(receipt.timestamp))} · tax ${money(receipt.tax)}</div>
        ${receipt.created_by ? `<div class="small muted">Scanned by ${esc(receipt.created_by)}</div>` : ""}</div>
      ${receipt.needs_review ? `<div class="err">The lines don't add up to the total, or the date wasn't readable. Check them before trusting this receipt.</div>` : ""}
      ${error ? `<div class="err">${esc(error)}</div>` : ""}
      ${matchCard()}
      <div class="card flush"><div style="padding:16px 16px 4px"><h3>Lines</h3>
        <div class="small muted">Tap a line to change what it is, who it was for, or add a note.</div></div>
        <div class="list">${receipt.items.map(item => `<div class="item" data-item="${item.id}">
          <div class="grow"><div>${esc(item.sku_name)}</div>
          <div class="sub">${esc(item.budget_line)}${item.category_is_manual || item.purpose_is_manual ? " · manual" : ""}</div>
          ${item.note ? `<div class="sub note-line">📝 ${esc(item.note)}</div>` : ""}</div>
          <span class="amt">${money(item.price)}</span></div>`).join("")}</div></div>
      <details class="card"><summary style="font-weight:600;cursor:pointer">Photo</summary>
        <img class="receipt-img" alt="Receipt photo" data-photo style="margin-top:12px"></details>
      <button class="btn danger" data-delete ${busy ? "disabled" : ""}>Delete receipt</button>
    </div>`;

    const img = $("[data-photo]", ctx.view);
    $("details", ctx.view).addEventListener("toggle", async e => {
      if (!e.target.open) return;
      if (!imageUrl) {
        try { imageUrl = URL.createObjectURL(await (await api(`/receipts/${id}/image`, { raw: true })).blob()); }
        catch { img.alt = "The photo isn't available."; return; }
      }
      img.src = imageUrl;
    });
    $("[data-unmatch]", ctx.view)?.addEventListener("click", () =>
      act(() => api(`/receipts/${id}/unmatch`, { method: "POST" }), "Unmatched"));
    $$("[data-match]", ctx.view).forEach(b => b.onclick = () => {
      const c = candidates[Number(b.dataset.match)];
      act(() => api(`/receipts/${id}/match`, { method: "POST", body: { transaction_id: c.transaction.id } }), "Matched");
    });
    $$("[data-item]", ctx.view).forEach(row => row.onclick = () => itemEditor(row.dataset.item));
    $("[data-delete]", ctx.view).onclick = async () => {
      if (!await confirmSheet("Delete this receipt?", "Its lines are removed from the matched card charge, which goes back to a single amount.", "Delete")) return;
      try {
        await api(`/receipts/${id}`, { method: "DELETE" });
        toast("Receipt deleted");
        location.hash = "#/receipts";
      } catch (e) { error = e.message; draw(); }
    };
  }

  function itemEditor(itemId) {
    const sheet = openSheet(`<div id="item-editor"></div>`);
    const box = $("#item-editor", sheet);
    let saving = false;
    const drawItem = () => {
      const item = receipt.items.find(i => i.id === itemId);
      if (!item) return closeSheet();
      box.innerHTML = `<h2>${esc(item.sku_name)}</h2>
        ${item.raw_text ? `<div class="small muted">${esc(item.raw_text)}</div>` : ""}
        <div class="stack" style="margin-top:12px">
          <h3 style="margin:0">What</h3><div class="chips">${tax.categories.map(c =>
            `<button class="chip ${item.category === c ? "on" : ""}" data-c="${esc(c)}" ${saving ? "disabled" : ""}>${esc(c)}</button>`).join("")}</div>
          <h3 style="margin:0">Who</h3><div class="chips">${tax.purposes.map(p =>
            `<button class="chip ${item.purpose === p ? "on" : ""}" data-p="${esc(p)}" ${saving ? "disabled" : ""}>${esc(p)}</button>`).join("")}</div>
          <h3 style="margin:0">Note</h3><div data-item-note></div>
          <button class="btn secondary" data-close>Done</button></div>`;
      noteBox($("[data-item-note]", box), item.note, async text => {
        receipt = await api(`/receipts/${id}/items/${itemId}`, { method: "PATCH", body: { note: text } });
        toast(text ? "Note saved" : "Note removed");
        if (ctx.alive()) { draw(); drawItem(); }
      }, { placeholder: "What this was, or who it was for" });
      $("[data-close]", box).onclick = closeSheet;
      const change = async patch => {
        saving = true; drawItem();
        try { receipt = await api(`/receipts/${id}/items/${itemId}`, { method: "PATCH", body: patch }); }
        catch (e) { toast(e.message); }
        saving = false;
        if (ctx.alive()) { draw(); drawItem(); }
      };
      $$("[data-c]", box).forEach(b => b.onclick = () => { if (item.category !== b.dataset.c) change({ category: b.dataset.c }); });
      $$("[data-p]", box).forEach(b => b.onclick = () => { if (item.purpose !== b.dataset.p) change({ purpose: b.dataset.p }); });
    };
    drawItem();
  }
  draw();
}

async function billsScreen(ctx) {
  const m = state.billsMonth;
  const payments = await api(`/recurring/expected?month=${m}`);
  if (!ctx.alive()) return;
  const due = payments.filter(p => p.status !== "paid").reduce((s, p) => s + p.recurring.amount, 0);
  const paid = payments.filter(p => p.status === "paid").reduce((s, p) => s + p.recurring.amount, 0);
  ctx.view.innerHTML = monthBar(m) + `<div class="stack">
    <div class="card row"><div class="grow"><div class="small muted">Still due</div><div class="amt" style="font-size:22px">${money(due)}</div></div>
      <div class="grow"><div class="small muted">Paid</div><div style="font-size:22px">${money(paid)}</div></div></div>
    ${payments.length ? `<div class="card flush list">${payments.map((p, i) => `<div class="item static">
      <div class="grow"><div>${esc(p.recurring.name)}</div><div class="sub">${esc(fmtShortDay(p.due))} · ${esc(p.budget_line)}</div></div>
      <div class="right"><div class="amt">${money(p.recurring.amount)}</div>
        ${p.status === "paid" ? `<div class="small ok">✓ Paid</div>`
          : `<button class="link small ${p.status === "overdue" ? "danger" : ""}" data-pay="${i}">${p.status === "overdue" ? "Overdue · mark paid" : "Mark paid"}</button>`}
      </div></div>`).join("")}</div>` : `<p class="muted center">No recurring bills this month.</p>`}
  </div>`;
  bindMonthBar(ctx.view, "billsMonth");
  $$("[data-pay]", ctx.view).forEach(b => b.onclick = () => markPaid(payments[Number(b.dataset.pay)]));
}

function markPaid(payment) {
  // Paid late still counts in the month it was due; paying ahead is recorded today.
  const paidOn = payment.due < today() ? payment.due : today();
  const sheet = openSheet(`<h2>Mark ${esc(payment.recurring.name)} paid?</h2>
    <p class="muted">Records a ${esc(payment.budget_line)} payment on ${esc(fmtShortDay(paidOn))}. Use this for bills that don't come through a card.</p>
    <label class="field">Amount<input inputmode="decimal" data-amount value="${Number(payment.recurring.amount).toFixed(2)}"></label>
    <p class="err" data-error></p>
    <div class="stack"><button class="btn" data-save>Mark paid</button><button class="btn text" data-close>Cancel</button></div>`);
  const input = $("[data-amount]", sheet), saveBtn = $("[data-save]", sheet);
  input.oninput = () => { const c = toCents(input.value); saveBtn.disabled = !(c > 0); };
  saveBtn.onclick = async () => {
    saveBtn.disabled = true;
    try {
      await api(`/recurring/${payment.recurring.id}/paid`, { method: "POST", body: { paid_on: paidOn, amount: centsText(toCents(input.value)) } });
      closeSheet();
      toast(`${payment.recurring.name} marked paid`);
      route();
    } catch (e) { $("[data-error]", sheet).textContent = e.message; saveBtn.disabled = false; }
  };
}

async function moreScreen(ctx) {
  if (!state.me) state.me = await api("/me").catch(() => null);
  if (!ctx.alive()) return;
  const standalone = navigator.standalone || matchMedia("(display-mode: standalone)").matches;
  const link = (href, icon, label, sub) => `<a class="item" href="${href}"><span class="badge sm" aria-hidden="true">${icon}</span>
    <div class="grow"><div>${label}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div><span class="muted">›</span></a>`;
  ctx.view.innerHTML = `<div class="stack">
    <div class="card flush list">
      ${link("#/review", "📅", "Month review", "How last month went, and what needs a look")}
      ${link("#/budgets", "🎯", "Budgets", "Monthly limits per budget line")}
      ${link("#/alerts", "🔔", "Budget alerts", "Warnings and overages so far")}
      ${link("#/categories", "🏷️", "Categories", "Add, rename or merge")}
      ${link("/import", "📄", "Import a statement", "CSV or RBC PDF (easier on a computer)")}
    </div>
    ${standalone ? "" : `<div class="card small"><b>Tip:</b> in Safari, tap the Share button, then <b>Add to Home Screen</b>, to open Xpensimate like an app.</div>`}
    <div class="card"><div class="small muted">Signed in as</div><div style="font-weight:600">${esc(state.me?.user ?? "…")}</div>
      <div class="small muted">Changes you make are recorded under this name.</div>
      <button class="btn text danger" data-signout style="margin-top:8px">Sign out of this browser</button></div>
  </div>`;
  $("[data-signout]", ctx.view).onclick = async () => {
    if (!await confirmSheet("Sign out?", "You'll need your access key to sign in again on this phone.", "Sign out")) return;
    localStorage.removeItem(TOKEN_KEY);
    state.me = null;
    location.hash = "#/";
    route();
  };
}

async function reviewScreen(ctx) {
  const m = state.reviewMonth;
  const r = await api(`/review?month=${m}`);
  if (!ctx.alive()) return;
  const comparison = (label, now, before) => `<div class="stat"><div class="k muted">${label}</div>
    <div class="v">${before > 0 ? `${now >= before ? "↗" : "↘"} ${signedPct((now - before) / before * 100)}` : "—"}</div></div>`;
  const summary = `<div class="card"><div class="small muted">Spent in ${esc(monthTitle(r.month))}</div>
    <div style="font-size:32px;font-weight:700">${money(r.total_spent)}</div>
    ${r.total_budget > 0 ? progress(r.total_spent, r.total_spent, r.total_budget, { over: r.total_spent > r.total_budget, warn: r.total_spent > r.total_budget * 0.8 }) +
      `<div style="margin-top:8px">${r.total_spent <= r.total_budget ? `${money(r.total_budget - r.total_spent)} under` : `${money(r.total_spent - r.total_budget)} over`} the ${money(r.total_budget)} budget</div>` : ""}
    <div class="stats">${comparison("vs last month", r.total_spent, r.previous_total)}${comparison("vs 3-month average", r.total_spent, r.average_3m)}</div></div>`;

  const check = r.needs_check.length ? `<div class="card flush"><div style="padding:16px 16px 4px"><h3>Needs a look (${r.needs_check.length})</h3>
    <div class="small muted">Guesses Claude wasn't sure about. Tap to confirm or fix.</div></div><div class="list">
    ${r.needs_check.map(t => `<a class="item" href="#/txn/${t.id}">${badge(t.budget_line, "sm")}
      <div class="grow"><div class="ellipsis">${esc(merchant(t))}</div><div class="sub">Guessed ${esc(t.budget_line)} · ${pct(t.inference_confidence * 100)} sure</div></div>
      <span class="amt">${money(t.amount)}</span></a>`).join("")}</div></div>` : "";

  const spending = r.lines.filter(l => l.spent !== 0 || l.monthly_limit != null);
  const scale = Math.max(1, ...spending.map(l => Math.max(l.spent, l.monthly_limit || 0)));
  const unused = spending.filter(l => l.spent <= 0 && l.monthly_limit != null);
  const where = `<div class="card"><h3>Where it went</h3><div class="small muted">Bars against each budget (the tick). Change is vs your 3-month average.</div>
    ${spending.filter(l => l.spent > 0).map(l => {
      const over = l.monthly_limit != null && l.spent > l.monthly_limit;
      const usual = l.average_3m > 0 ? `${signedPct((l.spent - l.average_3m) / l.average_3m * 100)} vs usual ${money(l.average_3m)}` : "new this month";
      return `<div style="padding:10px 0;border-bottom:0.5px solid var(--line)"><div class="row">${badge(l.budget_line, "sm")}
        <div class="grow"><div>${esc(l.budget_line)}</div><div class="small ${over ? "err" : "muted"}">${esc(usual)}${over ? " · over budget" : ""}</div></div>
        <div class="right"><div class="amt">${money(l.spent)}</div>${l.monthly_limit != null ? `<div class="small muted">of ${money(l.monthly_limit)}</div>` : ""}</div></div>
        <div class="value-bar"><span class="${over ? "over" : ""}" style="width:${clamp01(l.spent / scale) * 100}%"></span>
          ${l.monthly_limit != null ? `<b style="left:calc(${clamp01(l.monthly_limit / scale) * 100}% - 1px)"></b>` : ""}</div></div>`;
    }).join("")}
    ${unused.length ? `<div class="small muted" style="margin-top:8px">Nothing spent: ${esc(unused.map(l => l.budget_line).join(", "))}</div>` : ""}</div>`;

  const merchants = r.top_merchants.length ? `<div class="card"><h3>Top merchants</h3>${r.top_merchants.map((m, i) =>
    `<div class="row" style="padding:6px 0"><span class="muted" style="width:20px">${i + 1}</span><span class="grow ellipsis">${esc(m.merchant)}</span>
      <span class="small muted">${m.count}×</span><span class="amt">${money(m.spent)}</span></div>`).join("")}</div>` : "";

  const statements = `<div class="card"><h3>Statements</h3><div class="small muted">Import each card's statement to catch anything the alerts missed.</div>
    ${r.accounts.map(a => `<div class="row" style="padding:8px 0"><span>${a.statement_imported ? "✅" : "⚠️"}</span>
      <div class="grow"><div>${esc(a.account_name)}</div><div class="small muted">${a.statement_imported ? "Statement imported" : "No statement imported yet"} · ${plural(a.transactions, "charge")}</div></div>
      <span class="amt">${money(a.spent)}</span></div>`).join("")}
    ${r.accounts.some(a => !a.statement_imported) ? `<a class="link" href="/import">Import a statement</a>` : ""}
    ${r.unmatched_receipts > 0 ? `<div class="small" style="margin-top:8px">🧾 ${plural(r.unmatched_receipts, "receipt")} still waiting for their card charge</div>` : ""}</div>`;

  ctx.view.innerHTML = monthBar(m) + `<div class="stack">${summary}${check}${where}${merchants}${statements}</div>`;
  bindMonthBar(ctx.view, "reviewMonth");
}

async function budgetsScreen(ctx) {
  const [tax, budgets, summary] = await Promise.all([taxonomy(true), api("/budgets"), api(`/summary?month=${thisMonth()}`)]);
  if (!ctx.alive()) return;
  const byLine = Object.fromEntries(budgets.map(b => [b.category, b]));
  const spendBy = Object.fromEntries(summary.categories.map(c => [c.category, c]));
  const rows = tax.budget_lines.map(line => ({ line, budget: byLine[line], spend: spendBy[line] }));
  const total = budgets.reduce((s, b) => s + b.monthly_limit, 0);
  const rowHtml = r => `<div class="item" data-line="${esc(r.line)}">${badge(r.line, "sm")}
    <div class="grow"><div>${esc(r.line)}</div><div class="sub">${money(r.spend?.spent ?? 0)} spent this month${r.spend?.expected > 0 ? ` · +${money(r.spend.expected)} expected` : ""}</div></div>
    ${r.budget ? `<div class="right"><div class="amt">${money(r.budget.monthly_limit)}</div><div class="small muted">warn at ${Math.round(r.budget.warning_threshold_percent)}%</div></div>`
      : `<span class="link small">Set budget</span>`}</div>`;
  const shared = rows.filter(r => !(r.line in tax.person_lines)), people = rows.filter(r => r.line in tax.person_lines);
  ctx.view.innerHTML = `<div class="card"><h3>Monthly budgets total ${money(total)}</h3>
      <div class="small muted">Tap a line to set its monthly limit. The Android app gets a notification at the warning level and when it's exceeded.</div></div>
    <div class="section-label">Shared</div><div class="card flush list">${shared.map(rowHtml).join("")}</div>
    <div class="section-label">People</div><div class="card flush list">${people.map(rowHtml).join("")}</div>`;
  $$("[data-line]", ctx.view).forEach(el => el.onclick = () => budgetEditor(rows.find(r => r.line === el.dataset.line)));
}

function budgetEditor(row) {
  const sheet = openSheet(`<h2>${esc(row.line)}</h2>
    <div class="stack">
      <label class="field">Monthly limit ($)<input inputmode="decimal" data-limit value="${row.budget ? Number(row.budget.monthly_limit).toFixed(2) : ""}"></label>
      <label class="field">Warn at (% of limit)<input inputmode="numeric" data-warn value="${Math.round(row.budget?.warning_threshold_percent ?? 80)}"></label>
      ${row.spend ? `<div class="small muted">This month: ${money(row.spend.spent)} spent${row.spend.expected > 0 ? `, ${money(row.spend.projected)} projected` : ""}</div>` : ""}
      <p class="err" data-error></p>
      <button class="btn" data-save>Save</button>
      ${row.budget ? `<button class="btn text danger" data-remove>Remove budget</button>` : ""}
      <button class="btn text" data-close>Cancel</button>
    </div>`);
  const limit = $("[data-limit]", sheet), warn = $("[data-warn]", sheet), save = $("[data-save]", sheet);
  const valid = () => toCents(limit.value) > 0 && Number(warn.value) >= 1 && Number(warn.value) <= 100;
  limit.oninput = warn.oninput = () => { save.disabled = !valid(); };
  save.disabled = !valid();
  const run = async fn => {
    try { await fn(); closeSheet(); route(); } catch (e) { $("[data-error]", sheet).textContent = e.message; }
  };
  save.onclick = () => run(() => api(`/budgets/${enc(row.line)}`, {
    method: "PUT", body: { monthly_limit: centsText(toCents(limit.value)), warning_threshold_percent: Number(warn.value) },
  }));
  $("[data-remove]", sheet)?.addEventListener("click", () => run(() => api(`/budgets/${enc(row.line)}`, { method: "DELETE" })));
}

async function alertsScreen(ctx) {
  const alerts = await api("/alerts");
  if (!ctx.alive()) return;
  ctx.view.innerHTML = alerts.length ? `<div class="card flush list">${alerts.map(a => {
    const exceeded = a.level === "EXCEEDED";
    const inner = `<span style="font-size:20px">${exceeded ? "🔴" : "🟠"}</span><div class="grow">
      <div>${exceeded ? `Over budget: ${esc(a.budget_line)}` : `${esc(a.budget_line)} at ${pct(a.spent / a.monthly_limit * 100)}`}</div>
      <div class="sub">${money(a.spent)} of ${money(a.monthly_limit)} · ${esc(fmtWhen(a.created_at))}</div></div>`;
    return a.transaction_id ? `<a class="item" href="#/txn/${a.transaction_id}">${inner}</a>` : `<div class="item static">${inner}</div>`;
  }).join("")}</div>` : `<p class="muted center">No budget alerts yet. Set budgets per budget line to get warned as you near them.</p>`;
}

function categoryError(e) {
  if (e.status === 409) return "There's already a category with that name.";
  if (e.status === 422) return "That name can't be used (it may be a person's budget line or contain '/').";
  return e.message;
}

async function categoriesScreen(ctx) {
  const [cats, tax] = await Promise.all([api("/categories"), taxonomy()]);
  if (!ctx.alive()) return;
  ctx.view.innerHTML = `<div class="stack">
    <div class="small muted">What money is spent on. Who it was for (${esc(tax.purposes.join(", "))}) stays fixed.</div>
    <button class="btn secondary" data-add>+ Add category</button>
    <div class="card flush list">${cats.map(c => `<div class="item" data-name="${esc(c.name)}">${badge(c.name)}
      <div class="grow"><div>${esc(c.name)}</div><div class="sub">${plural(c.transactions, "transaction")}${c.monthly_limit != null ? ` · budget ${money(c.monthly_limit)}` : ""}</div></div>
      <span class="muted">›</span></div>`).join("")}</div></div>`;
  const done = async (fn, message) => {
    try { await fn(); closeSheet(); toast(message); await taxonomy(true); route(); }
    catch (e) { $("[data-error]", $("#sheet-root")).textContent = categoryError(e); }
  };
  $("[data-add]", ctx.view).onclick = () => {
    const sheet = openSheet(`<h2>New category</h2><div class="stack">
      <label class="field">Name<input data-new autocomplete="off"></label><p class="err" data-error></p>
      <button class="btn" data-save disabled>Save</button><button class="btn text" data-close>Cancel</button></div>`);
    const input = $("[data-new]", sheet), save = $("[data-save]", sheet);
    input.oninput = () => { save.disabled = !input.value.trim(); };
    save.onclick = () => done(() => api("/categories", { method: "POST", body: { name: input.value.trim() } }), "Category added");
    input.focus();
  };
  $$("[data-name]", ctx.view).forEach(el => el.onclick = () => {
    const c = cats.find(x => x.name === el.dataset.name);
    const others = cats.map(x => x.name).filter(n => n !== c.name);
    let mode = "rename";
    const sheet = openSheet(`<h2>${esc(c.name)}</h2><div class="stack">
      <div class="seg"><button class="on" data-mode="rename">Rename</button><button data-mode="merge">Merge into another</button></div>
      <div data-body></div><p class="err" data-error></p>
      <button class="btn" data-go disabled>Rename</button><button class="btn text" data-close>Cancel</button></div>`);
    const body = $("[data-body]", sheet), go = $("[data-go]", sheet);
    const draw = () => {
      $$("[data-mode]", sheet).forEach(b => b.classList.toggle("on", b.dataset.mode === mode));
      go.textContent = mode === "rename" ? "Rename" : "Merge";
      go.disabled = true;
      if (mode === "rename") {
        body.innerHTML = `<label class="field">New name<input data-rename value="${esc(c.name)}" autocomplete="off"></label>
          <div class="small muted" style="margin-top:6px">Its ${plural(c.transactions, "transaction")}, rules, budget and bills move to the new name.</div>`;
        const input = $("[data-rename]", body);
        input.oninput = () => { go.disabled = !input.value.trim() || input.value.trim() === c.name; };
      } else {
        body.innerHTML = `<div class="small muted">Removes ${esc(c.name)}; its ${plural(c.transactions, "transaction")}, rules and bills move to:</div>
          <select class="input" data-into><option value="">Choose a category…</option>${others.map(o => `<option>${esc(o)}</option>`).join("")}</select>`;
        const into = $("[data-into]", body);
        into.onchange = () => { go.disabled = !into.value; };
      }
    };
    $$("[data-mode]", sheet).forEach(b => b.onclick = () => { mode = b.dataset.mode; draw(); });
    go.onclick = () => {
      if (mode === "rename") {
        const name = $("[data-rename]", body).value.trim();
        done(() => api(`/categories/${enc(c.name)}/rename`, { method: "POST", body: { name } }), `Renamed to ${name}`);
      } else {
        const into = $("[data-into]", body).value;
        done(() => api(`/categories/${enc(c.name)}?into=${enc(into)}`, { method: "DELETE" }), `Merged into ${into}`);
      }
    };
    draw();
  });
}

function signInScreen(view) {
  $("#tabs").hidden = true;
  $("#back").hidden = true;
  $("#refresh").hidden = true;
  $("#title").textContent = "Xpensimate";
  view.innerHTML = `<div class="stack" style="padding-top:24px">
    <div class="center" style="padding:16px 0"><div style="font-size:48px">💸</div>
      <h2 style="margin:8px 0 4px">Welcome to Xpensimate</h2>
      <div class="muted">Your household's spending, budgets and receipts.</div></div>
    <form class="card stack" data-form>
      <label class="field">Your access key<input type="password" data-key autocomplete="current-password" autocapitalize="off" spellcheck="false" required></label>
      <div class="small muted">Each person has their own key, so changes show who made them. It stays on this phone only.</div>
      <p class="err" data-error></p>
      <button class="btn">Sign in</button>
    </form></div>`;
  $("[data-form]", view).onsubmit = async e => {
    e.preventDefault();
    const key = $("[data-key]", view).value.trim();
    if (!key) return;
    localStorage.setItem(TOKEN_KEY, key);
    try {
      state.me = await api("/me");
      toast(`Signed in as ${state.me.user}`);
      route();
    } catch (err) {
      localStorage.removeItem(TOKEN_KEY);
      // A rejected key re-routes to this screen; show why once it's back.
      setTimeout(() => { const el = $("[data-error]"); if (el) el.textContent = err.status === 401 ? "That key wasn't accepted." : err.message; }, 0);
    }
  };
}

// ---------- router ----------
const ROUTES = [
  { re: /^\/$/, screen: homeScreen, title: "Xpensimate", tab: "home" },
  { re: /^\/transactions$/, screen: transactionsScreen, title: "Spending", tab: "transactions" },
  { re: /^\/receipts$/, screen: receiptsScreen, title: "Receipts", tab: "receipts" },
  { re: /^\/bills$/, screen: billsScreen, title: "Bills", tab: "bills" },
  { re: /^\/more$/, screen: moreScreen, title: "More", tab: "more" },
  { re: /^\/line\/([^/]+)\/(\d{4}-\d{2})$/, screen: lineScreen, title: "" },
  { re: /^\/txn\/([0-9a-f-]+)$/, screen: transactionScreen, title: "Transaction" },
  { re: /^\/receipt\/([0-9a-f-]+)$/, screen: receiptScreen, title: "Receipt" },
  { re: /^\/review$/, screen: reviewScreen, title: "Month review", parent: "more" },
  { re: /^\/budgets$/, screen: budgetsScreen, title: "Budgets", parent: "more" },
  { re: /^\/alerts$/, screen: alertsScreen, title: "Budget alerts", parent: "more" },
  { re: /^\/categories$/, screen: categoriesScreen, title: "Categories", parent: "more" },
];

let renderSeq = 0;
let leaveHandlers = [];
let lastRender = 0;

async function route() {
  const seq = ++renderSeq;
  leaveHandlers.forEach(fn => fn());
  leaveHandlers = [];
  closeSheet();
  const view = $("#view");
  if (!token()) return signInScreen(view);

  const [path, queryString = ""] = (location.hash.slice(1) || "/").split("?");
  let match = null, r;
  for (r of ROUTES) if ((match = path.match(r.re))) break;
  if (!match) { location.replace("#/"); return; }
  // Detail screens keep the tab they were opened from highlighted.
  if (r.tab) state.tab = r.tab;
  else if (r.parent) state.tab = r.parent;
  $$("#tabs a").forEach(a => a.classList.toggle("on", a.dataset.tab === state.tab));
  $("#tabs").hidden = false;
  $("#refresh").hidden = false;
  $("#back").hidden = !!r.tab;
  $("#title").textContent = r.title;
  document.title = r.title && r.title !== "Xpensimate" ? `${r.title} · Xpensimate` : "Xpensimate";

  const ctx = {
    view,
    query: new URLSearchParams(queryString),
    alive: () => seq === renderSeq,
    title: t => { if (seq === renderSeq) { $("#title").textContent = t; document.title = `${t} · Xpensimate`; } },
    onLeave: fn => leaveHandlers.push(fn),
  };
  view.innerHTML = loadingHtml;
  if (r.tab) window.scrollTo(0, 0);
  const refresh = $("#refresh");
  refresh.classList.add("spin");
  try {
    await taxonomy();  // badges for person lines need it
    await r.screen(ctx, ...match.slice(1).map(decodeURIComponent));
    lastRender = Date.now();
  } catch (e) {
    if (!ctx.alive() || e.status === 401) return;
    view.innerHTML = `<div class="center"><p>${esc(e.message)}</p><button class="btn secondary" data-retry style="max-width:200px;margin:0 auto">Retry</button></div>`;
    $("[data-retry]", view).onclick = route;
  } finally {
    if (ctx.alive()) refresh.classList.remove("spin");
  }
}

$("#back").onclick = () => {
  if (history.length > 1) history.back();
  else location.hash = state.tab === "home" ? "#/" : `#/${state.tab}`;
};
$("#refresh").onclick = () => { state.taxonomy = null; route(); };
// Tapping the tab you're on reloads it (and returns a month-browsing screen to this month).
$$("#tabs a").forEach(a => a.addEventListener("click", e => {
  const target = a.getAttribute("href");
  if (location.hash === target || (target === "#/" && !location.hash)) { e.preventDefault(); route(); }
}));
window.addEventListener("hashchange", route);
// Coming back to the app after a while (e.g. from the home screen icon): show fresh numbers.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && token() && !$("#sheet-root").children.length && Date.now() - lastRender > 60_000) route();
});
route();
