import { api, esc, loading, pageHead } from "../api.js";

// Reviewer inbox: open appeals + low-confidence AI verdicts. Decisions are made on the dialog card.
class CqaReviewQueue extends HTMLElement {
  async connectedCallback() {
    this.innerHTML = loading();
    try {
      const q = await api.get("/api/review-queue");
      const open = (cid) => `#/dialog/${encodeURIComponent(cid)}`;
      const empty = (t) => `<div class="empty"><b>Всё чисто</b>${t}</div>`;
      this.innerHTML = `
        ${pageHead("Люди решают спорное", "Очередь проверки",
          "Апелляции операторов и вердикты, где модели не договорились. Решение принимается в карточке диалога, всё пишется в журнал.")}
        <div class="kpis">
          <div class="kpi feature">Апелляции<b>${q.appeals.length}</b><span class="small muted">операторы не согласны с оценкой</span></div>
          <div class="kpi">Низкая уверенность ИИ<b>${q.low_confidence.length}</b><span class="small muted">согласие ниже порога</span></div>
        </div>
        <div class="grid-2">
          <div class="panel"><h3>Апелляции <span class="muted">${q.appeals.length}</span></h3>
            ${q.appeals.length ? q.appeals.map((a) => `
              <div class="q-item" data-href="${open(a.conversation_id)}">
                <span class="ico">!</span>
                <div class="grow"><div class="row"><b class="mono">${esc(a.conversation_id)}</b><span class="badge">${esc(a.criterion_id)}</span>
                  <span class="grow"></span><span class="small faint">${esc(a.created_at)}</span></div>
                  <div style="margin-top:4px">«${esc(a.comment)}»</div>
                  <div class="small muted">подал: ${esc(a.filed_by)}</div></div>
              </div>`).join("") : empty("Нет открытых апелляций.")}</div>
          <div class="panel"><h3>Низкая уверенность ИИ <span class="muted">${q.low_confidence.length}</span></h3>
            ${q.low_confidence.length ? q.low_confidence.map((x) => `
              <div class="q-item" data-href="${open(x.conversation_id)}">
                <span class="ico lime">${Math.round(x.confidence * 100)}</span>
                <div class="grow"><div class="row"><b class="mono">${esc(x.conversation_id)}</b><span class="badge">${esc(x.criterion_id)}</span></div>
                  <div style="margin-top:4px">${esc(x.title)}</div>
                  <div class="small muted">голоса: ${Object.entries(x.votes || {}).map(([k, v]) => `${esc(k)} ${esc(v)}`).join(" · ")}</div></div>
              </div>`).join("") : empty("Модели во всём согласны.")}</div>
        </div>`;
      this.querySelectorAll("[data-href]").forEach((el) => (el.onclick = () => (location.hash = el.dataset.href)));
    } catch (e) { this.innerHTML = `<div class="panel err">${esc(e.message)}</div>`; }
  }
}
customElements.define("cqa-review-queue", CqaReviewQueue);
