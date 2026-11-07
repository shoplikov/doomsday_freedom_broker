import { api, channelText, esc, fmt, pageHead, scoreBar, statusBadge } from "../api.js";

// Dialog list: import a dataset by group_id, run a batch evaluation, filter, open a card.
class CqaConversationList extends HTMLElement {
  async connectedCallback() {
    const health = await api.get("/api/health").catch(() => ({}));
    this.innerHTML = `
      ${pageHead("Контроль качества", "Диалоги",
        "Каждый чат и звонок оценивают три модели: инспектор, адвокат и судья. Код считает баллы, спорное решают люди.")}
      <div data-stats></div>
      <div class="panel tight row">
        <input name="group" value="${health.dataset_api ? "DEV" : "SAMPLE"}" size="9" aria-label="group_id" class="mono">
        <button data-act="import">Загрузить набор</button>
        <button data-act="run" class="primary">Оценить все</button>
        <button data-act="export" title="Файл для организаторов: results/dev или results/prod">results.json</button>
        <span class="muted small" data-progress></span>
        <span class="grow"></span>
        <select name="language" aria-label="Язык"><option value="">Все языки</option><option>RU</option><option>KZ</option><option>EN</option></select>
        <select name="channel" aria-label="Канал"><option value="">Все каналы</option><option value="agent chat">Чат</option><option value="voice call">Звонок</option></select>
      </div>
      <div class="panel"><div data-list class="loading">Загрузка</div></div>`;
    this.querySelector('[data-act="import"]').onclick = () => this.importGroup();
    this.querySelector('[data-act="run"]').onclick = () => this.runAll();
    this.querySelector('[data-act="export"]').onclick = () => this.exportResults();
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

  // The organisers' results file (format 1.0) for the group in the input, as a download.
  async exportResults() {
    const group = this.querySelector('[name="group"]').value.trim();
    try {
      const doc = await api.get(`/api/results/${encodeURIComponent(group)}`);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2) + "\n"], { type: "application/json" }));
      a.download = "results.json";
      a.click();
      URL.revokeObjectURL(a.href);
      const errors = doc.results.filter((r) => r.status !== "ok").length;
      this.progress(`results.json: ${doc.results.length} диалогов, ошибок ${errors}`);
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

  stats(rows) {
    const scored = rows.filter((r) => r.total != null);
    const avg = scored.length ? scored.reduce((s, r) => s + r.total, 0) / scored.length : null;
    const count = (st) => rows.filter((r) => r.status === st).length;
    const bins = Array.from({ length: 10 }, (_, i) => scored.filter((r) => Math.min(9, Math.floor(r.total / 10)) === i).length);
    const peak = Math.max(1, ...bins);
    return `<div class="list-stats">
      <div class="kpis">
        <div class="kpi feature">Средний итог<b>${avg == null ? "—" : fmt(avg)}</b><span class="small muted">по ${scored.length} оценённым</span></div>
        <div class="kpi">Диалогов<b>${rows.length}</b><span class="small muted">${rows.length - scored.length} без оценки</span></div>
        <div class="kpi">Принято автоматически<b class="good">${count("auto_accepted") + count("reviewed")}</b><span class="small muted">из них проверено людьми: ${count("reviewed")}</span></div>
        <div class="kpi">Ждут человека<b>${count("needs_review")}</b><span class="small muted">грубых ошибок: ${rows.filter((r) => r.gross_error).length}</span></div>
      </div>
      <div class="kpi histo-card">Распределение итогов
        <div class="histo">${bins.map((n, i) => `<div title="${i * 10}–${i * 10 + 10}: ${n}">
          <i class="${n ? "" : "zero"}" style="height:${(n / peak) * 100}%"></i><span>${i * 10}</span></div>`).join("")}</div>
      </div></div>`;
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
      this.querySelector("[data-stats]").innerHTML = rows.length ? this.stats(rows) : "";
      if (!rows.length) {
        box.className = "empty";
        box.innerHTML = "<b>Пока пусто</b>Загрузите набор кнопкой выше: SAMPLE работает без сети.";
        return;
      }
      box.className = "table-wrap";
      box.innerHTML = `<table>
        <thead><tr><th>ID</th><th>Канал</th><th>Язык</th><th>Итог</th><th>Статус</th><th>Грубая ошибка</th><th>Чек-лист</th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr class="click" data-id="${esc(r.id)}">
            <td class="mono">${esc(r.id)}</td>
            <td>${esc(channelText[r.channel] ?? r.channel)}</td>
            <td><span class="badge">${esc(r.language)}</span></td>
            <td style="min-width:170px">${scoreBar(r.total)}</td>
            <td>${statusBadge(r.status)}</td>
            <td>${r.gross_error ? '<span class="badge bad">да</span>' : '<span class="faint">—</span>'}</td>
            <td class="muted">${r.checklist_version ? `v${r.checklist_version}` : ""}</td>
          </tr>`).join("")}</tbody></table>`;
      box.querySelectorAll("tr[data-id]").forEach((tr) =>
        (tr.onclick = () => (location.hash = `#/dialog/${encodeURIComponent(tr.dataset.id)}`)));
    } catch (e) { box.className = ""; box.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  }
}
customElements.define("cqa-conversation-list", CqaConversationList);
