import { api, getRole, setRole } from "./api.js";
import "./components/cqa-conversation-list.js";
import "./components/cqa-dialog-view.js";
import "./components/cqa-review-queue.js";
import "./components/cqa-checklist-editor.js";
import "./components/cqa-analytics.js";

const view = document.getElementById("view");

function route() {
  // Dynamics 365 passes the record id as ?data=<conversation_id>: open its card directly.
  const data = new URLSearchParams(location.search).get("data");
  if (!location.hash && data) {
    location.hash = `#/dialog/${encodeURIComponent(data)}`;
    return;
  }
  const [, page = "dialogs", id] = location.hash.split("/");
  const tag = { dialog: "cqa-dialog-view", review: "cqa-review-queue", checklist: "cqa-checklist-editor", analytics: "cqa-analytics" }[page]
    ?? "cqa-conversation-list";
  const el = document.createElement(tag);
  if (id) el.setAttribute("conversation-id", decodeURIComponent(id));
  view.replaceChildren(el);
  document.querySelectorAll("nav a").forEach((a) =>
    a.classList.toggle("active", a.dataset.page === (page === "dialog" ? "dialogs" : page)));
  document.querySelectorAll("[data-role]").forEach((b) =>
    b.classList.toggle("active", b.dataset.role === getRole()));
}

document.querySelectorAll("[data-role]").forEach((b) =>
  b.addEventListener("click", () => { setRole(b.dataset.role); route(); }));
window.addEventListener("hashchange", route);
route();

api.get("/api/health").then((h) => {
  document.getElementById("health").textContent =
    `LLM: ${h.provider}${h.provider === "mock" ? " (без ключа)" : ` · ${h.models.judge}`}`;
}).catch(() => { document.getElementById("health").textContent = "API недоступен"; });
