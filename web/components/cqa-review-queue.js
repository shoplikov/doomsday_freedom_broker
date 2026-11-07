import { api, esc } from "../api.js";

// Reviewer inbox: open appeals + low-confidence AI verdicts. Decisions are made on the dialog card.
class CqaReviewQueue extends HTMLElement {
  async connectedCallback() {
    this.innerHTML = '<div class="panel muted">Загрузка…</div>';
    try {
      const q = await api.get("/api/review-queue");
      const open = (cid) => `#/dialog/${encodeURIComponent(cid)}`;
      this.innerHTML = `
        <div class="panel"><h3>Апелляции (${q.appeals.length})</h3>
          ${q.appeals.length ? `<table><thead><tr><th>Диалог</th><th>Критерий</th><th>Комментарий</th><th>Подал</th><th>Когда</th></tr></thead><tbody>
          ${q.appeals.map((a) => `<tr class="click" data-href="${open(a.conversation_id)}"><td>${esc(a.conversation_id)}</td><td>${esc(a.criterion_id)}</td>
            <td>${esc(a.comment)}</td><td>${esc(a.filed_by)}</td><td class="small">${esc(a.created_at)}</td></tr>`).join("")}
          </tbody></table>` : '<span class="muted">Нет открытых апелляций.</span>'}</div>
        <div class="panel"><h3>Низкая уверенность ИИ (${q.low_confidence.length})</h3>
          ${q.low_confidence.length ? `<table><thead><tr><th>Диалог</th><th>Критерий</th><th>Согласие</th><th>Голоса</th></tr></thead><tbody>
          ${q.low_confidence.map((x) => `<tr class="click" data-href="${open(x.conversation_id)}"><td>${esc(x.conversation_id)}</td>
            <td>${esc(x.criterion_id)} · ${esc(x.title)}</td><td>${Math.round(x.confidence * 100)}%</td>
            <td class="small">${esc(JSON.stringify(x.votes))}</td></tr>`).join("")}
          </tbody></table>` : '<span class="muted">Пусто.</span>'}</div>`;
      this.querySelectorAll("[data-href]").forEach((tr) => (tr.onclick = () => (location.hash = tr.dataset.href)));
    } catch (e) { this.innerHTML = `<div class="panel err">${esc(e.message)}</div>`; }
  }
}
customElements.define("cqa-review-queue", CqaReviewQueue);
