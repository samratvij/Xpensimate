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
// Amounts show in the household's currency (from the server; remembered so the first screen is right too).
let moneyFormat;
function setCurrency(code) {
  try {
    moneyFormat = new Intl.NumberFormat(undefined, { style: "currency", currency: code, currencyDisplay: "narrowSymbol" });
    localStorage.setItem("xpensimate.currency", code);
  } catch { moneyFormat ??= new Intl.NumberFormat(undefined, { style: "currency", currency: "CAD", currencyDisplay: "narrowSymbol" }); }
}
setCurrency(localStorage.getItem("xpensimate.currency") || "CAD");
const money = n => moneyFormat.format(Number(n) || 0);
const currencyName = code => { try { return new Intl.DisplayNames(undefined, { type: "currency" }).of(code); } catch { return code; } };
// Common currencies first, then every other one the browser knows.
const COMMON_CURRENCIES = ["CAD", "USD", "EUR", "GBP", "INR", "AUD", "NZD", "CHF", "JPY", "SGD", "AED", "MXN", "BRL", "ZAR"];
const allCurrencies = () => [...new Set([...COMMON_CURRENCIES, ...(Intl.supportedValuesOf?.("currency") ?? [])])];
// A first guess for a new household, from the browser's region.
const REGION_CURRENCY = { CA: "CAD", US: "USD", GB: "GBP", IN: "INR", AU: "AUD", NZ: "NZD", CH: "CHF", JP: "JPY", SG: "SGD",
  AE: "AED", MX: "MXN", BR: "BRL", ZA: "ZAR", IE: "EUR", DE: "EUR", FR: "EUR", ES: "EUR", IT: "EUR", NL: "EUR", BE: "EUR",
  AT: "EUR", PT: "EUR", FI: "EUR", GR: "EUR" };
const guessCurrency = () => { try { return REGION_CURRENCY[new Intl.Locale(navigator.language).maximize().region]; } catch { return undefined; } };
const pct = n => `${Math.round(n)}%`;
const signedPct = change => `${change >= 0 ? "+" : "−"}${Math.abs(Math.round(change))}%`;
const clamp01 = x => Math.max(0, Math.min(1, x || 0));
const badge = (name, size = "") => `<span class="badge ${size}" aria-hidden="true">${ICONS[name] ?? PERSON_ICONS[personLines()[name]] ?? (name ? "❔" : "↔️")}</span>`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const merchant = t => t.merchant_normalized || t.merchant_raw;
const isIncome = t => t.txn_type === "INCOME";
const isTransfer = t => t.txn_type === "TRANSFER";
const accountName = id => state.accounts?.find(a => a.id === id)?.account_name ?? id;
/** "PC Money Account → PC Savings"; an unknown side reads as someone in the household or own money. */
const moveText = t => `${t.account_id ? accountName(t.account_id) : "Own money"} → ${t.counterpart_account_id ? accountName(t.counterpart_account_id) : (state.taxonomy?.people ?? []).join(" / ") || "Someone in the household"}`;
const INCOME_KINDS = ["Salary", "Other income"];
// Who income can belong to: the household's people, or Shared. Needs the taxonomy loaded.
const earners = () => [...(state.taxonomy?.people ?? []), "Shared"];
/** A guess worth a glance: picked by Claude (or nothing) with low confidence, never confirmed. Same rule as Android. */
const needsCheck = t => !t.excluded && !isIncome(t) && !t.category_is_manual && !t.purpose_is_manual &&
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

/** "12.5", "$12.50", "€12.50" or "1,200" as cents; null unless a whole number of cents. */
function toCents(text) {
  const s = String(text).trim().replace(/[^\d.,\-]/g, "").replace(/,/g, "");
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
  incomeMonth: thisMonth(),
  // Month-end review defaults to the month that just ended.
  reviewMonth: shiftMonth(thisMonth(), -1),
  balancesMonth: thisMonth(),
  insightsExpanded: false,
  tab: "home",
  // A chat answer is streaming: don't re-render the screen under it.
  chatBusy: false,
};

async function accounts() {
  if (!state.accounts) state.accounts = await api("/accounts").catch(() => []);
  return state.accounts;
}

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
  const income = isIncome(t);
  const sub = income ? `${t.category} · ${t.purpose}` : isTransfer(t) ? `Moved: ${moveText(t)}` : t.excluded ? "Not an expense"
    : t.is_itemized ? `${t.budget_line} · itemized` : t.budget_line;
  const amount = income || Number(t.amount) < 0
    ? `<span class="amt refund">+${money(Math.abs(t.amount))}</span>` : `<span class="amt">${money(t.amount)}</span>`;
  return `<a class="item ${t.excluded && !income ? "dim" : ""}" href="#/txn/${t.id}">
    ${income ? `<span class="badge" aria-hidden="true">💰</span>` : badge(t.excluded ? "" : t.category)}
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
  const [summary, trend, tax, insights, worth] = await Promise.all([
    api(`/summary?month=${m}${q}`),
    api(`/trends?months=${TREND_MONTHS}${q}`).catch(() => []),
    taxonomy(),
    // Insights are about the current month only.
    m === thisMonth() ? api(`/insights${who ? `?purpose=${enc(who)}` : ""}`).catch(() => []) : [],
    who ? null : api(`/balances?month=${m}`).catch(() => null),
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
      ${summary.total_bills_due > 0 ? `<div class="stat"><div class="k">Bills still due</div><div class="v">${money(summary.total_bills_due)}</div></div>` : ""}
      ${summary.total_estimated > 0 ? `<div class="stat"><div class="k">Usual spend to come</div><div class="v">${money(summary.total_estimated)}</div></div>` : ""}
      ${summary.total_expected > 0 ? `<div class="stat"><div class="k">Projected</div><div class="v">${money(projected)}</div></div>` : ""}
      ${compare}
    </div></div>`;

  const inc = summary.income, sav = summary.savings;
  let incomeCard = "";
  if (inc && (inc.received > 0 || inc.expected > 0)) {
    const people = Object.entries(inc.by_person);
    const projectedLeft = current && inc.expected > 0;
    const left = projectedLeft ? inc.projected_left : inc.left_after_spending;
    incomeCard = `<a class="card" href="#/income" data-income style="display:block;color:inherit">
      <div class="row"><span class="badge" aria-hidden="true">💰</span><div class="grow"><div style="font-weight:600">Income</div>
        <div class="small muted">${esc(people.map(([p, a]) => `${p} ${money(a)}`).join(" · ") || "No pay received yet")}</div></div>
        ${inc.received > 0 ? `<span class="amt refund">+${money(inc.received)}</span>` : `<span class="amt muted">${money(0)}</span>`}</div>
      <div class="stats" style="margin-top:6px">
        ${inc.expected > 0 ? `<div class="stat"><div class="k">Pay still expected</div><div class="v">${money(inc.expected)}</div></div>` : ""}
        <div class="stat"><div class="k">${projectedLeft ? "Left after spending (projected)" : "Left after spending"}</div>
          <div class="v ${left < 0 ? "danger" : ""}">${left < 0 ? "−" : ""}${money(Math.abs(left))}</div></div>
        ${sav && sav.rate != null ? `<div class="stat"><div class="k">Savings rate</div><div class="v ${sav.rate < 0 ? "danger" : ""}">${sav.rate}%</div></div>` : ""}
      </div>
      ${sav && (sav.to_savings || sav.from_savings || sav.to_investments || sav.from_investments) ? `<div class="small muted" style="margin-top:6px">
        Moved: ${[sav.net_to_savings ? `${sav.net_to_savings < 0 ? "−" : ""}${money(Math.abs(sav.net_to_savings))} to savings` : "",
                  sav.net_to_investments ? `${sav.net_to_investments < 0 ? "−" : ""}${money(Math.abs(sav.net_to_investments))} to investments` : ""]
                 .filter(Boolean).join(" · ") || "in and out evenly"}</div>` : ""}</a>`;
  } else if (!who || earners().includes(who)) {
    incomeCard = `<a class="card small muted" href="#/income" data-income style="display:block">💰 No income recorded for this month yet ›</a>`;
  }

  let trendCard = "";
  if (trend.length) {
    const peak = Math.max(1, ...trend.map(t => t.spent + t.expected));
    const shown = trend.find(t => t.month === m);
    trendCard = `<div class="card"><h3>Last ${TREND_MONTHS} months</h3>
      <div class="small muted">${shown ? `${esc(monthTitle(m))}: ${money(shown.spent)} spent${shown.expected > 0 ? ` + ${money(shown.expected)} still expected` : ""}` : "Tap a month to open it"}</div>
      <div class="trend" role="img" aria-label="${esc(trend.map(t => `${monthShort(t.month)} ${money(t.spent)}`).join(", "))}">
        ${trend.map(t => `<button data-month="${t.month}" aria-label="${esc(monthTitle(t.month))}">
          ${t.expected > 0 ? `<span class="bar due" style="height:${t.expected / peak * 100}%"></span>` : ""}
          ${t.spent > 0 ? `<span class="bar" style="height:${t.spent / peak * 100}%"></span>` : ""}</button>`).join("")}
      </div>
      <div class="trend-labels">${trend.map(t => `<span class="${t.month === m ? "on" : ""}">${esc(monthShort(t.month))}</span>`).join("")}</div>
      ${trend.some(t => t.expected > 0) ? `<div class="legend"><span><i></i>Spent</span><span><i class="due"></i>Still expected</span></div>` : ""}
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
    notes.push(...stillToCome(l));
    if (l.monthly_limit) notes.push(`${pct(l.spent / l.monthly_limit * 100)} of ${money(l.monthly_limit)}`);
    let status = "";
    if (l.monthly_limit) {
      if (l.over_limit) status = `<div class="note over">Over budget by ${money(l.spent - l.monthly_limit)}</div>`;
      else if (l.projected_over_limit) status = `<div class="note">On track to go over with what's still to come</div>`;
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

  ctx.view.innerHTML = monthBar(m) + `<div class="stack">${insightsSection(insights)}${hero}${incomeCard}${netWorthCard(worth)}${trendCard}${chips}${lineCards}</div>`;
  bindMonthBar(ctx.view, "homeMonth");
  bindInsights(ctx.view, insights, m, who);
  $$("[data-income]", ctx.view).forEach(a => a.onclick = () => { state.incomeMonth = m; });
  $$("[data-worth]", ctx.view).forEach(a => a.onclick = () => { state.balancesMonth = m; });
  $$("[data-month]", ctx.view).forEach(b => b.onclick = () => { state.homeMonth = b.dataset.month; route(); });
  $$("[data-who]", ctx.view).forEach(b => b.onclick = () => { state.purpose = b.dataset.who || null; route(); });
}

