import { api, errorBox, esc, getRole, icon, loading, pageHead, plural } from "../api.js";

// Reviewer inbox: open appeals + low-confidence AI verdicts. Decisions are made on the dialog card.
class CqaReviewQueue extends HTMLElement {
  async connectedCallback() {
    this.innerHTML = loading();
    try {
      this.q = await api.get("/api/review-queue");
      this.render();
    } catch (e) { this.innerHTML = errorBox(e.message); }
  }

  onRoleChange() { if (this.q) this.render(); }

  render() {
    const q = this.q;
    const open = (cid, crit) => `#/dialog/${encodeURIComponent(cid)}`;
    const total = q.appeals.length + q.low_confidence.length;
    const empty = (title, text) => `<div class="empty"><b>${title}</b><p>${text}</p></div>`;
    const go = `<span class="go">${icon("chevron")}</span>`;
    this.innerHTML = `<div class="page-enter">
      ${pageHead("Проверка",
        total ? `${total} ${plural(total, "вопрос ждёт", "вопроса ждут", "вопросов ждут")} человека. Откройте диалог, прочитайте цитаты и нажмите «Решить» у критерия — решение и причина попадут в журнал.`
          : "Здесь собираются апелляции операторов и вердикты, где модели не договорились.")}
      ${getRole() !== "reviewer" && total ? `<div class="alert info" style="margin-bottom:16px">${icon("alert")}<div>Решения принимает проверяющий.
        Переключите роль в шапке на «Проверяющий», чтобы увидеть кнопку «Решить».</div></div>` : ""}
      <div class="grid-2">
        <section class="card"><div class="card-head"><h2>Апелляции <span class="faint">${q.appeals.length}</span></h2><p>Оператор не согласен с оценкой</p></div>
          ${q.appeals.length ? q.appeals.map((a) => `
            <div class="q-item" data-href="${open(a.conversation_id)}" tabindex="0" role="link">
              <span class="ico">${icon("alert")}</span>
              <div><div class="row tight"><b class="mono">${esc(a.conversation_id)}</b><span class="badge">${esc(a.criterion_id)}</span>
                <span class="small faint">${esc(a.filed_by)} · ${esc(String(a.created_at).slice(0, 16).replace("T", " "))}</span></div>
                <p class="body">«${esc(a.comment)}»</p></div>${go}
            </div>`).join("") : empty("Апелляций нет", "Когда оператор оспорит оценку, она появится здесь.")}</section>
        <section class="card"><div class="card-head"><h2>Спорные вердикты <span class="faint">${q.low_confidence.length}</span></h2><p>Модели разошлись во мнениях</p></div>
          ${q.low_confidence.length ? q.low_confidence.map((x) => `
            <div class="q-item" data-href="${open(x.conversation_id)}" tabindex="0" role="link">
              <span class="ico info" title="Согласие моделей">${Math.round(x.confidence * 100)}%</span>
              <div><div class="row tight"><b class="mono">${esc(x.conversation_id)}</b><span class="badge">${esc(x.criterion_id)}</span></div>
                <p class="body">${esc(x.title)}</p>
                <p class="small faint">Голоса: ${Object.entries(x.votes || {}).map(([k, v]) => `${esc({ inspector: "M1", advocate: "M2", judge: "M3" }[k] ?? k)} ${esc(v)}`).join(" · ")}</p></div>${go}
            </div>`).join("") : empty("Всё согласовано", "Модели согласны во всех вердиктах. Новые спорные случаи появятся после оценки.")}</section>
      </div></div>`;
    this.querySelectorAll("[data-href]").forEach((el) => {
      el.onclick = () => (location.hash = el.dataset.href);
      el.onkeydown = (e) => { if (e.key === "Enter") location.hash = el.dataset.href; };
    });
  }
}
customElements.define("cqa-review-queue", CqaReviewQueue);
