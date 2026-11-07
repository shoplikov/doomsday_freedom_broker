import { api, errorBox, esc, icon, loading, pageHead, statusText } from "../api.js";

const STATUS_COLORS = { auto_accepted: "var(--primary)", reviewed: "var(--info)", needs_review: "var(--warn)", failed: "var(--bad)" };
const ROLE_NAMES = { inspector: "M1 Инспектор", advocate: "M2 Адвокат", judge: "M3 Судья" };

// Does the three-model panel work? Agreement of the two independent models, who the judge sides with,
// and accuracy against human gold labels: each model alone vs the panel.
class CqaAnalytics extends HTMLElement {
  async connectedCallback() {
    this.innerHTML = loading("Считаю метрики");
    try {
      this.versions = await api.get("/api/checklists");
      await this.load(this.versions[0]?.version);
    } catch (e) { this.innerHTML = errorBox(e.message); }
  }

  async load(version) {
    const a = await api.get(`/api/analytics?checklist_version=${version}`);
    const pct = (v) => (v == null ? "—" : `${v}%`);
    const acc = a.gold.accuracy_pct;
    const best = Math.max(acc.inspector ?? -1, acc.advocate ?? -1);
    const tone = a.gold.labels === 0 ? "info" : acc.panel > best ? "ok" : "warn";
    const verdict = a.gold.labels === 0 ? "Точность пока не измерить: разметьте эталон в карточках диалогов (поле «Эталон» у каждого критерия)."
      : acc.panel > best ? `Панель точнее лучшей одиночной модели на ${(acc.panel - best).toFixed(1)} п.п. Идея работает.`
      : acc.panel === best ? "Панель на уровне лучшей одиночной модели. Посмотрите ниже, где судья ошибается."
      : "Панель хуже одиночной модели. Проверьте промпт судьи и промахи относительно эталона ниже.";
    const calls = Object.fromEntries(a.calls.map((c) => [c.role, c]));
    const statusTotal = Object.values(a.status).reduce((s, n) => s + n, 0);
    const maxMs = Math.max(1, ...a.calls.map((c) => c.avg_ms ?? 0));
    const kpi = (k, v, s, cls = "") => `<div class="kpi"><span class="k">${k}</span><span class="v ${cls}">${v}</span><span class="s">${s}</span></div>`;
    this.innerHTML = `<div class="page-enter">
      ${pageHead("Аналитика",
        "Работает ли панель из трёх моделей: насколько независимые модели согласны, на чью сторону встаёт судья и точнее ли панель, чем одна модель.",
        `<div class="field" style="width:180px"><label for="ver">Чек-лист</label><select id="ver" data-version>${this.versions.map((v) =>
          `<option value="${v.version}" ${v.version === a.checklist_version ? "selected" : ""}>v${v.version}</option>`).join("")}</select></div>`)}
      <div class="kpis">
        ${kpi("Диалогов", a.dialogs, "последняя оценка каждого")}
        ${kpi("M1 и M2 согласны", pct(a.ab_agreement_pct), "остальное решает судья")}
        ${kpi("Точность панели", pct(acc.panel), `M1 ${pct(acc.inspector)} · M2 ${pct(acc.advocate)} · M3 ${pct(acc.judge)} · меток ${a.gold.labels}`, acc.panel != null && acc.panel > best ? "good" : "")}
        ${kpi("Точность: согласны / спор", `${pct(a.gold.accuracy_when_m1_eq_m2_pct)} / ${pct(a.gold.accuracy_when_split_pct)}`, `согласие — сигнал доверия? (n ${a.gold.agreed_n} / ${a.gold.split_n})`)}
        ${kpi("Стоимость", `$${a.cost_usd}`, `≈ $${a.avg_cost_usd ?? 0} за диалог`)}
        ${kpi("Средний итог", a.avg_total ?? "—", `грубых ошибок: ${a.gross_errors}`)}
      </div>
      <div class="alert ${tone}" style="margin-top:16px">${icon(tone === "ok" ? "check" : "alert")}<div><b>Вывод.</b> ${esc(verdict)}</div></div>

      <div class="grid-2 gap-top">
        <section class="card"><h2 style="margin-bottom:14px">Статусы оценок</h2>
          ${statusTotal ? `<div class="stackbar">${Object.entries(a.status).map(([k, n]) =>
            `<i style="width:${(100 * n) / statusTotal}%;background:${STATUS_COLORS[k] ?? "var(--text-3)"}" title="${esc(statusText[k] ?? k)}: ${n}"></i>`).join("")}</div>
          <div class="legend">${Object.entries(a.status).map(([k, n]) =>
            `<span style="--c:${STATUS_COLORS[k] ?? "var(--text-3)"}">${esc(statusText[k] ?? k)} <b>${n}</b></span>`).join("")}</div>`
            : '<p class="faint">Оценок пока нет.</p>'}
          <h2 style="margin:28px 0 14px">По языкам <span class="faint small">согласие M1 и M2</span></h2>
          <div class="hbars">${Object.entries(a.by_language).map(([k, v]) => `
            <div class="hbar"><span>${esc(k)} <span class="faint small">${v.n} ответов</span></span>
              <span class="meter"><i style="width:${v.ab_agreement_pct ?? 0}%"></i></span><em>${pct(v.ab_agreement_pct)}</em></div>`).join("") || '<p class="faint">—</p>'}</div>
        </section>
        <section class="card"><h2 style="margin-bottom:14px">Вызовы моделей <span class="faint small">среднее время ответа</span></h2>
          <div class="hbars">${["inspector", "advocate", "judge"].filter((r) => calls[r]).map((r) => `
            <div class="hbar"><span>${ROLE_NAMES[r]}</span>
              <span class="meter"><i style="width:${(100 * (calls[r].avg_ms ?? 0)) / maxMs}%"></i></span>
              <em>${((calls[r].avg_ms ?? 0) / 1000).toFixed(1)} с</em></div>`).join("") || '<p class="faint">Вызовов не было.</p>'}</div>
          <div class="table-wrap" style="margin-top:16px"><table class="compact"><thead><tr><th>Роль</th><th class="num">Вызовов</th><th class="num">Повторов</th>
            <th class="num">Сбоев</th><th class="num">Макс., мс</th><th class="num">Токены вх./исх.</th></tr></thead>
          <tbody>${["inspector", "advocate", "judge"].filter((r) => calls[r]).map((r) => `<tr><td>${ROLE_NAMES[r]}</td><td class="num">${calls[r].calls}</td>
            <td class="num">${calls[r].retries}</td><td class="num">${calls[r].errors}</td><td class="num">${calls[r].max_ms}</td>
            <td class="num">${calls[r].tokens_in}/${calls[r].tokens_out}</td></tr>`).join("")}</tbody></table></div>
        </section>
      </div>

      <section class="card gap-top"><div class="card-head"><h2>Карта критериев</h2><p>Проценты по каждому критерию: чем насыщеннее цвет, тем выше</p></div>${this.heatHtml(a.criteria)}</section>

      <details class="card gap-top disclosure"><summary>Все цифры по критериям</summary><div class="table-wrap" style="margin-top:12px">
        <table class="compact"><thead><tr><th>Критерий</th><th class="num">n</th><th class="num">M1=M2</th><th>Судья: оба / M1 / M2 / никто</th>
          <th class="num">Ср. согласие</th><th class="num">На проверку</th><th class="num">Ошибки</th><th class="num">Валидатор</th><th class="num">Изменено людьми</th>
          <th class="num">Эталон n</th><th class="num">Точн. M1</th><th class="num">Точн. M2</th><th class="num">Точн. M3</th><th class="num">Точн. панели</th><th>Ответы ИИ</th></tr></thead>
        <tbody>${a.criteria.map((c) => `<tr>
          <td>${esc(c.criterion_id)} · ${esc(c.title)}</td><td class="num">${c.n}</td><td class="num">${pct(c.ab_agreement_pct)}</td>
          <td>${c.judge_sided.both} / ${c.judge_sided.inspector} / ${c.judge_sided.advocate} / ${c.judge_sided.neither}</td>
          <td class="num">${c.avg_confidence ?? "—"}</td><td class="num">${c.needs_review}</td><td class="num">${c.failed}</td><td class="num">${c.validation_issues}</td>
          <td class="num">${c.human_changed}</td><td class="num">${c.gold_n}</td>
          <td class="num">${pct(c.accuracy_pct.inspector)}</td><td class="num">${pct(c.accuracy_pct.advocate)}</td><td class="num">${pct(c.accuracy_pct.judge)}</td>
          <td class="num"><b>${pct(c.accuracy_pct.panel)}</b></td><td class="small">${esc(JSON.stringify(c.final_dist))}</td></tr>`).join("")}</tbody></table></div></details>

      ${this.listHtml("Промахи относительно эталона", a.gold_misses, (m) => `эталон ${esc(m.gold)} · итог ИИ ${esc(m.final)}`)}
      ${this.listHtml("Где M1 и M2 разошлись", a.disagreements, (d) => `итог ${esc(d.final)} · согласие ${Math.round((d.confidence ?? 0) * 100)}% · ${esc(statusText[d.status] ?? d.status)}`)}
      </div>`;
    this.querySelector("[data-version]").onchange = (e) => this.load(e.target.value);
  }

