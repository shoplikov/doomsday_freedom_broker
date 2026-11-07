import { api, esc } from "../api.js";

// Does the three-model panel work? Agreement of the two independent models, who the judge sides with,
// and accuracy against human gold labels: each model alone vs the panel.
class CqaAnalytics extends HTMLElement {
  async connectedCallback() {
    this.innerHTML = '<div class="panel muted">Считаю…</div>';
    try {
      this.versions = await api.get("/api/checklists");
      await this.load(this.versions[0]?.version);
    } catch (e) { this.innerHTML = `<div class="panel err">${esc(e.message)}</div>`; }
  }

  async load(version) {
    const a = await api.get(`/api/analytics?checklist_version=${version}`);
    const pct = (v) => (v == null ? "—" : `${v}%`);
    const acc = a.gold.accuracy_pct;
    const best = Math.max(acc.inspector ?? -1, acc.advocate ?? -1);
    const verdict = a.gold.labels === 0 ? "Разметьте эталон в карточках диалогов (селектор «эталон»), чтобы измерить точность."
      : acc.panel > best ? `Панель точнее лучшей одиночной модели на ${(acc.panel - best).toFixed(1)} п.п. — идея работает.`
      : acc.panel === best ? "Панель на уровне лучшей одиночной модели: смотрите, где судья ошибается (список ниже)."
      : "Панель хуже одиночной модели: проверьте промпт судьи и ошибки эталона ниже.";
    const calls = Object.fromEntries(a.calls.map((c) => [c.role, c]));
    this.innerHTML = `
      <div class="panel row">
        <b>Аналитика панели M1 · M2 → M3</b>
        <select data-version>${this.versions.map((v) => `<option value="${v.version}" ${v.version === a.checklist_version ? "selected" : ""}>чек-лист v${v.version}</option>`).join("")}</select>
        <span class="muted small">последняя оценка каждого диалога этой версии</span>
      </div>
      <div class="kpis">
        <div class="kpi">Диалогов<b>${a.dialogs}</b><span class="small muted">${esc(JSON.stringify(a.status))}</span></div>
        <div class="kpi">M1 = M2 (независимо)<b>${pct(a.ab_agreement_pct)}</b><span class="small muted">остальное решает судья</span></div>
        <div class="kpi">Эталон: точность панели<b class="${acc.panel != null && acc.panel > best ? "good" : ""}">${pct(acc.panel)}</b>
          <span class="small muted">M1 ${pct(acc.inspector)} · M2 ${pct(acc.advocate)} · M3 ${pct(acc.judge)} · меток ${a.gold.labels}</span></div>
        <div class="kpi">Точность: M1=M2 / спор<b>${pct(a.gold.accuracy_when_m1_eq_m2_pct)} / ${pct(a.gold.accuracy_when_split_pct)}</b>
          <span class="small muted">согласие = сигнал доверия? (n ${a.gold.agreed_n} / ${a.gold.split_n})</span></div>
        <div class="kpi">Стоимость<b>$${a.cost_usd}</b><span class="small muted">≈ $${a.avg_cost_usd ?? 0} за диалог</span></div>
        <div class="kpi">Средний итог<b>${a.avg_total ?? "—"}</b><span class="small muted">грубых ошибок: ${a.gross_errors}</span></div>
      </div>
      <div class="panel" style="margin-top:12px"><b>${esc(verdict)}</b></div>
      <div class="panel"><h3>По критериям</h3>
        <table class="small"><thead><tr><th>Критерий</th><th>n</th><th>M1=M2</th><th>Судья: оба / M1 / M2 / никто</th>
          <th>Ср. согласие</th><th>На проверку</th><th>Ошибки</th><th>Валидатор</th><th>Изменено людьми</th>
          <th>Эталон n</th><th>Точн. M1</th><th>Точн. M2</th><th>Точн. M3</th><th>Точн. панели</th><th>Ответы ИИ</th></tr></thead>
        <tbody>${a.criteria.map((c) => `<tr>
          <td>${esc(c.criterion_id)} · ${esc(c.title)}</td><td>${c.n}</td><td>${pct(c.ab_agreement_pct)}</td>
          <td>${c.judge_sided.both} / ${c.judge_sided.inspector} / ${c.judge_sided.advocate} / ${c.judge_sided.neither}</td>
          <td>${c.avg_confidence ?? "—"}</td><td>${c.needs_review}</td><td>${c.failed}</td><td>${c.validation_issues}</td>
          <td>${c.human_changed}</td><td>${c.gold_n}</td>
          <td>${pct(c.accuracy_pct.inspector)}</td><td>${pct(c.accuracy_pct.advocate)}</td><td>${pct(c.accuracy_pct.judge)}</td>
          <td><b>${pct(c.accuracy_pct.panel)}</b></td><td>${esc(JSON.stringify(c.final_dist))}</td></tr>`).join("")}</tbody></table></div>
      <div class="panel"><h3>По языкам</h3>
        <table class="small"><thead><tr><th>Язык</th><th>Ответов</th><th>M1=M2</th><th>Эталон n</th><th>Точн. панели</th></tr></thead>
        <tbody>${Object.entries(a.by_language).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.n}</td><td>${pct(v.ab_agreement_pct)}</td>
          <td>${v.gold_n}</td><td>${pct(v.panel_accuracy_pct)}</td></tr>`).join("")}</tbody></table></div>
      <div class="panel"><h3>Вызовы LLM</h3>
        <table class="small"><thead><tr><th>Роль</th><th>Вызовов</th><th>Повторов</th><th>Сбоев API</th><th>Ср. мс</th><th>Макс. мс</th><th>Токены in/out</th></tr></thead>
        <tbody>${["inspector", "advocate", "judge"].filter((r) => calls[r]).map((r) => `<tr><td>${r}</td><td>${calls[r].calls}</td>
          <td>${calls[r].retries}</td><td>${calls[r].errors}</td><td>${Math.round(calls[r].avg_ms ?? 0)}</td><td>${calls[r].max_ms}</td>
          <td>${calls[r].tokens_in}/${calls[r].tokens_out}</td></tr>`).join("")}</tbody></table></div>
      ${this.listHtml("Промахи относительно эталона", a.gold_misses, (m) => `эталон ${esc(m.gold)} · итог ИИ ${esc(m.final)}`)}
      ${this.listHtml("Где M1 и M2 разошлись", a.disagreements, (d) => `итог ${esc(d.final)} · согласие ${Math.round((d.confidence ?? 0) * 100)}% · ${esc(d.status)}`)}`;
    this.querySelector("[data-version]").onchange = (e) => this.load(e.target.value);
  }

  listHtml(title, rows, extra) {
    return `<details class="panel" ${rows.length && rows.length <= 20 ? "open" : ""}><summary><b>${title} (${rows.length})</b></summary>${rows.length ? `<table class="small"><tbody>
      ${rows.slice(0, 100).map((r) => `<tr><td><a href="#/dialog/${encodeURIComponent(r.conversation_id)}">${esc(r.conversation_id)}</a></td>
        <td>${esc(r.criterion_id)}</td><td>${esc(JSON.stringify(r.votes))}</td><td>${extra(r)}</td></tr>`).join("")}
      </tbody></table>` : '<span class="muted">Нет.</span>'}</details>`;
  }
}
customElements.define("cqa-analytics", CqaAnalytics);
