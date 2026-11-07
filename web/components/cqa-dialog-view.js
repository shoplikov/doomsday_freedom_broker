import { api, channelText, esc, fmt, getRole, loading, statusBadge, tone, verdictText } from "../api.js";

const speakerText = { operator: "Оператор", client: "Клиент", system: "Система", bot: "Бот" };
const roleShort = { inspector: "M1", advocate: "M2", judge: "M3" };

const pretty = (text) => { try { return JSON.stringify(JSON.parse(text), null, 2); } catch { return text ?? ""; } };

// Dialog card: transcript (+ audio) on the left, evaluation with evidence on the right.
// Operator can appeal a criterion; Reviewer decides appeals and low-confidence items.
class CqaDialogView extends HTMLElement {
  connectedCallback() {
    this.id_ = this.getAttribute("conversation-id");
    this.showRaw = false;
    this.innerHTML = loading();
    this.load();
  }

  async load() {
    try {
      const id = encodeURIComponent(this.id_);
      [this.data, this.gold] = await Promise.all([
        api.get(`/api/conversations/${id}`),
        api.get(`/api/gold?conversation_id=${id}`).then((rows) => Object.fromEntries(rows.map((g) => [g.criterion_id, g.label]))),
      ]);
      this.openTraces = this.openTraces ?? new Set();
      this.render();
    } catch (e) { this.innerHTML = `<div class="panel err">${esc(e.message)}</div>`; }
  }

  async evaluate() {
    const btn = this.querySelector('[data-act="evaluate"]');
    btn.disabled = true; btn.textContent = "Оцениваю…";
    try { await api.post("/api/evaluations", { conversation_id: this.id_ }); await this.load(); }
    catch (e) { btn.disabled = false; btn.textContent = `Ошибка: ${e.message}`; }
  }

  render() {
    const d = this.data;
    const ev = d.latest;
    this.innerHTML = `
      <section class="hero" style="margin-bottom:20px">
        <div><a href="#/dialogs" class="kicker">Диалоги</a>
          <h1 class="display" style="font-size:clamp(28px,4vw,42px)">${esc(d.id)}</h1>
          <div class="row"><span class="badge">${esc(channelText[d.channel] ?? d.channel)}</span><span class="badge">${esc(d.language)}</span>
            <span class="muted small">${d.messages.length} сообщений</span></div></div>
        <div class="hero-actions">
          <label class="small muted"><input type="checkbox" data-raw ${this.showRaw ? "checked" : ""}> исходный текст (ИИ видел маскированный)</label>
          <button data-act="evaluate" class="primary">${ev ? "Переоценить" : "Оценить"}</button>
        </div>
      </section>
      <div class="split">
        <div class="panel sticky-col">
          ${d.audio_url ? `<audio controls preload="none" src="${esc(d.audio_url)}"></audio>` : ""}
          ${this.signalsHtml(d.signals)}
          <div data-transcript>${d.messages.map((m) => `
            <div class="msg ${esc(m.speaker)}" id="msg-${esc(m.mid)}" data-t="${m.t_sec ?? ""}">
              <div class="meta"><b>${esc(speakerText[m.speaker] ?? m.speaker)}</b><span>${esc(m.mid)}</span><span>${esc(m.ts_raw)}</span>${m.lang ? `<span>${esc(m.lang)}</span>` : ""}</div>
              <div class="bubble">${esc(this.showRaw ? m.text : m.text_masked)}</div>
            </div>`).join("")}</div>
        </div>
        <div>${ev ? this.evaluationHtml(ev) : '<div class="panel empty"><b>Ещё не оценён</b>Нажмите «Оценить», и три модели разберут диалог по чек-листу.</div>'}</div>
      </div>`;
    this.querySelector('[data-act="evaluate"]').onclick = () => this.evaluate();
    this.querySelector("[data-raw]").onchange = (e) => { this.showRaw = e.target.checked; this.render(); };
    this.querySelectorAll("[data-mid]").forEach((b) => (b.onclick = () => this.jump(b.dataset.mid)));
    this.querySelectorAll("[data-appeal]").forEach((b) => (b.onclick = () => this.appealForm(b.dataset.appeal)));
    this.querySelectorAll("[data-decide]").forEach((b) => (b.onclick = () => this.decisionForm(b.dataset.decide)));
    this.querySelectorAll("[data-trace-toggle]").forEach((b) => (b.onclick = () => {
      const cid = b.dataset.traceToggle;
      this.openTraces.has(cid) ? this.openTraces.delete(cid) : this.openTraces.add(cid);
      this.render();
    }));
    this.querySelectorAll("[data-trace-id]").forEach((b) => (b.onclick = () => this.showTrace(b)));
    this.querySelectorAll("[data-gold]").forEach((s) => (s.onchange = async () => {
      try {
        await api.post("/api/gold", { conversation_id: this.id_, criterion_id: s.dataset.gold, label: s.value });
        this.gold[s.dataset.gold] = s.value || undefined;
        s.nextElementSibling.textContent = "сохранено";
      } catch (e) { s.nextElementSibling.textContent = e.message; }
    }));
  }