// ---------- Worth a look (same cards as Android) ----------
const INSIGHTS_SHOWN = 3;
const INSIGHT_ICONS = { alert: "⛔", warn: "⚠️", info: "💡" };

/** What to do about this month, most pressing first. Nothing when the server has nothing to say. */
function insightsSection(insights) {
  if (!insights.length) return "";
  const shown = state.insightsExpanded ? insights : insights.slice(0, INSIGHTS_SHOWN);
  const more = insights.length - INSIGHTS_SHOWN;
  return `<div class="insights"><div class="section-label" style="margin-top:0">Worth a look</div>
    ${shown.map(i => `<div class="card insight ${esc(i.severity)}" data-insight="${esc(i.key)}" role="button" tabindex="0">
      <span class="insight-icon" aria-label="${esc(i.severity)}">${INSIGHT_ICONS[i.severity] ?? INSIGHT_ICONS.info}</span>
      <div class="grow"><div class="insight-title">${esc(i.title)}</div><div class="small muted">${esc(i.body)}</div>
        ${i.action === "track_recurring" && i.recurring ? `<button class="link" data-track>Track as a bill</button>` : ""}
        ${i.action === "remove_hold" && i.transaction_id ? `<button class="link" data-hold>Not a charge</button>` : ""}</div>
      <button class="icon-btn insight-x" data-dismiss aria-label="Dismiss"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
    </div>`).join("")}
    ${more > 0 ? `<button class="btn text" data-insights-more>${state.insightsExpanded ? "Show fewer" : `Show ${more} more`}</button>` : ""}
  </div>`;
}

function bindInsights(view, insights, month, who) {
  const byKey = new Map(insights.map(i => [i.key, i]));
  const track = async i => {
    try { const r = await api("/recurring", { method: "POST", body: i.recurring }); toast(`Added ${r.name} to Bills`); route(); }
    catch (e) { toast(e.message); }
  };
  const open = i => {
    if (i.action === "track_recurring") return track(i);
    if (i.action === "open_line" && i.line) location.hash = `#/line/${enc(i.line)}/${month}${who ? `?purpose=${enc(who)}` : ""}`;
    else if (i.action === "open_transaction" || i.action === "remove_hold") { if (i.transaction_id) location.hash = `#/txn/${i.transaction_id}`; }
    else if (i.action === "open_bills") { state.billsMonth = month; location.hash = "#/bills"; }
  };
  $$("[data-insight]", view).forEach(card => {
    const i = byKey.get(card.dataset.insight);
    card.onclick = () => open(i);
    card.onkeydown = e => { if (e.target === card && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); open(i); } };
    $("[data-dismiss]", card).onclick = async e => {
      e.stopPropagation();
      card.remove();
      try { await api("/insights/dismiss", { method: "POST", body: { key: i.key } }); route(); }
      catch (err) { toast(err.message); route(); }
    };
    const trackBtn = $("[data-track]", card);
    if (trackBtn) trackBtn.onclick = e => { e.stopPropagation(); track(i); };
    const holdBtn = $("[data-hold]", card);
    if (holdBtn) holdBtn.onclick = async e => {
      e.stopPropagation();
      card.remove();
      try {
        const t = await api(`/transactions/${i.transaction_id}`, { method: "PATCH", body: { excluded: true } });
        toast(`Marked ${merchant(t)} ${money(t.amount)} as not a transaction`);
      } catch (err) { toast(err.message); }
      route();
    };
  });
  const moreBtn = $("[data-insights-more]", view);
  if (moreBtn) moreBtn.onclick = () => { state.insightsExpanded = !state.insightsExpanded; route(); };
}

