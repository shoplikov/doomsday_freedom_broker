// The single API adapter. Components never call fetch directly, so pointing the UI at another
// backend (e.g. from a Dynamics 365 web resource) is one setting: window.CQA_API_BASE.
export const API_BASE = window.CQA_API_BASE ?? "";

export function getRole() {
  try { return localStorage.getItem("cqa-role") || "operator"; } catch { return "operator"; }
}
export function setRole(role) {
  try { localStorage.setItem("cqa-role", role); } catch { /* private mode: role resets on reload */ }
}
export function getPref(key, fallback = null) {
  try { return localStorage.getItem(`cqa-${key}`) ?? fallback; } catch { return fallback; }
}
export function setPref(key, value) {
  try { localStorage.setItem(`cqa-${key}`, value); } catch { /* private mode */ }
}

async function request(method, path, body) {
  let res;
  try {
    res = await fetch(API_BASE + path, {
      method,
      headers: { "Content-Type": "application/json", "X-Role": getRole() },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error("Сервер не отвечает. Проверьте соединение и попробуйте ещё раз.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg ?? x).join("; ") : d ? JSON.stringify(d) : res.statusText);
  }
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body ?? {}),
};

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const fmt = (n) => (n == null ? "—" : Number(n).toFixed(2));
export const plural = (n, one, few, many) => {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
};

export function verdictText(final) {
  if (!final) return "нет ответа";
  if (final.verdict === "scored") return `${final.step} ${plural(final.step, "балл", "балла", "баллов")}`;
  return { yes: "Да", no: "Нет", na: "Не применимо" }[final.verdict] ?? final.verdict;
}

export const statusText = {
  auto_accepted: "Принято",
  needs_review: "Нужна проверка",
  reviewed: "Проверено человеком",
  failed: "Ошибка оценки",
};

// ---- icons (one stroke set, currentColor) ----
const path = {
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  download: '<path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14"/>',
  upload: '<path d="M12 16V5m0 0-4 4m4-4 4 4M5 20h14"/>',
  play: '<path d="M7 5v14l11-7z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  check: '<path d="m5 12 5 5 9-10"/>',
  alert: '<path d="M12 8v5m0 3.5v.5"/><circle cx="12" cy="12" r="9"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6"/>',
  trash: '<path d="M5 7h14M10 7V5h4v2m-7 0 1 12h8l1-12"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
};
export const icon = (name, w = 1.8) =>
  `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path[name]}</svg>`;

// ---- shared view helpers ----
export const pageHead = (title, lead = "", actions = "", crumbs = "") => `
  <header class="page-head"><div>${crumbs ? `<div class="crumbs">${crumbs}</div>` : ""}<h1>${title}</h1>${lead ? `<p class="lead">${lead}</p>` : ""}</div>
  ${actions ? `<div class="actions">${actions}</div>` : ""}</header>`;

export const loading = (text = "Загрузка") => `<div class="loading">${esc(text)}…</div>`;
export const errorBox = (msg) => `<div class="alert bad">${icon("alert")}<div><b>Не получилось загрузить.</b> ${esc(msg)}</div></div>`;

// Totals are on a 0..100 scale.
export const tone = (v) => (v == null ? "" : v >= 85 ? "" : v >= 60 ? "warn" : "bad");
export const scoreBar = (v) => v == null ? '<span class="faint">—</span>'
  : `<span class="score"><b>${fmt(v)}</b><span class="meter ${tone(v)}"><i style="width:${Math.max(0, Math.min(100, v))}%"></i></span></span>`;

export const statusClass = (s) => (s === "needs_review" ? "warn" : s === "failed" ? "bad" : s === "reviewed" ? "info" : s ? "ok" : "");
export const statusBadge = (s) => s
  ? `<span class="badge dot ${statusClass(s)}">${esc(statusText[s] ?? s)}</span>` : '<span class="faint">Не оценён</span>';

export const channelText = { "agent chat": "Чат", "voice call": "Звонок" };