  goldOptions(c) {
    const opts = c.type === "scale" ? c.steps.map((s) => [String(s), `ступень ${s}`]) : [["yes", "Да"], ["no", "Нет"]];
    if (c.na_allowed) opts.push(["na", "N/A"]);
    const cur = this.gold[c.criterion_id] ?? "";
    return `<option value="">—</option>` + opts.map(([v, l]) =>
      `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(l)}</option>`).join("");
  }

  // Full panel trace for one criterion: what M1 and M2 said independently, what M3 decided, and the raw calls.
  traceHtml(c) {
    const roles = ["inspector", "advocate", "judge"];
    const names = { inspector: "M1 Инспектор (строгий)", advocate: "M2 Адвокат (за оператора)", judge: "M3 Судья (решение)" };
    const cell = (role) => {
      const r = c.roles?.[role];
      const errs = c.errors?.[role];
      const ids = c.trace_ids?.[role] ?? [];
      if (!r && !errs && !ids.length) return '<td class="muted">не вызывался (M1 и M2 согласны)</td>';
      return `<td>
        ${r ? `<b>${esc(c.votes?.[role] ?? "—")}</b>
          ${r.atoms ? `<div class="small">${Object.entries(r.atoms).map(([k, v]) => `${v ? "✓" : "✗"} ${esc(k)}`).join("<br>")}</div>` : ""}
          ${(r.evidence || []).map((e) => `<div class="small">${esc(e.msg)}: «${esc(e.quote)}»</div>`).join("")}
          ${r.checked_range ? `<div class="small muted">проверено ${esc(r.checked_range.join("–"))}</div>` : ""}
          ${r.na_reason ? `<div class="small muted">N/A: ${esc(r.na_reason)}</div>` : ""}
          <div class="small">${esc(r.rationale)}</div>` : '<b class="err">нет валидного ответа</b>'}
        ${errs ? `<div class="small err">${errs.map(esc).join("<br>")}</div>` : ""}
        <div class="small">${ids.map((id, i) => `<button class="chip" data-trace-id="${id}">вызов #${id}${i ? " (повтор)" : ""}</button>`).join("")}</div>
      </td>`;
    };
    return `<div class="trace">
      <table class="small"><thead><tr>${roles.map((r) => `<th>${names[r]}</th>`).join("")}</tr></thead>
        <tbody><tr>${roles.map(cell).join("")}</tr></tbody></table>
      <div class="small muted">Источник итога: ${esc(c.source ?? "—")} · согласие ${Math.round((c.confidence ?? 0) * 100)}%
        · порог авто-принятия задаётся TRUST_THRESHOLD</div>
      <div data-trace-body></div></div>`;
  }

  async showTrace(btn) {
    const box = btn.closest(".trace").querySelector("[data-trace-body]");
    try {
      const t = await api.get(`/api/traces/${btn.dataset.traceId}`);
      box.innerHTML = `<div class="form small">
        <b>#${t.id} · ${esc(t.role)} · попытка ${t.attempt} · ${esc(t.model)} · ${t.latency_ms} мс · токены ${t.tokens_in}/${t.tokens_out}</b>
        ${t.error ? `<div class="err">${esc(t.error)}</div>` : ""}
        ${t.validation && Object.keys(t.validation).length ? `<div class="err">валидатор: ${esc(JSON.stringify(t.validation))}</div>` : ""}
        <details><summary>Запрос (${t.request.length} сообщений)</summary>${t.request.map((m) =>
          `<div class="muted">${esc(m.role)}</div><pre>${esc(m.content)}</pre>`).join("")}</details>
        <details open><summary>Ответ модели</summary><pre>${esc(pretty(t.response))}</pre></details></div>`;
    } catch (e) { box.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  }

  signalsHtml(s) {
    const lp = (p) => Object.entries(p || {}).map(([k, v]) => `${k} ${v}%`).join(" · ") || "—";
    return `<div class="signals small">
      <span class="badge">клиент: ${esc(lp(s.language_profile.client))}</span>
      <span class="badge">оператор: ${esc(lp(s.language_profile.operator))}</span>
      ${s.first_operator_reply ? `<button class="chip" data-mid="${esc(s.first_operator_reply.mid)}">первый ответ: ${esc(s.first_operator_reply.mid)}</button>` : ""}
      ${s.operator_reply_gaps.length ? `<span class="badge warn">паузы: ${s.operator_reply_gaps.map((g) => `${esc(g.before)} ${g.seconds}с`).join(", ")}</span>` : ""}
      ${s.risk_hits.length ? `<span class="badge bad">риск: ${s.risk_hits.map((r) => esc(r.mid)).join(", ")}</span>` : ""}
    </div>`;
  }

  jump(mid) {
    this.querySelectorAll(".msg.hl").forEach((m) => m.classList.remove("hl"));
    const el = this.querySelector(`#msg-${CSS.escape(mid)}`);
    if (!el) return;
    el.classList.add("hl");
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    const audio = this.querySelector("audio");
    // ponytail: assumes the recording starts at the first transcript timestamp; add an offset if it does not.
    if (audio && el.dataset.t !== "") { audio.currentTime = Number(el.dataset.t); audio.play().catch(() => {}); }
  }

  evaluationHtml(ev) {
    const s = ev.result.score;
    const changed = ev.total_ai !== ev.total;
    const openAppeals = Object.fromEntries(ev.appeals.filter((a) => a.state === "submitted").map((a) => [a.criterion_id, a]));
    const role = getRole();
    return `
      <div class="panel">
        <div class="score-card">
          <div class="ring" style="--v:${ev.total ?? 0};--c:var(--${{ warn: "warn", bad: "bad" }[tone(ev.total)] ?? "lime"})">
            <div><span class="total">${fmt(ev.total)}</span><small>из 100</small></div></div>
          <div class="sections">
            <div class="row">${statusBadge(ev.status)}${s.gross_error ? '<span class="badge bad">грубая ошибка</span>' : ""}
              ${changed ? `<span class="layers muted">ИИ: ${fmt(ev.total_ai)} → итог: ${fmt(ev.total)}</span>` : ""}</div>
            ${s.sections.filter((x) => x.max_points).map((x) => `
              <div class="sec"><span>${esc(x.title)}<b>${fmt(x.points)} / ${fmt(x.max_points)}</b></span>
                <div class="bar"><i style="width:${(100 * x.points) / x.max_points}%"></i></div></div>`).join("")}
          </div>
        </div>
        <div class="small faint" style="margin-top:14px">чек-лист v${ev.checklist_version} · применимый вес ${s.applicable_weight}
          · ${esc(ev.models.provider)} · $${(ev.cost_usd ?? 0).toFixed(4)} · ${ev.tokens_in}/${ev.tokens_out} токенов
          ${s.total_note ? ` · <span class="err">итог не рассчитан: ${esc(s.total_note)}</span>` : ""}</div>
      </div>
      <div>${ev.result.criteria.map((c) => this.criterionHtml(ev, c, s.criteria[c.criterion_id], openAppeals[c.criterion_id], role)).join("")}</div>`;
  }

  criterionHtml(ev, c, pts, appeal, role) {
    const pointsText = c.type === "flag" ? (c.final?.verdict === "yes" ? "нарушение" : "—")
      : pts.points == null ? "не учитывается" : `${fmt(pts.points)} / ${fmt(pts.max_points)}`;
    const conf = c.confidence ?? 0;
    const canDecide = role === "reviewer" && (appeal || (["needs_review", "failed"].includes(c.status) && !c.decision));
    return `
      <div class="crit" data-crit="${esc(c.criterion_id)}">
        <h4><span><span class="cid">${esc(c.criterion_id)}</span>${esc(c.title)}</span><span class="pts">${pointsText}</span></h4>
        <div class="row small">
          <b class="verdict ${this.verdictClass(c)}">${verdictText(c.final)}</b>
          <span class="votes" title="Голоса: M1 инспектор, M2 адвокат, M3 судья">${Object.entries(c.votes || {}).map(([k, v]) =>
            `<span class="${String(v) === this.finalKey(c) ? "agree" : ""}">${roleShort[k] ?? esc(k)} ${esc(v)}</span>`).join("")}</span>
          <span class="badge ${conf >= 0.99 ? "ok" : conf >= 0.66 ? "" : "warn"}" title="Согласие инспектора, адвоката и судьи">согласие ${Math.round(conf * 100)}%</span>
          ${c.status === "needs_review" ? '<span class="badge warn dot">на проверку</span>' : ""}
          ${c.cached ? '<span class="faint">кэш</span>' : ""}
          ${appeal ? '<span class="badge warn">апелляция</span>' : ""}
        </div>
        ${c.decision ? `<div class="small muted" style="margin-top:6px">ИИ: ${verdictText(c.ai)} → решение ${esc(c.decision.by)}: «${esc(c.decision.reason)}»</div>` : ""}
        ${c.rationale ? `<div class="rationale">${esc(c.rationale)}</div>` : ""}
        ${c.na_reason ? `<div class="small muted">N/A: ${esc(c.na_reason)}</div>` : ""}
        <div>${(c.evidence || []).map((e) => `<button class="chip" data-mid="${esc(e.msg)}" title="${esc(e.quote)}">${esc(e.msg)}: «${esc(String(e.quote).slice(0, 50))}»</button>`).join("")}
          ${c.checked_range ? `<span class="small muted">проверено: <button class="chip" data-mid="${esc(c.checked_range[0])}">${esc(c.checked_range[0])}</button>–<button class="chip" data-mid="${esc(c.checked_range[1])}">${esc(c.checked_range[1])}</button></span>` : ""}</div>
        ${Object.keys(c.errors || {}).length ? `<div class="small err">валидатор: ${esc(JSON.stringify(c.errors))}</div>` : ""}
        ${appeal ? `<div class="small">Комментарий оператора: «${esc(appeal.comment)}»</div>` : ""}
        <div class="row small actions">
          ${role === "operator" && !appeal ? `<button data-appeal="${esc(c.criterion_id)}">Оспорить</button>` : ""}
          ${canDecide ? `<button data-decide="${esc(c.criterion_id)}" class="primary">Решение</button>` : ""}
          <button data-trace-toggle="${esc(c.criterion_id)}">${this.openTraces.has(c.criterion_id) ? "Скрыть трассу" : "Трасса"}</button>
          <span class="grow"></span>
          <label class="muted">эталон <select data-gold="${esc(c.criterion_id)}">${this.goldOptions(c)}</select><span class="muted"></span></label>
        </div>
        ${this.openTraces.has(c.criterion_id) ? this.traceHtml(c) : ""}
        <div data-form></div>
      </div>`;
  }

  finalKey(c) {
    const f = c.final;
    return !f ? "" : f.verdict === "scored" ? String(f.step) : f.verdict;
  }

  verdictClass(c) {
    const v = c.final?.verdict;
    if (c.type === "flag") return v === "yes" ? "no" : v === "no" ? "yes" : "";
    if (v === "scored") return c.final.step > 0 ? "yes" : "no";
    return v === "yes" ? "yes" : v === "no" ? "no" : "";
  }

  formSlot(cid) { return this.querySelector(`[data-crit="${CSS.escape(cid)}"] [data-form]`); }

  appealForm(cid) {
    const slot = this.formSlot(cid);
    slot.innerHTML = `<div class="form"><textarea rows="2" placeholder="Почему вы не согласны?"></textarea>
      <div class="row"><button class="primary">Отправить апелляцию</button><span class="err"></span></div></div>`;
    slot.querySelector("button").onclick = async () => {
      try {
        await api.post("/api/appeals", { evaluation_id: this.data.latest.id, criterion_id: cid, comment: slot.querySelector("textarea").value });
        this.load();
      } catch (e) { slot.querySelector(".err").textContent = e.message; }
    };
  }

  decisionForm(cid) {
    const ev = this.data.latest;
    const c = ev.result.criteria.find((x) => x.criterion_id === cid);
    const appeal = ev.appeals.find((a) => a.criterion_id === cid && a.state === "submitted");
    const options = c.type === "scale" ? c.steps.map((s) => [`scored:${s}`, `ступень ${s}`])
      : c.type === "flag" ? [["yes", "Да (нарушение)"], ["no", "Нет"]] : [["yes", "Да"], ["no", "Нет"]];
    if (c.na_allowed) options.push(["na", "N/A"]);
    const slot = this.formSlot(cid);
    slot.innerHTML = `<div class="form">
      <div class="row"><select data-action><option value="uphold">Подтвердить оценку ИИ</option><option value="change">Изменить</option></select>
        <select data-verdict>${options.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select></div>
      <textarea rows="2" placeholder="Причина (обязательно)"></textarea>
      <div class="row"><button class="primary">Сохранить решение</button><span class="err"></span></div></div>`;
    slot.querySelector("button").onclick = async () => {
      const [verdict, step] = slot.querySelector("[data-verdict]").value.split(":");
      const body = { action: slot.querySelector("[data-action]").value, verdict, step: step ? Number(step) : null,
        reason: slot.querySelector("textarea").value };
      try {
        if (appeal) await api.post(`/api/appeals/${appeal.id}/decision`, body);
        else await api.post(`/api/evaluations/${ev.id}/review`, { ...body, criterion_id: cid });
        this.load();
      } catch (e) { slot.querySelector(".err").textContent = e.message; }
    };
  }
}
customElements.define("cqa-dialog-view", CqaDialogView);