// What a line still expects this month: bills not yet paid, and the rest of the usual spend on a necessity.
function stillToCome(row) {
  const out = [];
  if (row.bills_due > 0) out.push(`+${money(row.bills_due)} bills due`);
  if (row.estimated > 0) out.push(`~${money(row.estimated)} more at the usual pace`);
  return out;
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
    ${spend?.expected > 0 ? `<div class="small muted" style="margin-top:8px">${esc([...stillToCome(spend), `${money(spend.projected)} projected`].join(" · "))}</div>` : ""}
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

  function drawIncome() {
    ctx.title(merchant(txn));
    ctx.view.innerHTML = `<div class="stack">
      <div class="detail-head">
        <div class="merchant">${esc(merchant(txn))}</div>
        <div class="total refund">+${money(txn.amount)}</div>
        <div class="muted">${esc(fmtFull(txn.timestamp))}${txn.account_id ? ` · ${esc(txn.account_id)}` : ""}</div>
        ${txn.edited_by ? `<div class="small muted">Last changed by ${esc(txn.edited_by)}</div>` : ""}
      </div>
      <div><span class="chip" style="cursor:default">Income · not spending</span></div>
      ${error ? `<div class="err">${esc(error)}</div>` : ""}
      <div class="card"><h3 style="margin-bottom:8px">Note</h3><div data-note></div></div>
      <div class="card stack">
        <h3>Kind</h3><div class="chips">${INCOME_KINDS.map(c =>
          `<button class="chip ${txn.category === c ? "on" : ""}" data-cat="${esc(c)}" ${saving ? "disabled" : ""}>${esc(c)}</button>`).join("")}</div>
        <h3>Who earned it</h3><div class="chips">${earners().map(p =>
          `<button class="chip ${txn.purpose === p ? "on" : ""}" data-who="${esc(p)}" ${saving ? "disabled" : ""}>${esc(p)}</button>`).join("")}</div>
      </div></div>`;
    $$("[data-cat]", ctx.view).forEach(b => b.onclick = () => { if (txn.category !== b.dataset.cat) save({ category: b.dataset.cat }); });
    $$("[data-who]", ctx.view).forEach(b => b.onclick = () => { if (txn.purpose !== b.dataset.who) save({ purpose: b.dataset.who }); });
    noteBox($("[data-note]", ctx.view), txn.note, async text => {
      txn = await api(`/transactions/${id}`, { method: "PATCH", body: { note: text } });
      toast(text ? "Note saved" : "Note removed");
      if (ctx.alive()) draw();
    });
  }

  function draw() {
    if (isIncome(txn)) return drawIncome();
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
      ${isTransfer(txn) ? `<div><span class="chip" style="cursor:default">Moved: ${esc(moveText(txn))} · not spending</span></div>`
        : txn.excluded ? `<div class="err" style="font-weight:600">Not counted as an expense</div>`
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
        ${isTransfer(txn) ? "Not a transfer: count as spending" : txn.excluded ? "Count as an expense again" : "Not an expense (transfer, card payment…)"}</button>
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
  502: "The receipt couldn't be read right now. Try again in a minute.",
  503: "Receipt reading isn't set up on the server (no Claude API key or local model).",
};

async function uploadReceipt(file) {
  openSheet(`<h2>Reading receipt…</h2><div class="row"><div class="spinner" style="margin:12px 0"></div>
    <p class="muted">Reading the lines. This can take up to a minute.</p></div>`, { locked: true });
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

const PAY_STATUS = { received: `<div class="small ok">✓ Received</div>`, upcoming: `<div class="small muted">Upcoming</div>`,
                     late: `<div class="small danger">Not seen yet</div>` };

async function incomeScreen(ctx) {
  const m = state.incomeMonth;
  const [inc, accounts, , sources] = await Promise.all([api(`/income?month=${m}`), api("/accounts"), taxonomy(),
                                                         api("/income/sources")]);
  if (!ctx.alive()) return;
  const people = Object.entries(inc.by_person);
  ctx.view.innerHTML = monthBar(m) + `<div class="stack">
    <div class="card row"><div class="grow"><div class="small muted">Received</div><div class="refund" style="font-size:22px;font-weight:600">+${money(inc.received)}</div></div>
      <div class="grow"><div class="small muted">Still expected</div><div style="font-size:22px">${money(inc.expected)}</div></div></div>
    ${people.length ? `<div class="card flush list">${people.map(([p, a]) => `<div class="item static"><span class="badge sm" aria-hidden="true">👤</span>
      <div class="grow">${esc(p)}</div><span class="amt">${money(a)}</span></div>`).join("")}</div>` : ""}
    ${inc.pay_dates.length ? `<div class="section-label"><span>Pay dates</span></div>
      <div class="card flush list">${inc.pay_dates.map(p => `<div class="item static">
        <div class="grow"><div>${esc(p.name)}</div><div class="sub">${esc(fmtShortDay(p.day))}</div></div>
        <div class="right"><div class="amt">${money(p.amount)}</div>${PAY_STATUS[p.status] ?? ""}</div></div>`).join("")}</div>` : ""}
    <div class="section-label"><span>Deposits</span></div>
    ${inc.deposits.length ? `<div class="card flush list">${inc.deposits.map(txRow).join("")}</div>`
      : `<p class="muted center">No income recorded this month.</p>`}
    <button class="btn secondary" data-add>Add income by hand</button>
    <p class="small muted">Pay arrives on its own from bank deposit alerts (Wealthsimple and RBC today) and from screenshots of
      an account's transactions. Add anything else here.</p>
    ${regularPayHtml(sources)}
  </div>`;
  bindMonthBar(ctx.view, "incomeMonth");
  $("[data-add]", ctx.view).onclick = () => addIncome(accounts);
  $("[data-add-source]", ctx.view).onclick = () => sourceSheet(null, accounts);
  $$("[data-source]", ctx.view).forEach(el => el.onclick = () => sourceSheet(sources.find(s => s.id === el.dataset.source), accounts));
}

function addIncome(accounts) {
  const into = accounts.filter(a => a.kind === "chequing" || a.kind === "savings");
  const sheet = openSheet(`<h2>Add income</h2>
    <label class="field">Amount<input inputmode="decimal" data-amount placeholder="0.00"></label>
    <label class="field">From (payer)<input data-payer placeholder="Employer, CRA, …" maxlength="255"></label>
    <label class="field">Date<input type="date" data-date value="${today()}"></label>
    <div class="field">Who earned it<div class="chips">${earners().map((p, i) => `<button class="chip ${i ? "" : "on"}" data-earner="${p}">${p}</button>`).join("")}</div></div>
    <div class="field">Kind<div class="chips">${INCOME_KINDS.map((k, i) => `<button class="chip ${i ? "" : "on"}" data-kind="${k}">${k}</button>`).join("")}</div></div>
    <label class="field">Into account<select class="input" data-account><option value="">Not sure</option>
      ${into.map(a => `<option value="${esc(a.id)}">${esc(a.account_name)}</option>`).join("")}</select></label>
    <p class="err" data-error></p>
    <div class="stack"><button class="btn" data-save disabled>Add</button><button class="btn text" data-close>Cancel</button></div>`);
  let earner = earners()[0], kind = INCOME_KINDS[0];
  const pick = (attr, set) => $$(`[${attr}]`, sheet).forEach(b => b.onclick = () => {
    $$(`[${attr}]`, sheet).forEach(x => x.classList.toggle("on", x === b)); set(b.getAttribute(attr));
  });
  pick("data-earner", v => { earner = v; });
  pick("data-kind", v => { kind = v; });
  const amount = $("[data-amount]", sheet), payer = $("[data-payer]", sheet), saveBtn = $("[data-save]", sheet);
  const check = () => { saveBtn.disabled = !(toCents(amount.value) > 0 && payer.value.trim()); };
  amount.oninput = check; payer.oninput = check;
  saveBtn.onclick = async () => {
    saveBtn.disabled = true;
    try {
      await api("/income", { method: "POST", body: {
        amount: centsText(toCents(amount.value)), payer: payer.value.trim(), received_on: $("[data-date]", sheet).value,
        earner, category: kind, account_id: $("[data-account]", sheet).value || null } });
      closeSheet();
      toast("Income added");
      route();
    } catch (e) { $("[data-error]", sheet).textContent = e.message; saveBtn.disabled = false; }
  };
}

// ---------- regular pay (income sources) ----------
const CADENCE_LABELS = { weekly: "Every week", biweekly: "Every two weeks", semimonthly: "Twice a month (15th and last day)",
                         monthly: "Once a month" };

/** The household's regular paycheques: what tells pay from other deposits, and when it's due. */
function regularPayHtml(sources) {
  return `<div class="section-label"><span>Regular pay</span></div>
    ${sources.length ? `<div class="card flush list">${sources.map(s => `<div class="item" data-source="${esc(s.id)}">
      <span class="badge sm" aria-hidden="true">💼</span>
      <div class="grow"><div>${esc(s.name)}${s.active ? "" : ` <span class="muted">(stopped)</span>`}</div>
        <div class="sub">${esc(s.earner)} · ${money(s.amount)} · ${esc(CADENCE_LABELS[s.cadence] ?? s.cadence)}</div></div>
      <span class="muted">›</span></div>`).join("")}</div>`
      : `<p class="small muted">Add each paycheque, so a deposit from that payer counts as pay and the month shows pay still to come.</p>`}
    <button class="btn secondary" data-add-source>+ Add regular pay</button>`;
}

function sourceSheet(source, accounts) {
  const into = accounts.filter(a => a.kind === "chequing" || a.kind === "savings");
  const s = source ?? { name: "", earner: earners()[0], account_id: null, payer_pattern: "", amount: "", cadence: "biweekly",
                        anchor_date: today(), active: true };
  const sheet = openSheet(`<h2>${source ? esc(source.name) : "Regular pay"}</h2><div class="stack">
    <label class="field">Name<input data-f="name" value="${esc(s.name)}" maxlength="128" placeholder="e.g. Alex's pay"></label>
    <div class="field">Whose pay<div class="chips">${earners().map(p =>
      `<button class="chip ${p === s.earner ? "on" : ""}" data-earner="${esc(p)}">${esc(p)}</button>`).join("")}</div></div>
    <label class="field">Payer, as the deposit names it<input data-f="payer_pattern" value="${esc(s.payer_pattern)}" maxlength="128" placeholder="e.g. ACME PAYROLL"></label>
    <label class="field">Usual amount<input data-f="amount" inputmode="decimal" value="${s.amount ? esc(centsText(toCents(s.amount))) : ""}" placeholder="0.00"></label>
    <label class="field">How often<select class="input" data-f="cadence">${Object.entries(CADENCE_LABELS).map(([k, v]) =>
      `<option value="${k}" ${k === s.cadence ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
    <label class="field">A recent pay day<input type="date" data-f="anchor_date" value="${esc(s.anchor_date)}"></label>
    <label class="field">Paid into<select class="input" data-f="account_id"><option value="">Any account</option>
      ${into.map(a => `<option value="${esc(a.id)}" ${a.id === s.account_id ? "selected" : ""}>${esc(a.account_name)}</option>`).join("")}</select></label>
    ${source ? `<label class="row small"><input type="checkbox" data-active ${s.active ? "checked" : ""}> Still paid</label>` : ""}
    <p class="err" data-error></p>
    <button class="btn" data-save>${source ? "Save" : "Add"}</button>
    ${source ? `<button class="btn danger" data-delete>Remove</button>` : ""}
    <button class="btn text" data-close>Cancel</button></div>`);
  let earner = s.earner;
  $$("[data-earner]", sheet).forEach(b => b.onclick = () => {
    earner = b.dataset.earner; $$("[data-earner]", sheet).forEach(x => x.classList.toggle("on", x === b));
  });
  const field = name => $(`[data-f="${name}"]`, sheet).value.trim();
  $("[data-save]", sheet).onclick = async () => {
    const cents = toCents(field("amount"));
    if (!field("name") || field("payer_pattern").length < 2 || !(cents > 0)) {
      $("[data-error]", sheet).textContent = "Give it a name, the payer (2+ letters) and the usual amount.";
      return;
    }
    const body = { name: field("name"), earner, payer_pattern: field("payer_pattern"), amount: centsText(cents),
                   cadence: field("cadence"), anchor_date: field("anchor_date"), account_id: field("account_id") || null };
    if (source) body.active = $("[data-active]", sheet).checked;
    try {
      await api(source ? `/income/sources/${source.id}` : "/income/sources", { method: source ? "PATCH" : "POST", body });
      closeSheet(); toast(source ? "Saved" : "Regular pay added"); route();
    } catch (e) { $("[data-error]", sheet).textContent = e.message; }
  };
  $("[data-delete]", sheet)?.addEventListener("click", async () => {
    if (!await confirmSheet(`Remove ${source.name}?`, "Pay already recorded stays; new deposits from this payer won't count as pay automatically.", "Remove")) return;
    await api(`/income/sources/${source.id}`, { method: "DELETE" });
    toast("Removed"); route();
  });
}

// ---------- savings target and goals ----------
const GOAL_KINDS = [["savings", "Savings"], ["investment", "Investment"], ["retirement", "Retirement"]];
const TARGET_STATUS = { met: "✓ Met", on_track: "On track", behind: "Behind" };

async function savingsScreen(ctx) {
  const [plan, accounts] = await Promise.all([api("/savings"), api("/accounts")]);
  if (!ctx.alive()) return;
  const t = plan.target, p = plan.progress;
  ctx.view.innerHTML = `<div class="stack">
    <div class="card stack"><h3>Monthly savings target</h3>
      <div class="small muted">How much the household means to move into savings and investments each month. Money moved
        between your own accounts counts; pay left sitting in chequing doesn't.</div>
      ${p ? `<div class="row"><div class="grow"><div class="small muted">${esc(monthTitle(p.month))}</div>
          <div style="font-size:22px;font-weight:600">${money(p.saved)} <span class="small muted">of ${money(p.target)}</span></div></div>
          <span class="small">${esc(TARGET_STATUS[p.status] ?? p.status)}</span></div>
        <div class="progress"><span class="spent" style="width:${Math.min(100, p.percent)}%"></span></div>` : ""}
      <label class="field">Each month<input data-target inputmode="decimal" value="${t ? esc(centsText(toCents(t.monthly_amount))) : ""}" placeholder="e.g. 1000.00"></label>
      <label class="field">Remind me if behind on day<input data-alert-day inputmode="numeric" value="${t?.alert_day ?? 25}" maxlength="2"></label>
      <p class="err" data-target-error></p>
      <button class="btn" data-save-target>${t ? "Save target" : "Set a target"}</button>
    </div>
    <div class="section-label"><span>Goals</span></div>
    ${plan.goals.length ? plan.goals.map(g => `<div class="card stack" data-goal="${esc(g.id)}" role="button" tabindex="0" style="cursor:pointer">
      <div class="row"><div class="grow"><div style="font-weight:600">${esc(g.name)}${g.active ? "" : ` <span class="muted">(paused)</span>`}</div>
        <div class="small muted">${money(g.current)} of ${money(g.target_amount)} by ${esc(fmtDate(`${g.target_date}T12:00:00`))}</div></div>
        <span class="small">${Math.round(g.percent)}%</span></div>
      <div class="progress"><span class="spent" style="width:${Math.min(100, g.percent)}%"></span></div>
      <div class="small muted">${g.required_monthly != null ? `${money(g.required_monthly)} a month would get there` : g.remaining <= 0 ? "Reached 🎉" : "Past its date"}
        ${g.oldest_balance ? ` · balances as of ${esc(fmtDate(`${g.oldest_balance}T12:00:00`))}` : ""}</div>
    </div>`).join("") : `<p class="small muted">A goal is an amount by a date, counted from the balances of the accounts you pick
      (record balances under Balances and net worth).</p>`}
    <button class="btn secondary" data-add-goal>+ Add a goal</button>
  </div>`;
  $("[data-save-target]", ctx.view).onclick = async () => {
    const cents = toCents($("[data-target]", ctx.view).value), day = +$("[data-alert-day]", ctx.view).value;
    if (!(cents >= 0) || !(day >= 1 && day <= 28)) {
      $("[data-target-error]", ctx.view).textContent = "Enter an amount, and a day from 1 to 28.";
      return;
    }
    try {
      await api("/savings/target", { method: "PUT", body: { monthly_amount: centsText(cents), alert_day: day } });
      toast("Target saved"); route();
    } catch (e) { $("[data-target-error]", ctx.view).textContent = e.message; }
  };
  $("[data-add-goal]", ctx.view).onclick = () => goalSheet(null, accounts);
  $$("[data-goal]", ctx.view).forEach(card => card.onclick = () => goalSheet(plan.goals.find(g => g.id === card.dataset.goal), accounts));
}

function goalSheet(goal, accounts) {
  const holding = accounts.filter(a => !["credit_card", "loan"].includes(a.kind));
  const g = goal ?? { name: "", target_amount: "", target_date: `${new Date().getFullYear() + 1}-12-31`, account_kinds: ["savings"],
                      account_ids: [], active: true };
  const kinds = new Set(g.account_kinds), ids = new Set(g.account_ids);
  const sheet = openSheet(`<h2>${goal ? esc(goal.name) : "New goal"}</h2><div class="stack">
    <label class="field">What for<input data-f="name" value="${esc(g.name)}" maxlength="128" placeholder="e.g. House down payment"></label>
    <label class="field">Amount<input data-f="amount" inputmode="decimal" value="${g.target_amount ? esc(centsText(toCents(g.target_amount))) : ""}" placeholder="0.00"></label>
    <label class="field">By<input type="date" data-f="date" value="${esc(g.target_date)}"></label>
    <div class="field">Counts every account of these kinds<div class="chips">${GOAL_KINDS.map(([k, label]) =>
      `<button class="chip ${kinds.has(k) ? "on" : ""}" data-kind="${k}">${label}</button>`).join("")}</div></div>
    <div class="field">…and these accounts<div class="card flush list">${holding.map(a => `<label class="item static">
      <input type="checkbox" data-account="${esc(a.id)}" ${ids.has(a.id) ? "checked" : ""}>
      <div class="grow">${esc(a.account_name)}<div class="sub">${esc(a.owner)}</div></div></label>`).join("")}</div></div>
    ${goal ? `<label class="row small"><input type="checkbox" data-active ${g.active ? "checked" : ""}> Working towards it</label>` : ""}
    <p class="err" data-error></p>
    <button class="btn" data-save>${goal ? "Save" : "Add goal"}</button>
    ${goal ? `<button class="btn danger" data-delete>Remove</button>` : ""}
    <button class="btn text" data-close>Cancel</button></div>`);
  $$("[data-kind]", sheet).forEach(b => b.onclick = () => {
    kinds.has(b.dataset.kind) ? kinds.delete(b.dataset.kind) : kinds.add(b.dataset.kind);
    b.classList.toggle("on", kinds.has(b.dataset.kind));
  });
  $("[data-save]", sheet).onclick = async () => {
    const name = $('[data-f="name"]', sheet).value.trim(), cents = toCents($('[data-f="amount"]', sheet).value);
    const chosen = $$("[data-account]", sheet).filter(c => c.checked).map(c => c.dataset.account);
    if (!name || !(cents > 0)) { $("[data-error]", sheet).textContent = "Give it a name and an amount."; return; }
    if (!kinds.size && !chosen.length) { $("[data-error]", sheet).textContent = "Pick the accounts it's saved in."; return; }
    const body = { name, target_amount: centsText(cents), target_date: $('[data-f="date"]', sheet).value,
                   account_kinds: [...kinds], account_ids: chosen };
    if (goal) body.active = $("[data-active]", sheet).checked;
    try {
      await api(goal ? `/savings/goals/${goal.id}` : "/savings/goals", { method: goal ? "PATCH" : "POST", body });
      closeSheet(); toast(goal ? "Goal saved" : "Goal added"); route();
    } catch (e) { $("[data-error]", sheet).textContent = e.message; }
  };
  $("[data-delete]", sheet)?.addEventListener("click", async () => {
    if (!await confirmSheet(`Remove ${goal.name}?`, "Balances and transfers stay; only the goal goes.", "Remove")) return;
    await api(`/savings/goals/${goal.id}`, { method: "DELETE" });
    toast("Goal removed"); route();
  });
}

// ---------- balances and net worth ----------
const GROUP_TITLES = { savings: "Savings", investments: "Investments", chequing: "Chequing", credit_cards: "Credit cards (owed)",
                       loans: "Loans (owed)", other: "Other assets" };
const signedMoney = n => `${n < 0 ? "−" : "+"}${money(Math.abs(n))}`;

function netWorthCard(worth) {
  if (!worth) return "";
  const known = worth.accounts.filter(a => a.balance != null && a.source !== "static").length;
  const parts = [worth.change != null ? `${signedMoney(worth.change)} vs last month` : "",
                 worth.contributions || worth.market_return
                   ? `contributed ${money(worth.contributions)}, market ${signedMoney(worth.market_return)}` : ""].filter(Boolean);
  return `<a class="card" href="#/balances" data-worth style="display:block;color:inherit">
    <div class="row"><span class="badge" aria-hidden="true">🏦</span><div class="grow"><div style="font-weight:600">Net worth</div>
      <div class="small muted">${known ? esc(parts.join(" · ") || `${plural(known, "account")} with a balance`)
        : "Add balances from a screenshot of your bank apps"}</div></div>
      <span class="amt">${worth.net_worth < 0 ? "−" : ""}${money(Math.abs(worth.net_worth))}</span></div></a>`;
}

async function balancesScreen(ctx) {
  const m = state.balancesMonth;
  const [worth] = await Promise.all([api(`/balances?month=${m}`), taxonomy()]);
  if (!ctx.alive()) return;
  const byGroup = Object.keys(GROUP_TITLES).map(g => [g, worth.accounts.filter(a => a.group === g)]).filter(([, list]) => list.length);
  const row = a => {
    const notes = [];
    if (a.balance == null) notes.push("No balance yet");
    else if (a.source === "static") notes.push("Fixed amount");
    else notes.push(`${a.stale ? "Carried from " : "As of "}${fmtDate(a.as_of + "T12:00:00")}`);
    if (a.contributions != null) notes.push(`contributed ${money(a.contributions)}, ${a.group === "savings" ? "interest" : "market"} ${signedMoney(a.market_return)}`);
    else if (a.change) notes.push(`${signedMoney(a.change)} since ${fmtDate(a.previous_as_of + "T12:00:00")}`);
    return `<a class="item" role="button" tabindex="0" data-account="${esc(a.account_id)}">
      <div class="grow"><div class="ellipsis">${esc(a.account_name)}</div>
        <div class="sub ${a.stale ? "warn-text" : ""}">${esc(a.owner)} · ${esc(notes.join(" · "))}</div></div>
      <span class="amt ${a.balance == null ? "muted" : ""}">${a.balance == null ? "—" : money(a.balance)}</span></a>`;
  };
  ctx.view.innerHTML = monthBar(m) + `<div class="stack">
    <div class="card"><div class="small muted">Net worth at the end of ${esc(monthTitle(m))}</div>
      <div style="font-size:28px;font-weight:700">${worth.net_worth < 0 ? "−" : ""}${money(Math.abs(worth.net_worth))}</div>
      <div class="stats">
        <div class="stat"><div class="k">Assets</div><div class="v">${money(worth.assets)}</div></div>
        <div class="stat"><div class="k">Owed</div><div class="v">${money(worth.liabilities)}</div></div>
        ${worth.change != null ? `<div class="stat"><div class="k">vs last month</div><div class="v">${signedMoney(worth.change)}</div></div>` : ""}
      </div>
      ${worth.contributions || worth.market_return ? `<div class="small muted" style="margin-top:8px">Savings and investments:
        ${money(worth.contributions)} contributed, <span style="white-space:nowrap">${signedMoney(worth.market_return)}</span> market return and interest.</div>` : ""}
      ${worth.missing ? `<div class="small muted" style="margin-top:4px">${plural(worth.missing, "account")} without a balance yet (left out).</div>` : ""}
    </div>
    <button class="btn" data-shot>Read balances from a screenshot</button>
    <input type="file" accept="image/*" data-file hidden>
    ${byGroup.map(([g, list]) => `<div class="section-label"><span>${esc(GROUP_TITLES[g])}</span>
      <span class="muted">${money(list.reduce((t, a) => t + (a.balance || 0), 0))}</span></div>
      <div class="card flush list">${list.map(row).join("")}</div>`).join("")}
    <p class="small muted">Tap an account to type its balance. Balances are what the bank app shows; for cards and loans,
      what's owed. Facts only, not investment advice.</p>
  </div>`;
  bindMonthBar(ctx.view, "balancesMonth");
  $$("[data-account]", ctx.view).forEach(b => b.onclick = () => editBalance(worth.accounts.find(a => a.account_id === b.dataset.account), m));
  const file = $("[data-file]", ctx.view);
  $("[data-shot]", ctx.view).onclick = () => file.click();
  file.onchange = () => { if (file.files[0]) readBalances(file.files[0], worth.accounts); };
}

/** The day a balance is for: the last day of a past month, or today in the current one. */
const balanceDay = m => m === thisMonth() ? today() : localDay(new Date(monthDate(shiftMonth(m, 1)).getTime() - 12 * 3600e3).toISOString());

function editBalance(account, m) {
  const sheet = openSheet(`<h2>${esc(account.account_name)}</h2>
    <p class="muted">${account.group === "credit_cards" || account.group === "loans" ? "What's owed, as the bank shows it." : "The balance the bank app shows."}</p>
    <label class="field">Balance<input inputmode="decimal" data-amount value="${account.balance != null && !account.stale ? Number(account.balance).toFixed(2) : ""}"></label>
    <label class="field">On<input type="date" data-date value="${balanceDay(m)}" max="${today()}"></label>
    <p class="err" data-error></p>
    <div class="stack"><button class="btn" data-save>Save</button><button class="btn text" data-close>Cancel</button></div>`);
  const amount = $("[data-amount]", sheet), save = $("[data-save]", sheet);
  amount.oninput = () => { save.disabled = !(toCents(amount.value) >= 0 && amount.value.trim()); };
  amount.oninput();
  save.onclick = async () => {
    save.disabled = true;
    try {
      await api(`/balances/${enc(account.account_id)}`, { method: "PUT",
        body: { as_of: $("[data-date]", sheet).value, balance: centsText(toCents(amount.value)) } });
      closeSheet();
      toast("Balance saved");
      route();
    } catch (e) { $("[data-error]", sheet).textContent = e.message; save.disabled = false; }
  };
}

async function readBalances(file, accounts) {
  openSheet(`<h2>Reading balances…</h2><div class="row"><div class="spinner" style="margin:12px 0"></div>
    <p class="muted">Claude is reading the screenshot.</p></div>`, { locked: true });
  let read;
  try {
    const form = new FormData();
    form.append("file", await compressImage(file), "balances.jpg");
    if (!state.me) state.me = await api("/me").catch(() => null);
    if (earners().includes(state.me?.user)) form.append("owner", state.me.user);
    read = await api("/balances/read", { method: "POST", form });
  } catch (e) {
    const sheet = openSheet(`<h2>Couldn't read it</h2><p>${esc(e.message)}</p><button class="btn" data-close>OK</button>`);
    $("[data-close]", sheet).focus();
    return;
  }
  const options = sel => `<option value="">Skip</option>` + accounts.map(a =>
    `<option value="${esc(a.account_id)}" ${a.account_id === sel ? "selected" : ""}>${esc(a.account_name)}</option>`).join("");
  const sheet = openSheet(`<h2>Balances on the screenshot</h2>
    <p class="muted">Check each one is matched to the right account (or skip it), then save.</p>
    <label class="field">For the day<input type="date" data-date value="${read.as_of}" max="${today()}"></label>
    <div class="list">${read.rows.map((r, i) => `<div class="item static" style="display:block">
      <div class="row"><div class="grow">${esc(r.shown_name)}${r.currency !== "CAD" ? ` <span class="muted">(${esc(r.currency)})</span>` : ""}</div>
        <span class="amt">${money(r.balance)}${r.owed ? " owed" : ""}</span></div>
      <select class="input" data-row="${i}">${options(r.account_id)}</select></div>`).join("")}</div>
    <p class="err" data-error></p>
    <div class="stack"><button class="btn" data-save>Save balances</button><button class="btn text" data-close>Cancel</button></div>`);
  $("[data-save]", sheet).onclick = async () => {
    const rows = read.rows.map((r, i) => ({ account_id: $(`[data-row="${i}"]`, sheet).value, balance: Number(r.balance).toFixed(2) }))
      .filter(r => r.account_id);
    const ids = rows.map(r => r.account_id);
    if (!rows.length) { $("[data-error]", sheet).textContent = "Nothing to save: every row is skipped."; return; }
    if (new Set(ids).size !== ids.length) { $("[data-error]", sheet).textContent = "Two rows are matched to the same account."; return; }
    try {
      await api("/balances/batch", { method: "POST", body: { as_of: $("[data-date]", sheet).value, rows } });
      closeSheet();
      toast(`${plural(rows.length, "balance")} saved`);
      route();
    } catch (e) { $("[data-error]", sheet).textContent = e.message; }
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
      ${link("#/household", "🏠", "Household", "People, cards and accounts")}
      ${link("#/income", "💰", "Income", "Pay received and expected, by person")}
      ${link("#/balances", "🏦", "Balances and net worth", "What each account holds, from screenshots")}
      ${link("#/savings", "🎯", "Savings target and goals", "How much to put away, and what for")}
      ${link("#/categories", "🏷️", "Categories", "Add, rename or merge")}
      ${link("#/memory", "🧠", "Assistant memory", "Plans and facts the assistant keeps in mind")}
      ${link("/import", "📄", "Import a statement", "CSV or RBC PDF (easier on a computer)")}
      ${link("#/history", "🗂️", "Import past spending", "From a spreadsheet or another app")}
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
    <div class="small muted">Guesses the AI wasn't sure about. Tap to confirm or fix.</div></div><div class="list">
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

// What a category is for, so features follow it through renames (CategoryRow.ROLES on the server).
const CATEGORY_ROLES = [
  ["estimated", "Usual monthly spend", "An everyday necessity with no bill (groceries, fuel): its usual amount counts as still to come"],
  ["subscriptions", "Subscriptions", "A new recurring charge here becomes a bill, with a reminder before it renews"],
  ["tipped", "Tipped", "A receipt here can match a card charge that includes a tip (restaurants)"],
];

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
      <div class="card flush list">${CATEGORY_ROLES.map(([role, label, hint]) => `<label class="item static">
        <div class="grow">${label}<div class="sub">${hint}</div></div>
        <span class="switch"><input type="checkbox" data-role="${role}" ${c[role] ? "checked" : ""}><span></span></span></label>`).join("")}</div>
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
    $$("[data-role]", sheet).forEach(input => input.onchange = async () => {
      try {
        await api(`/categories/${enc(c.name)}`, { method: "PATCH", body: { [input.dataset.role]: input.checked } });
        c[input.dataset.role] = input.checked;
        toast("Saved");
      } catch (e) { input.checked = !input.checked; toast(e.message); }
    });
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

// ---------- chat assistant ----------
/** Whole amounts in the household's currency, for example questions: "$3,000", "€3.000", "₹3,000". */
const roughMoney = n => new Intl.NumberFormat(undefined, { style: "currency", currency: moneyFormat.resolvedOptions().currency,
                                                           currencyDisplay: "narrowSymbol", maximumFractionDigits: 0 }).format(n);
const chatExamples = () => [
  "How much did we spend eating out in the last 3 months?",
  `Can we afford a ${roughMoney(3000)} trip in March?`,
  `We're planning to buy a car next summer, about ${roughMoney(25000)}. Keep that in mind.`,
  "What should I cut back on this month?",
];

/** The assistant's plain-text answer as HTML: escaped, with **bold** kept. Line breaks come from CSS (pre-wrap). */
const chatText = t => esc(t).replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");

function chatBubble(m) {
  if (m.kind === "question") return `<div class="bubble me">${esc(m.text)}</div>`;
  const actions = (m.actions ?? []).map(a => `<div class="chat-action">✅ ${esc(a.summary)}</div>`).join("");
  const sources = (m.sources ?? []).map(s =>
    `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title || new URL(s.url).hostname)}</a>`).join(" · ");
  return `<div class="bubble ai"><div data-text>${chatText(m.text ?? "")}</div>${actions ? `<div data-actions>${actions}</div>` : `<div data-actions></div>`}
    ${sources ? `<div class="tiny muted chat-sources">Sources: ${sources}</div>` : ""}</div>`;
}

/** POST /chat as server-sent events; calls on(event, data) for each. */
async function streamChat(body, on) {
  let resp;
  try {
    resp = await fetch("/api/v1/chat", {
      method: "POST",
      headers: { Authorization: `Bearer ${token()}`, "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. Is Tailscale on?");
  }
  if (!resp.ok) {
    const data = await resp.json().catch(() => null);
    throw new ApiError(resp.status, errorDetail(data) || `Server error ${resp.status}.`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const name = block.match(/^event: (.*)$/m)?.[1];
      const data = block.match(/^data: (.*)$/m)?.[1];
      if (name && data) on(name, JSON.parse(data));
    }
  }
}

async function chatScreen(ctx, threadId) {
  const [usage, thread] = await Promise.all([
    api("/chat/usage"),
    threadId ? api(`/chat/threads/${threadId}`) : Promise.resolve(null),
  ]);
  if (!ctx.alive()) return;
  if (thread) ctx.title(thread.title);
  let currentId = thread?.id ?? null;
  let left = usage.left;

  ctx.view.innerHTML = `<div class="chat">
    <div class="row chat-bar">
      <button class="link" data-history>History</button><span class="grow"></span>
      <span class="tiny muted" data-left></span>
      <button class="link" data-new ${thread ? "" : "hidden"}>New chat</button>
    </div>
    <div data-log class="chat-log">${thread ? thread.messages.map(chatBubble).join("") : `
      <div class="card stack" data-empty>
        <div><b>Ask about your money</b></div>
        <div class="small muted">Questions about spending, budgets and bills, plans you want help with, or changes
          (“set the grocery budget to ${roughMoney(800)}”). It uses your transactions, receipts, bills and balances, what
          you've told it to remember${usage.web_search ? ", and the web" : ""}. Your chats are private to you.</div>
        <div class="chips">${chatExamples().map(q => `<button class="chip" data-example>${esc(q)}</button>`).join("")}</div>
      </div>`}</div>
    <form class="chat-compose" data-form>
      <textarea data-input rows="1" maxlength="4000" placeholder="Ask or tell me something…" enterkeyhint="send"></textarea>
      <button class="btn" data-send aria-label="Send">↑</button>
    </form>
  </div>`;
  const log = $("[data-log]", ctx.view), input = $("[data-input]", ctx.view), send = $("[data-send]", ctx.view);
  const showLeft = () => { $("[data-left]", ctx.view).textContent = left == null ? "" : `${plural(left, "question")} left this month`; };
  showLeft();
  const scrollDown = () => window.scrollTo(0, document.body.scrollHeight);
  scrollDown();
  if (!usage.available) { input.disabled = send.disabled = true; input.placeholder = "Chat needs an AI model set up on the server"; }
  if (thread && !thread.can_continue) { input.disabled = send.disabled = true; input.placeholder = "This chat is full: start a new one"; }

  const grow = () => { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 140)}px`; };
  input.oninput = grow;
  input.onkeydown = e => { if (e.key === "Enter" && !e.shiftKey && !matchMedia("(pointer: coarse)").matches) { e.preventDefault(); ask(); } };
  $$("[data-example]", ctx.view).forEach(b => b.onclick = () => { input.value = b.textContent; grow(); input.focus(); });
  $("[data-form]", ctx.view).onsubmit = e => { e.preventDefault(); ask(); };
  $("[data-new]", ctx.view).onclick = () => { location.hash = "#/ask"; };
  $("[data-history]", ctx.view).onclick = () => chatHistory();

  async function ask() {
    const message = input.value.trim();
    if (!message || state.chatBusy) return;
    state.chatBusy = true;
    send.disabled = true;
    input.value = "";
    grow();
    $("[data-empty]", log)?.remove();
    log.insertAdjacentHTML("beforeend", chatBubble({ kind: "question", text: message }));
    log.insertAdjacentHTML("beforeend", `<div class="bubble ai pending"><div data-text></div><div data-actions></div>
      <div class="tiny muted" data-status>Thinking…</div></div>`);
    const bubble = log.lastElementChild;
    const textEl = $("[data-text]", bubble), statusEl = $("[data-status]", bubble);
    let answer = "";
    scrollDown();
    try {
      await streamChat({ message, thread_id: currentId }, (name, data) => {
        if (name === "thread") {
          currentId = data.thread_id;
          if (ctx.alive()) { ctx.title(data.title); $("[data-new]", ctx.view).hidden = false; }
          history.replaceState(null, "", `#/ask/${currentId}`);
        } else if (name === "text") {
          answer += data.text;
          textEl.innerHTML = chatText(answer);
          statusEl.hidden = true;
        } else if (name === "status") {
          statusEl.textContent = `${data.text}…`;
          statusEl.hidden = false;
        } else if (name === "action") {
          $("[data-actions]", bubble).insertAdjacentHTML("beforeend", `<div class="chat-action">✅ ${esc(data.summary)}</div>`);
        } else if (name === "done") {
          bubble.outerHTML = chatBubble({ kind: "answer", ...data });
          left = data.questions_left;
          showLeft();
          if (data.actions.length) state.taxonomy = null;  // budgets or categories may have changed
        } else if (name === "error") {
          throw new ApiError(502, data.detail);
        }
        scrollDown();
      });
    } catch (e) {
      bubble.classList.remove("pending");
      statusEl.hidden = false;
      statusEl.innerHTML = `<span class="err">${esc(e.message)}</span>`;
      if (!answer) input.value = message;
    } finally {
      state.chatBusy = false;
      send.disabled = false;
    }
  }
}

async function chatHistory() {
  const threads = await api("/chat/threads").catch(e => { toast(e.message); return null; });
  if (!threads) return;
  const sheet = openSheet(`<h2>Your chats</h2>${threads.length ? `<div class="card flush list">${threads.map(t => `
    <div class="item static"><a class="grow" href="#/ask/${t.id}" data-open><div class="ellipsis">${esc(t.title)}</div>
      <div class="sub">${esc(fmtWhen(t.updated_at))}</div></a>
      <button class="icon-btn" data-delete="${t.id}" aria-label="Delete chat"><svg viewBox="0 0 24 24"><path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13"/></svg></button></div>`).join("")}</div>`
    : `<p class="muted">No chats yet.</p>`}<button class="btn text" data-close>Close</button>`);
  $$("[data-open]", sheet).forEach(a => a.addEventListener("click", closeSheet));
  $$("[data-delete]", sheet).forEach(b => b.onclick = async () => {
    try {
      await api(`/chat/threads/${b.dataset.delete}`, { method: "DELETE" });
      b.closest(".item").remove();
      if (location.hash === `#/ask/${b.dataset.delete}`) { closeSheet(); location.hash = "#/ask"; }
    } catch (e) { toast(e.message); }
  });
}

async function memoryScreen(ctx) {
  const memories = await api("/chat/memory");
  if (!ctx.alive()) return;
  ctx.view.innerHTML = `<div class="stack">
    <div class="small muted">Plans and facts the assistant keeps in mind when you chat. Only you see these. Tell it in chat,
      or add one here.</div>
    ${memories.length ? `<div class="card flush list">${memories.map(m => `<div class="item static"><div class="grow">
      <div style="white-space:normal">${esc(m.fact)}</div><div class="sub">${esc(fmtDate(m.created_at))}</div></div>
      <button class="icon-btn" data-delete="${m.id}" aria-label="Forget"><svg viewBox="0 0 24 24"><path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13"/></svg></button></div>`).join("")}</div>`
      : `<p class="muted center">Nothing yet.</p>`}
    <form class="card stack" data-form><label class="field">Add something to remember
      <input data-fact maxlength="500" placeholder="e.g. Saving ${esc(roughMoney(5000))} for a car by June"></label>
      <button class="btn">Add</button></form>
  </div>`;
  $$("[data-delete]", ctx.view).forEach(b => b.onclick = async () => {
    try { await api(`/chat/memory/${b.dataset.delete}`, { method: "DELETE" }); route(); } catch (e) { toast(e.message); }
  });
  $("[data-form]", ctx.view).onsubmit = async e => {
    e.preventDefault();
    const fact = $("[data-fact]", ctx.view).value.trim();
    if (fact.length < 3) return;
    try { await api("/chat/memory", { method: "POST", body: { fact } }); route(); } catch (err) { toast(err.message); }
  };
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
// ---------- household setup (first run) and editing ----------
const PERSON_KINDS = [
  { icon: "person", label: "Person", hint: "Their own spending: gym, haircuts, clothes…" },
  { icon: "family", label: "Family", hint: "Money sent to them, and things bought for them" },
  { icon: "abroad", label: "Abroad", hint: "Spending kept apart, e.g. while living in another country" },
];

function defaultLines(p) {
  const name = p.name.trim() || "…";
  if (p.icon === "family") return p.split ? { line: `${name} Shopping`, transfer: name } : { line: name, transfer: "" };
  if (p.icon === "abroad") return { line: name, transfer: "" };
  return { line: `${name} Personal`, transfer: "" };
}

function householdDraft(h) {
  const people = h.purposes.slice(1).map(p => ({
    name: p.name, icon: p.icon, line: p.budget_line || "", transfer: p.transfer_budget_line || "",
    split: !!p.transfer_budget_line, guide: p.guide, original: { name: p.name, icon: p.icon }, custom: true,
  }));
  // An account's owner points at the person itself, so renaming them in the wizard keeps it.
  const accounts = h.accounts.map(a => ({ ...a, card_last4: (a.card_last4 || "").split(",").join(", "),
    ownerRef: people.find(p => p.name === a.owner) || null, known: h.known_accounts.some(k => k.id === a.id) }));
  return { description: h.setup_needed ? "" : h.description, currency: h.currency, originalCurrency: h.currency,
    sharedGuide: h.setup_needed ? null : h.purposes[0].guide, people, accounts };
}

/** "1234, 5678" as ["1234", "5678"]; null when empty. */
function cardList(text) {
  const cards = String(text || "").split(/[,\s]+/).filter(Boolean);
  return cards.length ? cards : null;
}

function slug(text) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 40) || "account";
}

function householdBody(d) {
  return {
    description: d.description.trim(),
    currency: d.currency,
    purposes: [{ name: "Shared", guide: d.sharedGuide }, ...d.people.map(p => {
      const lines = p.custom ? { line: p.line, transfer: p.transfer } : defaultLines(p);
      const same = p.original && p.original.name === p.name.trim() && p.original.icon === p.icon;
      return {
        name: p.name.trim(), icon: p.icon, guide: same ? p.guide : null,
        budget_line: lines.line.trim() || null, transfer_budget_line: lines.transfer.trim() || null,
      };
    })],
    accounts: d.accounts.map(a => ({
      id: a.id, institution: a.institution.trim(), name: a.name.trim(),
      // Your own accounts follow the household's currency unless they had one of their own.
      currency: a.known || (a.currency && a.currency !== d.originalCurrency) ? a.currency : null,
      card_last4: cardList(a.card_last4),
      kind: a.kind || "chequing",
      owner: a.ownerRef && d.people.includes(a.ownerRef) ? a.ownerRef.name.trim() : "Shared",
    })),
  };
}

async function householdScreen(ctx) {
  const [h, cats] = await Promise.all([api("/household"), api("/categories")]);
  if (!ctx.alive()) return;
  const setup = h.setup_needed;
  const d = householdDraft(h);
  if (setup) d.currency = guessCurrency() || h.currency;
  d.categories = cats.map(c => ({ name: c.name, original: c.name, uses: c.transactions }));
  const steps = ["About you", "People", "Categories", "Accounts", "Review"];
  let step = 0, error = "";
  if (setup) { $("#tabs").hidden = true; $("#back").hidden = true; ctx.title("Set up Xpensimate"); }

  const peopleHtml = () => d.people.map((p, i) => {
    const lines = p.custom ? p : { ...p, ...defaultLines(p) };
    const uses = p.original ? h.purposes_in_use[p.original.name] || 0 : 0;
    return `<div class="card stack" data-person="${i}">
      <div class="row"><label class="field grow">Name<input data-p="name" value="${esc(p.name)}" maxlength="40" placeholder="e.g. Alex" autocapitalize="words"></label>
        <button class="btn text danger" data-remove-person="${i}" style="width:auto;color:var(--critical)" ${uses ? `disabled title="Used by ${uses} charges"` : ""}>Remove</button></div>
      <div class="seg">${PERSON_KINDS.map(k => `<button type="button" data-kind="${k.icon}" class="${p.icon === k.icon ? "on" : ""}">${k.label}</button>`).join("")}</div>
      <div class="small muted">${esc(PERSON_KINDS.find(k => k.icon === p.icon).hint)}${uses ? ` · ${plural(uses, "charge")} so far` : ""}</div>
      ${p.icon === "family" ? `<label class="row small"><input type="checkbox" data-p="split" ${p.split ? "checked" : ""}> Budget money sent to them separately from things bought for them</label>` : ""}
      <details><summary class="small muted">Budget line${lines.transfer ? "s" : ""}: ${esc(lines.line)}${lines.transfer ? `, ${esc(lines.transfer)}` : ""}</summary>
        <div class="stack" style="margin-top:8px">
          <label class="field">Budget line<input data-p="line" value="${esc(lines.line)}" maxlength="64"></label>
          ${p.icon === "family" && p.split ? `<label class="field">Money sent to them<input data-p="transfer" value="${esc(lines.transfer)}" maxlength="64"></label>` : ""}
        </div></details>
    </div>`;
  }).join("");

  const categoriesHtml = () => `<div class="card small muted">What money is spent on. Shared spending is budgeted
      by these, and every receipt line gets one. Rename or remove the starter ones and add your own; you set
      the budgets later.</div>
    <div class="card flush list">${d.categories.map((c, i) => `<div class="item static">${badge(c.original || c.name)}
      <div class="grow" style="min-width:0"><input class="input" data-cat="${i}" value="${esc(c.name)}" maxlength="64" placeholder="e.g. Pets" aria-label="Category name">
        ${c.uses ? `<div class="sub small muted" style="margin-top:4px">${plural(c.uses, "charge")} · merge it in Categories to remove it</div>` : ""}</div>
      ${c.uses ? "" : `<button class="icon-btn" data-remove-cat="${i}" aria-label="Remove ${esc(c.name)}" style="color:var(--critical)">✕</button>`}
    </div>`).join("")}</div>
    <button class="btn secondary" data-add-cat>+ Add a category</button>`;

  const bankOptions = a => [...h.known_accounts.map(k => `<option value="${esc(k.id)}" ${a.id === k.id ? "selected" : ""}>${esc(k.name)}</option>`),
    `<option value="" ${a.known ? "" : "selected"}>Another card or account…</option>`].join("");
  const accountsHtml = () => d.accounts.map((a, i) => `<div class="card stack" data-account="${i}">
      <label class="field">Account<select data-a="known">${bankOptions(a)}</select></label>
      ${a.known ? "" : `<label class="field">Bank<input data-a="institution" value="${esc(a.institution)}" maxlength="64" placeholder="e.g. Scotiabank"></label>`}
      <label class="field">Name in the app<input data-a="name" value="${esc(a.name)}" maxlength="64"></label>
      <div class="row">
        <label class="field grow">Type<select data-a="kind">${h.account_kinds.map(k =>
          `<option value="${esc(k.id)}" ${(a.kind || "chequing") === k.id ? "selected" : ""}>${esc(k.label)}</option>`).join("")}</select></label>
        <label class="field grow">Whose<select data-owner><option value="">Shared</option>${d.people.map((p, j) =>
          `<option value="${j}" ${a.ownerRef === p ? "selected" : ""}>${esc(p.name.trim() || "(unnamed)")}</option>`).join("")}</select></label>
      </div>
      <div class="row"><label class="field grow">Last 4 digits of each card<input data-a="card_last4" value="${esc(a.card_last4)}" inputmode="numeric" maxlength="40" placeholder="1234, or 1234, 5678 for two cards"></label>
        <button class="btn text danger" data-remove-account="${i}" style="width:auto;align-self:flex-end;color:var(--critical)">Remove</button></div>
    </div>`).join("");

  const review = () => {
    const body = householdBody(d);
    return `<div class="card stack">
      <div><div class="small muted">Your household</div><div>${esc(body.description || "—")}</div></div>
      <div><div class="small muted">Currency</div><div>${esc(currencyName(body.currency))} (${esc(body.currency)})</div></div>
      <div><div class="small muted">Who spending is for</div>
        ${body.purposes.map(p => `<div class="row" style="margin-top:6px"><span class="badge sm" aria-hidden="true">${p.budget_line ? PERSON_ICONS[p.icon] : "🛒"}</span>
          <div class="grow"><b>${esc(p.name)}</b><div class="sub small muted">${!p.budget_line ? "Budgeted by category"
            : p.transfer_budget_line ? `Budgeted as ${esc(p.transfer_budget_line)} (money sent) and ${esc(p.budget_line)}` : `Budgeted as ${esc(p.budget_line)}`}</div></div></div>`).join("")}</div>
      <div><div class="small muted">Categories</div><div>${esc(d.categories.map(c => c.name.trim()).join(", "))}</div></div>
      <div><div class="small muted">Accounts</div>${body.accounts.length ? body.accounts.map(a => `<div>${esc(a.name)}${a.card_last4 ? ` <span class="muted">${a.card_last4.map(c => `•••• ${esc(c)}`).join(", ")}</span>` : ""}
        <span class="small muted">· ${esc(h.account_kinds.find(k => k.id === a.kind)?.label || a.kind)}${a.owner !== "Shared" ? `, ${esc(a.owner)}'s` : ""}</span></div>`).join("")
        : `<div class="muted">None yet: purchases still come in, without an account.</div>`}</div>
    </div>`;
  };

  const sections = [
    () => `<div class="card stack">
        <h3>${setup ? "Welcome! Let's set up your household" : "About your household"}</h3>
        <div class="small muted">A sentence about who you are and where you live. The AI model reads it when it sorts a purchase you haven't seen before.</div>
        <textarea class="input note-input" data-desc rows="3" style="min-height:88px" maxlength="500" placeholder="e.g. two people in Toronto, Canada, Alex and Sam, who also help Alex's parents">${esc(d.description)}</textarea>
        <label class="field">Currency<select data-currency>${allCurrencies().map(c =>
          `<option value="${c}" ${c === d.currency ? "selected" : ""}>${esc(currencyName(c))} (${c})</option>`).join("")}</select></label>
        <div class="small muted">Amounts show in it. ${setup ? "" : "Changing it relabels amounts; it doesn't convert them."}</div>
      </div>`,
    () => `<div class="card small muted">Every purchase is for someone. <b>Shared</b> spending (groceries, rent, eating out…) is
        budgeted by category. Add the people whose own spending you want to budget separately.</div>
      ${peopleHtml()}
      <button class="btn secondary" data-add-person>+ Add a person</button>`,
    categoriesHtml,
    () => `<div class="card small muted">Your cards and bank accounts. When a bank alert shows a card's last 4 digits,
        the purchase goes to that account. You can skip this and add them later.</div>
      ${accountsHtml()}
      <button class="btn secondary" data-add-account>+ Add an account</button>`,
    review,
  ];

  const draw = () => {
    ctx.view.innerHTML = `<div class="stack">
      <div class="chips">${steps.map((s, i) => `<button class="chip ${i === step ? "on" : ""}" data-step="${i}">${i + 1}. ${s}</button>`).join("")}</div>
      ${sections[step]()}
      <p class="err" data-error>${esc(error)}</p>
      <div class="btn-row">
        ${step > 0 ? `<button class="btn secondary" data-prev>Back</button>` : ""}
        ${step < steps.length - 1 ? `<button class="btn" data-next>Next</button>` : `<button class="btn" data-save>${setup ? "Finish setup" : "Save"}</button>`}
      </div></div>`;
    bind();
  };

  const check = () => {
    const at = steps[step];
    if (at === "About you" && !d.description.trim()) return "Add a few words about your household.";
    if (at === "People") {
      const names = d.people.map(p => p.name.trim());
      if (names.some(n => !n)) return "Every person needs a name.";
      if (new Set([...names, "Shared"]).size !== names.length + 1) return "Each person needs a different name (and not \"Shared\").";
    }
    if (at === "Categories") {
      const names = d.categories.map(c => c.name.trim());
      if (!names.length) return "Keep at least one category.";
      if (names.some(n => !n)) return "Every category needs a name.";
      if (names.some(n => n.includes("/"))) return "A category name can't contain \"/\".";
      if (new Set(names.map(n => n.toLowerCase())).size !== names.length) return "Each category needs a different name.";
      const taken = new Set(["Uncategorized", ...householdBody(d).purposes.flatMap(p => [p.budget_line, p.transfer_budget_line]).filter(Boolean)]);
      const clash = names.find(n => taken.has(n));
      if (clash) return `"${clash}" is already a person's budget line (or means uncategorized); pick another name.`;
    }
    if (at === "Accounts") {
      if (d.accounts.some(a => !a.name.trim() || !a.institution.trim())) return "Every account needs a bank and a name.";
      if (d.accounts.some(a => (cardList(a.card_last4) || []).some(c => !/^\d{4}$/.test(c)))) return "Card digits are the last 4 numbers on each card, with a comma between cards.";
    }
    return "";
  };
  const go = to => { error = to > step ? check() : ""; if (!error) step = to; draw(); window.scrollTo(0, 0); };

  function bind() {
    const v = ctx.view;
    $("[data-desc]", v)?.addEventListener("input", e => { d.description = e.target.value; });
    $("[data-currency]", v)?.addEventListener("change", e => { d.currency = e.target.value; });
    $$("[data-step]", v).forEach(b => b.onclick = () => { let to = +b.dataset.step; if (to > step) { for (; step < to && !(error = check()); step++); draw(); } else go(to); });
    $("[data-prev]", v)?.addEventListener("click", () => go(step - 1));
    $("[data-next]", v)?.addEventListener("click", () => go(step + 1));
    $("[data-add-person]", v)?.addEventListener("click", () => { d.people.push({ name: "", icon: "person", line: "", transfer: "", split: false, custom: false }); draw(); });
    $("[data-add-account]", v)?.addEventListener("click", () => {
      const used = new Set(d.accounts.map(a => a.id));
      const k = h.known_accounts.find(k => !used.has(k.id));
      d.accounts.push(k ? { ...k, card_last4: "", ownerRef: null, known: true }
        : { id: "", institution: "", name: "", currency: null, card_last4: "", kind: "credit_card", ownerRef: null, known: false });
      draw();
    });
    $$("[data-person]", v).forEach(card => {
      const p = d.people[+card.dataset.person];
      $$("[data-p]", card).forEach(input => input.addEventListener(input.type === "checkbox" ? "change" : "input", () => {
        const field = input.dataset.p;
        if (field === "split") { p.split = input.checked; p.custom = false; draw(); return; }
        if (field === "line" || field === "transfer") {
          if (!p.custom) Object.assign(p, defaultLines(p));
          p.custom = true;
        }
        p[field] = input.value;
        if (field === "name" && !p.custom) { const s = $("summary", card); if (s) s.textContent = `Budget line: ${defaultLines(p).line}`; }
      }));
      $$("[data-kind]", card).forEach(b => b.onclick = () => { p.icon = b.dataset.kind; p.custom = false; draw(); });
    });
    $$("[data-cat]", v).forEach(input => input.addEventListener("input", () => { d.categories[+input.dataset.cat].name = input.value; }));
    $$("[data-remove-cat]", v).forEach(b => b.onclick = () => { d.categories.splice(+b.dataset.removeCat, 1); draw(); });
    $("[data-add-cat]", v)?.addEventListener("click", () => {
      d.categories.push({ name: "", original: null, uses: 0 });
      draw();
      $$("[data-cat]", ctx.view).at(-1).focus();
    });
    $$("[data-remove-person]", v).forEach(b => b.onclick = () => {
      const [gone] = d.people.splice(+b.dataset.removePerson, 1);
      d.accounts.forEach(a => { if (a.ownerRef === gone) a.ownerRef = null; });
      draw();
    });
    $$("[data-account]", v).forEach(card => {
      const a = d.accounts[+card.dataset.account];
      $$("[data-a]", card).forEach(input => input.addEventListener(input.tagName === "SELECT" ? "change" : "input", () => {
        if (input.dataset.a === "known") {
          const k = h.known_accounts.find(k => k.id === input.value);
          Object.assign(a, k ? { ...k, card_last4: a.card_last4, ownerRef: a.ownerRef, known: true }
            : { id: "", institution: "", name: "", currency: null, known: false });
          draw();
          return;
        }
        a[input.dataset.a] = input.value;
      }));
      $("[data-owner]", card).onchange = e => { a.ownerRef = e.target.value === "" ? null : d.people[+e.target.value]; };
    });
    $$("[data-remove-account]", v).forEach(b => b.onclick = () => { d.accounts.splice(+b.dataset.removeAccount, 1); draw(); });
    $("[data-save]", v)?.addEventListener("click", save);
  }

  async function save() {
    for (step = 0; step < steps.length - 1; step++) if ((error = check())) return draw();
    // Accounts outside the known list get an id from their bank and name.
    const ids = new Set(d.accounts.filter(a => a.id).map(a => a.id));
    for (const a of d.accounts) if (!a.id) { let id = slug(`${a.institution} ${a.name}`), n = 2; while (ids.has(id)) id = `${slug(`${a.institution} ${a.name}`)}_${n++}`; a.id = id; ids.add(id); }
    const btn = $("[data-save]", ctx.view);
    btn.disabled = true;
    try {
      await api("/household", { method: "PUT", body: householdBody(d) });
      await api("/categories", { method: "PUT", body: { categories: d.categories.map(c => ({ name: c.name.trim(), original: c.original })) } });
      await taxonomy(true);
      state.taxonomy = null;
      state.setupNeeded = false;
      setCurrency(d.currency);
      toast(setup ? "You're all set" : "Household saved");
      location.hash = setup ? "#/" : "#/more";
      if (setup) route();
    } catch (e) {
      error = e.message;
      draw();
    }
  }

  draw();
}


// ---------- import past spending (a CSV from a spreadsheet or another app) ----------
const fmtLabel = f => f.replace("%Y", "YYYY").replace("%y", "YY").replace("%m", "MM").replace("%d", "DD").replace("%B", "Month").replace("%b", "Mon");

async function historyScreen(ctx) {
  const status = await api("/history");
  if (!ctx.alive()) return;
  let file = null, p = null, m = null, error = "", busy = false;

  const field = (key, label, required) => `<label class="field">${label}${required ? "" : " (optional)"}
    <select data-col="${key}">${required ? "" : `<option value="">—</option>`}${p.headers.map(h =>
      `<option ${m[key] === h ? "selected" : ""}>${esc(h)}</option>`).join("")}</select></label>`;

  const valueMap = (column, map, choices, blank) => {
    const values = p.values[column];
    if (!values) return `<div class="small muted">This column has too many different values to match one by one; ${blank.toLowerCase()} for all.</div>`;
    return values.map(v => `<div class="row" style="margin-top:6px"><div class="grow small">${esc(v)}</div>
      <select class="input" style="max-width:55%" data-map="${map}" data-value="${esc(v)}">
        <option value="">${blank}</option>${choices.map(c => `<option ${m[map][v] === c ? "selected" : ""}>${esc(c)}</option>`).join("")}
      </select></div>`).join("");
  };

  const mappingHtml = () => {
    const formats = p.date_formats[m.date] || [];
    if (!formats.includes(m.date_format)) m.date_format = formats[0] || "";
    return `<div class="card stack">
        <h3>${esc(file.name)}</h3><div class="small muted">${plural(p.row_count, "row")}. Check which column is which.</div>
        ${field("date", "Date", true)}
        ${formats.length ? (formats.length > 1 ? `<label class="field">Dates are written as<select data-fmt>${formats.map(f =>
            `<option value="${esc(f)}" ${f === m.date_format ? "selected" : ""}>${fmtLabel(f)}</option>`).join("")}</select></label>`
            : `<div class="small muted">Dates read as ${fmtLabel(formats[0])}.</div>`)
          : `<div class="small err">Can't read the dates in this column.</div>`}
        ${field("merchant", "Where (merchant or description)", true)}
        ${field("amount", "Amount", true)}
        <div class="seg"><button data-sign="positive" class="${m.spending_is === "positive" ? "on" : ""}">Spending is positive</button>
          <button data-sign="negative" class="${m.spending_is === "negative" ? "on" : ""}">Spending is negative</button></div>
        ${field("category", "Category", false)}
        ${field("who", "Who it was for", false)}
        ${field("note", "Notes", false)}
      </div>
      ${m.category ? `<div class="card"><h3>Your categories</h3><div class="small muted">Match each to one of Xpensimate's.</div>
        ${valueMap(m.category, "categories", p.categories, "Leave uncategorized")}</div>` : ""}
      ${m.who ? `<div class="card"><h3>Who it was for</h3><div class="small muted">Anything not matched counts as Shared.</div>
        ${valueMap(m.who, "who_values", p.purposes, "Shared")}</div>` : ""}
      <div class="card stack">
        <label class="row"><input type="checkbox" data-add ${m.add_as_spending ? "checked" : ""}>
          <span class="grow">Also add it as spending${p.first_live_charge ? ` before ${fmtDate(`${p.first_live_charge}T12:00:00`)}` : ""}</span></label>
        <div class="small muted">${p.first_live_charge
          ? `So trends and reviews go back further. Rows from ${fmtDate(`${p.first_live_charge}T12:00:00`)} on only teach categorization, since Xpensimate already has that spending.`
          : "So trends and reviews go back further."} Either way, every row teaches Xpensimate how you file each merchant.
          Importing again replaces this import.</div>
      </div>
      <p class="err" data-error>${esc(error)}</p>
      <button class="btn" data-import ${busy || !m.date_format ? "disabled" : ""}>${busy ? "Importing…" : `Import ${plural(p.row_count, "row")}`}</button>`;
  };

  const draw = () => {
    ctx.view.innerHTML = `<div class="stack">
      ${p ? mappingHtml() : `<div class="card stack">
        <h3>Bring in your past spending</h3>
        <div class="small muted">A CSV from a budgeting spreadsheet or another app: one row per purchase, with a date,
          where, and how much, and ideally your category and who it was for. Xpensimate learns how you file each
          merchant, and can add the older spending to your history.</div>
        <label class="btn secondary" style="cursor:pointer">Choose a CSV file<input type="file" accept=".csv,.tsv,.txt,text/csv" data-file hidden></label>
        <div class="small muted">From Excel or Google Sheets: File → Download → CSV.</div>
        <p class="err" data-error>${esc(error)}</p></div>
        ${status.examples ? `<div class="card stack"><h3>Your last import</h3>
          <div class="small">${plural(status.examples, "row")} learned from, ${plural(status.rules, "merchant rule")},
            ${plural(status.added_as_spending, "purchase")} added as spending.</div>
          <button class="btn danger" data-clear>Remove imported history</button></div>` : ""}`}
    </div>`;
    bind();
  };

  const bind = () => {
    const v = ctx.view;
    $("[data-file]", v)?.addEventListener("change", async e => {
      file = e.target.files[0];
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      try {
        p = await api("/history/preview", { method: "POST", form });
        const s = p.suggested;
        m = { date: s.date, merchant: s.merchant, amount: s.amount, category: s.category, who: s.who, note: s.note,
          date_format: s.date_format || "", spending_is: s.spending_is, add_as_spending: true,
          categories: { ...p.category_guesses }, who_values: { ...p.who_guesses } };
        error = "";
      } catch (err) { error = err.message; file = null; }
      draw();
    });
    $$("[data-col]", v).forEach(s => s.onchange = () => { m[s.dataset.col] = s.value || null; draw(); });
    $("[data-fmt]", v)?.addEventListener("change", e => { m.date_format = e.target.value; });
    $$("[data-sign]", v).forEach(b => b.onclick = () => { m.spending_is = b.dataset.sign; draw(); });
    $$("[data-map]", v).forEach(s => s.onchange = () => { m[s.dataset.map][s.dataset.value] = s.value || null; });
    $("[data-add]", v)?.addEventListener("change", e => { m.add_as_spending = e.target.checked; });
    $("[data-clear]", v)?.addEventListener("click", async () => {
      if (!await confirmSheet("Remove imported history?", "Xpensimate forgets what it learned from the import, and the spending it added is taken out.", "Remove")) return;
      await api("/history", { method: "DELETE" });
      toast("Imported history removed");
      route();
    });
    $("[data-import]", v)?.addEventListener("click", async () => {
      if (!m.date || !m.merchant || !m.amount) { error = "Pick the date, where and amount columns."; return draw(); }
      busy = true; error = ""; draw();
      const form = new FormData();
      form.append("file", file);
      form.append("mapping", JSON.stringify(m));
      try {
        const r = await api("/history/import", { method: "POST", form });
        const skipped = Object.entries(r.skipped).map(([why, n]) => `${n} ${why}`).join(", ");
        state.taxonomy = null;
        busy = false; p = null; file = null;
        ctx.view.innerHTML = `<div class="stack"><div class="card stack"><h3>Imported</h3>
          <div>${plural(r.rows, "row")} learned from, ${plural(r.rules, "merchant rule")} made, ${plural(r.added_as_spending, "purchase")} added as spending.</div>
          ${skipped ? `<div class="small muted">Skipped: ${esc(skipped)}.</div>` : ""}
          <a class="btn" href="#/">Done</a></div></div>`;
      } catch (err) { busy = false; error = err.message; draw(); }
    });
  };

  draw();
}


const ROUTES = [
  { re: /^\/$/, screen: homeScreen, title: "Xpensimate", tab: "home" },
  { re: /^\/transactions$/, screen: transactionsScreen, title: "Spending", tab: "transactions" },
  { re: /^\/receipts$/, screen: receiptsScreen, title: "Receipts", tab: "receipts" },
  { re: /^\/bills$/, screen: billsScreen, title: "Bills", tab: "bills" },
  { re: /^\/ask$/, screen: chatScreen, title: "Ask", tab: "ask" },
  { re: /^\/ask\/([0-9a-f-]+)$/, screen: chatScreen, title: "Ask", tab: "ask" },
  { re: /^\/more$/, screen: moreScreen, title: "More", tab: "more" },
  { re: /^\/line\/([^/]+)\/(\d{4}-\d{2})$/, screen: lineScreen, title: "" },
  { re: /^\/txn\/([0-9a-f-]+)$/, screen: transactionScreen, title: "Transaction" },
  { re: /^\/receipt\/([0-9a-f-]+)$/, screen: receiptScreen, title: "Receipt" },
  { re: /^\/review$/, screen: reviewScreen, title: "Month review", parent: "more" },
  { re: /^\/budgets$/, screen: budgetsScreen, title: "Budgets", parent: "more" },
  { re: /^\/alerts$/, screen: alertsScreen, title: "Budget alerts", parent: "more" },
  { re: /^\/categories$/, screen: categoriesScreen, title: "Categories", parent: "more" },
  { re: /^\/memory$/, screen: memoryScreen, title: "Assistant memory", parent: "more" },
  { re: /^\/household$/, screen: householdScreen, title: "Household", parent: "more" },
  { re: /^\/history$/, screen: historyScreen, title: "Import past spending", parent: "more" },
  { re: /^\/income$/, screen: incomeScreen, title: "Income", parent: "home" },
  { re: /^\/balances$/, screen: balancesScreen, title: "Balances", parent: "home" },
  { re: /^\/savings$/, screen: savingsScreen, title: "Savings target and goals", parent: "more" },
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

  // A new server starts with the setup wizard.
  if (state.setupNeeded === undefined) {
    try {
      const h = await api("/household");
      state.setupNeeded = h.setup_needed;
      setCurrency(h.currency);
    } catch { state.setupNeeded = false; }
    if (seq !== renderSeq) return;
  }
  if (state.setupNeeded && location.hash !== "#/household") { location.replace("#/household"); return route(); }

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
    await Promise.all([taxonomy(), accounts()]);  // badges for person lines, and account names on transfers
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
  if (document.visibilityState === "visible" && token() && !$("#sheet-root").children.length && !state.chatBusy &&
      state.tab !== "ask" && Date.now() - lastRender > 60_000) route();
});
route();
