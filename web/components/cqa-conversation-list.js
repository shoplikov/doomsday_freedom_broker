import { api, esc, fmt, statusText } from "../api.js";

// Dialog list: import a dataset by group_id, run a batch evaluation, filter, open a card.
class CqaConversationList extends HTMLElement {
  async connectedCallback() {
    const health = await api.get("/api/health").catch(() => ({}));
    this.innerHTML = `
      <div class="panel row">
        <input name="group" value="${health.dataset_api ? "DEV" : "SAMPLE"}" size="10" aria-label="group_id">
        <button data-act="import">Загрузить набор</button>
        <button data-act="run" class="primary">Оценить все</button>
        <span class="muted" data-progress></span>
        <span style="flex:1"></span>
        <select name="language"><option value="">Все языки</option><option>RU</option><option>KZ</option><option>EN</option></select>
        <select name="channel"><option value="">Все каналы</option><option value="agent chat">Чат</option><option value="voice call">Звонок</option></select>
      </div>
      <div class="panel"><div data-list class="muted">Загрузка…</div></div>`;
    this.querySelector('[data-act="import"]').onclick = () => this.importGroup();
    this.querySelector('[data-act="run"]').onclick = () => this.runAll();
    this.querySelectorAll("select").forEach((s) => (s.onchange = () => this.load()));
    this.load();
  }

  progress(text) { this.querySelector("[data-progress]").textContent = text; }

  async importGroup() {
    const group = this.querySelector('[name="group"]').value.trim();
    this.progress("Загружаю…");
    try {
      const r = await api.post(`/api/datasets/${encodeURIComponent(group)}/import`);
      this.progress(`Загружено: ${r.imported}`);
      this.load();
    } catch (e) { this.progress(`Ошибка: ${e.message}`); }
  }

  async runAll() {
    try {
      let run = await api.post("/api/runs", {});
      while (run.status === "running") {
        this.progress(`Оценка: ${run.done}/${run.total}${run.failed.length ? `, ошибок ${run.failed.length}` : ""}`);
        await new Promise((r) => setTimeout(r, 1500));
        run = await api.get(`/api/runs/${run.id}`);
      }
      this.progress(`Готово: ${run.done}/${run.total}${run.failed.length ? `, ошибок ${run.failed.length}` : ""}`);
      this.load();
    } catch (e) { this.progress(`Ошибка: ${e.message}`); }
  }

  async load() {
    const q = new URLSearchParams();
    for (const name of ["language", "channel"]) {
      const v = this.querySelector(`[name="${name}"]`).value;
      if (v) q.set(name, v);
    }
    const box = this.querySelector("[data-list]");
    try {
      const rows = await api.get(`/api/conversations?${q}`);
      if (!rows.length) { box.textContent = "Диалогов нет. Загрузите набор (SAMPLE работает без сети)."; return; }
      box.className = "";
      box.innerHTML = `<table>
        <thead><tr><th>ID</th><th>Канал</th><th>Язык</th><th>Итог</th><th>Статус</th><th>Грубая ошибка</th><th>Чек-лист</th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr class="click" data-id="${esc(r.id)}">
            <td>${esc(r.id)}</td><td>${esc(r.channel)}</td><td>${esc(r.language)}</td>
            <td><b>${fmt(r.total)}</b></td>
            <td>${r.status ? `<span class="badge ${r.status === "needs_review" ? "warn" : r.status === "failed" ? "bad" : "ok"}">${esc(statusText[r.status] ?? r.status)}</span>` : '<span class="muted">не оценён</span>'}</td>
            <td>${r.gross_error ? '<span class="badge bad">да</span>' : ""}</td>
            <td>${r.checklist_version ? `v${r.checklist_version}` : ""}</td>
          </tr>`).join("")}</tbody></table>`;
      box.querySelectorAll("tr[data-id]").forEach((tr) =>
        (tr.onclick = () => (location.hash = `#/dialog/${encodeURIComponent(tr.dataset.id)}`)));
    } catch (e) { box.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  }
}
customElements.define("cqa-conversation-list", CqaConversationList);