  // Criteria x metrics, each cell shaded by its percentage (like a contribution graph).
  heatHtml(criteria) {
    if (!criteria.length) return '<p class="faint">Нет данных.</p>';
    const cols = [
      ["M1=M2", (c) => c.ab_agreement_pct],
      ["Согласие", (c) => (c.avg_confidence == null ? null : Math.round(c.avg_confidence * 100))],
      ["Без проверки", (c) => (c.n ? Math.round((100 * (c.n - c.needs_review)) / c.n) : null)],
      ["Точн. M1", (c) => c.accuracy_pct.inspector],
      ["Точн. M2", (c) => c.accuracy_pct.advocate],
      ["Точн. M3", (c) => c.accuracy_pct.judge],
      ["Панель", (c) => c.accuracy_pct.panel],
    ];
    // Stretch 40..100 % over the full colour range: the interesting differences are near the top.
    const shade = (v) => Math.round(8 + 87 * Math.max(0, Math.min(1, (v - 40) / 60)) ** 1.6);
    const cell = (v) => v == null ? '<span class="cell na">—</span>'
      : `<span class="cell ${shade(v) >= 55 ? "hot" : ""}" style="--v:${shade(v)}" title="${v}%">${Math.round(v)}</span>`;
    return `<div class="table-wrap"><div class="heat" style="grid-template-columns:minmax(180px,1.6fr) repeat(${cols.length}, minmax(64px,1fr));min-width:700px">
      <span></span>${cols.map(([h]) => `<span class="h">${h}</span>`).join("")}
      ${criteria.map((c) => `<span class="name" title="${esc(c.title)}"><span class="faint mono">${esc(c.criterion_id)}</span> ${esc(c.title)}</span>
        ${cols.map(([, f]) => cell(f(c))).join("")}`).join("")}</div></div>`;
  }

  listHtml(title, rows, extra) {
    return `<details class="card gap-top disclosure" ${rows.length && rows.length <= 20 ? "open" : ""}><summary>${title}<span class="badge">${rows.length}</span></summary>${rows.length
      ? `<div class="table-wrap" style="margin-top:10px"><table class="compact"><tbody>
      ${rows.slice(0, 100).map((r) => `<tr><td><a class="mono" href="#/dialog/${encodeURIComponent(r.conversation_id)}">${esc(r.conversation_id)}</a></td>
        <td>${esc(r.criterion_id)}</td><td class="faint">${Object.entries(r.votes || {}).map(([k, v]) => `${esc(k)} ${esc(v)}`).join(" · ")}</td><td>${extra(r)}</td></tr>`).join("")}
      </tbody></table></div>` : '<p class="faint" style="margin-top:8px">Таких случаев нет.</p>'}</details>`;
  }
}
customElements.define("cqa-analytics", CqaAnalytics);
