// The single API adapter. Components never call fetch directly, so pointing the UI at another
// backend (e.g. from a Dynamics 365 web resource) is one setting: window.CQA_API_BASE.
export const API_BASE = window.CQA_API_BASE ?? "";

export function getRole() {
  try { return localStorage.getItem("cqa-role") || "operator"; } catch { return "operator"; }
}
export function setRole(role) {
  try { localStorage.setItem("cqa-role", role); } catch { /* private mode: role resets on reload */ }
}

async function request(method, path, body) {
  const res = await fetch(API_BASE + path, {
    method,
    headers: { "Content-Type": "application/json", "X-Role": getRole() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : d ? JSON.stringify(d) : res.statusText);
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

export function verdictText(final) {
  if (!final) return "нет ответа";
  if (final.verdict === "scored") return `ступень ${final.step}`;
  return { yes: "Да", no: "Нет", na: "N/A" }[final.verdict] ?? final.verdict;
}

export const statusText = {
  auto_accepted: "принято автоматически",
  needs_review: "нужна проверка",
  reviewed: "проверено человеком",
  failed: "ошибка оценки",
};
