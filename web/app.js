import { api, getRole, setRole } from "./api.js";
import "./components/cqa-conversation-list.js";
import "./components/cqa-dialog-view.js";
import "./components/cqa-review-queue.js";
import "./components/cqa-checklist-editor.js";
import "./components/cqa-analytics.js";

const view = document.getElementById("view");
let current = "";

function route() {
  // Dynamics 365 passes the record id as ?data=<conversation_id>: open its card directly.
  const data = new URLSearchParams(location.search).get("data");
  if (!location.hash && data) {
    location.hash = `#/dialog/${encodeURIComponent(data)}`;
    return;
  }
  // A page with unsaved edits (the checklist editor) can keep the user from losing them.
  const active = view.firstElementChild;
  if (active?.isDirty?.() && location.hash !== current
      && !confirm("В чек-листе есть неопубликованные изменения. Уйти и потерять их?")) {
    history.replaceState(null, "", current);
    return;
  }
  current = location.hash;
  const [, page = "dialogs", id] = location.hash.split("/");
  const tag = { dialog: "cqa-dialog-view", review: "cqa-review-queue", checklist: "cqa-checklist-editor", analytics: "cqa-analytics" }[page]
    ?? "cqa-conversation-list";
  const el = document.createElement(tag);
  if (id) el.setAttribute("conversation-id", decodeURIComponent(id));
  view.replaceChildren(el);
  window.scrollTo(0, 0);
  document.querySelectorAll("nav a").forEach((a) => {
    const on = a.dataset.page === (page === "dialog" ? "dialogs" : page);
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  markRole();
  refreshQueueCount();
}

function markRole() {
  document.querySelectorAll("[data-role]").forEach((b) => {
    b.classList.toggle("active", b.dataset.role === getRole());
    b.setAttribute("aria-pressed", String(b.dataset.role === getRole()));
  });
}

// Small badge on «Проверка»: how many things wait for a human.
export async function refreshQueueCount() {
  const el = document.getElementById("queue-count");
  try {
    const q = await api.get("/api/review-queue");
    const n = q.appeals.length + q.low_confidence.length;
    el.textContent = n;
    el.hidden = n === 0;
    el.title = `Апелляций: ${q.appeals.length}, спорных вердиктов: ${q.low_confidence.length}`;
  } catch { el.hidden = true; }
}
window.addEventListener("cqa:queue-changed", refreshQueueCount);

document.querySelectorAll("[data-role]").forEach((b) =>
  b.addEventListener("click", () => {
    if (b.dataset.role === getRole()) return;
    setRole(b.dataset.role);
    markRole();
    view.firstElementChild?.onRoleChange?.();
  }));
window.addEventListener("hashchange", route);
window.addEventListener("beforeunload", (e) => { if (view.firstElementChild?.isDirty?.()) e.preventDefault(); });
route();

// Theme: no colour transitions during the swap, or every surface fades at once.
document.getElementById("theme").addEventListener("click", () => {
  const root = document.documentElement;
  const next = root.dataset.theme === "dark" ? "light" : "dark";
  const off = document.createElement("style");
  off.textContent = "*,*::before,*::after{transition:none!important}";
  document.head.appendChild(off);
  root.dataset.theme = next;
  void getComputedStyle(root).opacity;
  requestAnimationFrame(() => requestAnimationFrame(() => off.remove()));
  try { localStorage.setItem("cqa-theme", next); } catch { /* private mode: theme resets on reload */ }
});

const health = document.getElementById("health");
api.get("/api/health").then((h) => {
  const models = [...new Set(Object.values(h.models))];
  health.classList.add(h.provider === "mock" ? "mock" : "on");
  health.textContent = h.provider === "mock" ? "Тестовый режим, без модели" : models.join(" · ");
  health.title = h.provider === "mock" ? "LLM_PROVIDER=mock: ответы ненастоящие"
    : `Инспектор: ${h.models.inspector}\nАдвокат: ${h.models.advocate}\nСудья: ${h.models.judge}`;
}).catch(() => { health.classList.add("down"); health.textContent = "Сервер недоступен"; });
