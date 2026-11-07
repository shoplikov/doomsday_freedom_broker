import { api, channelText, errorBox, esc, fmt, getRole, icon, loading, plural, statusBadge, tone, verdictText } from "../api.js";

const speakerText = { operator: "Оператор", client: "Клиент", system: "Система", bot: "Бот", unknown: "?" };
const roleShort = { inspector: "M1", advocate: "M2", judge: "M3" };
const roleName = { inspector: "M1 Инспектор (строгий)", advocate: "M2 Адвокат (за оператора)", judge: "M3 Судья (решение)" };
const voteText = (v) => ({ yes: "да", no: "нет", na: "н/п" }[v] ?? v);
const pretty = (text) => { try { return JSON.stringify(JSON.parse(text), null, 2); } catch { return text ?? ""; } };
const MIN_REASON = 10;

// Dialog card: transcript (+ audio) on the left, evaluation with evidence on the right.
// Operator can appeal a criterion; Reviewer decides appeals and low-confidence items.
class CqaDialogView extends HTMLElement {
  connectedCallback() {
    this.id_ = this.getAttribute("conversation-id");
    this.showRaw = false;
    this.openTraces = new Set();
    this.innerHTML = loading();
    this.load();
  }

  onRoleChange() { if (this.data) this.render(); }

  async load() {
    try {
      const id = encodeURIComponent(this.id_);
      [this.data, this.gold] = await Promise.all([
        api.get(`/api/conversations/${id}`),
        api.get(`/api/gold?conversation_id=${id}`).then((rows) => Object.fromEntries(rows.map((g) => [g.criterion_id, g.label]))),
      ]);
      this.render();
    } catch (e) {
      this.innerHTML = `<div class="crumbs"><a href="#/dialogs">Диалоги</a> /</div>${errorBox(e.message)}`;
    }
  }

  async evaluate() {
    const btn = this.querySelector('[data-act="evaluate"]');
    btn.disabled = true; btn.textContent = "Модели читают диалог…";
    try {
      await api.post("/api/evaluations", { conversation_id: this.id_ });
      await this.load();
      window.dispatchEvent(new Event("cqa:queue-changed"));
    } catch (e) { btn.disabled = false; btn.textContent = "Повторить"; this.flash(`Оценка не удалась: ${e.message}`); }
  }

  flash(msg) {
    const el = this.querySelector("[data-flash]");
    el.innerHTML = `<div class="alert bad" style="margin-bottom:16px">${icon("alert")}<div>${esc(msg)}</div></div>`;
  }

  render() {
    const d = this.data;
    const ev = d.latest;
    const counts = d.messages.reduce((m, x) => ((m[x.speaker] = (m[x.speaker] ?? 0) + 1), m), {});
    this.innerHTML = `<div class="page-enter">
      <header class="page-head">
        <div><div class="crumbs"><a href="#/dialogs">Диалоги</a><span>/</span></div>
          <h1 class="mono" style="font-size:26px;font-family:inherit">${esc(d.id)}</h1>
          <p class="lead">${esc(channelText[d.channel] ?? d.channel)} · ${esc(d.language)} · ${d.messages.length} ${plural(d.messages.length, "сообщение", "сообщения", "сообщений")}
            (оператор ${counts.operator ?? 0}, клиент ${counts.client ?? 0})</p></div>
        <div class="actions">
          <button data-act="evaluate" class="${ev ? "" : "primary"}">${icon("refresh")}${ev ? "Переоценить" : "Оценить диалог"}</button>
        </div>
      </header>
      <div data-flash></div>
      <div class="split">
        <section class="card sticky-col" aria-label="Переписка">
          <div class="transcript-head"><h2>Переписка</h2>
            <label class="switch small" title="Модели видят текст с замаскированными персональными данными">
              <input type="checkbox" data-raw ${this.showRaw ? "checked" : ""}><span class="track"></span><span>Исходный текст</span></label></div>
          ${d.audio_url ? `<audio controls preload="none" src="${esc(d.audio_url)}"></audio>` : ""}
          ${this.signalsHtml(d.signals)}
          <div data-transcript>${d.messages.map((m) => `
            <div class="msg ${esc(m.speaker)}" id="msg-${esc(m.mid)}" data-t="${m.t_sec ?? ""}">
              <div class="meta"><b>${esc(speakerText[m.speaker] ?? m.speaker)}</b><span class="mid">${esc(m.mid)}</span>${m.ts_raw ? `<span>${esc(m.ts_raw)}</span>` : ""}</div>
              <div class="bubble">${esc(this.showRaw ? m.text : m.text_masked)}</div>
            </div>`).join("")}</div>
        </section>
        <div>${ev ? this.evaluationHtml(ev) : `<section class="card empty"><b>Диалог ещё не оценён</b>
          <p>Нажмите «Оценить диалог»: три модели разберут его по чек-листу и приведут цитаты для каждого вывода. Это займёт до минуты.</p></section>`}</div>
      </div></div>`;
    this.bind();
  }

  bind() {
    this.querySelector('[data-act="evaluate"]').onclick = () => this.evaluate();
    this.querySelector("[data-raw]").onchange = (e) => { this.showRaw = e.target.checked; this.render(); };
    this.querySelectorAll("[data-mid]").forEach((b) => (b.onclick = () => this.jump(b.dataset.mid)));
    this.querySelectorAll("[data-goto]").forEach((b) => (b.onclick = () =>
      this.querySelector(`[data-crit="${CSS.escape(b.dataset.goto)}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" })));
    this.querySelectorAll("[data-appeal]").forEach((b) => (b.onclick = () => this.appealForm(b.dataset.appeal)));
    this.querySelectorAll("[data-decide]").forEach((b) => (b.onclick = () => this.decisionForm(b.dataset.decide)));
    this.querySelectorAll("[data-trace-toggle]").forEach((b) => (b.onclick = () => {
      const cid = b.dataset.traceToggle;
      this.openTraces.has(cid) ? this.openTraces.delete(cid) : this.openTraces.add(cid);
      this.render();
      this.querySelector(`[data-crit="${CSS.escape(cid)}"]`)?.scrollIntoView({ block: "nearest" });
    }));
    this.querySelectorAll("[data-trace-id]").forEach((b) => (b.onclick = () => this.showTrace(b)));
    this.querySelectorAll("[data-gold]").forEach((s) => (s.onchange = async () => {
      const note = s.nextElementSibling;
      try {
        await api.post("/api/gold", { conversation_id: this.id_, criterion_id: s.dataset.gold, label: s.value });
        this.gold[s.dataset.gold] = s.value || undefined;
        note.textContent = "сохранено";
        note.className = "good";
      } catch (e) { note.textContent = e.message; note.className = "err"; }
    }));
  }

  goldOptions(c) {
    const opts = c.type === "scale" ? c.steps.map((s) => [String(s), `${s} ${plural(s, "балл", "балла", "баллов")}`]) : [["yes", "Да"], ["no", "Нет"]];
    if (c.na_allowed) opts.push(["na", "Не применимо"]);
    const cur = this.gold[c.criterion_id] ?? "";
    return `<option value="">не размечено</option>` + opts.map(([v, l]) =>
      `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(l)}</option>`).join("");
  }

  // Full panel trace for one criterion: what M1 and M2 said independently, what M3 decided, and the raw calls.
  traceHtml(c) {
    const cell = (role) => {
      const r = c.roles?.[role];
      const errs = c.errors?.[role];
      const ids = c.trace_ids?.[role] ?? [];
      if (!r && !errs && !ids.length) return '<td class="faint">не вызывался: M1 и M2 согласны</td>';
      return `<td>
        ${r ? `<b>${esc(voteText(c.votes?.[role] ?? "—"))}</b>
          ${r.atoms ? `<div class="small">${Object.entries(r.atoms).map(([k, v]) => `${v ? "✓" : "✗"} ${esc(k)}`).join("<br>")}</div>` : ""}
          ${(r.evidence || []).map((e) => `<div class="small">${esc(e.msg)}: «${esc(e.quote)}»</div>`).join("")}
          ${r.checked_range ? `<div class="small faint">проверено ${esc(r.checked_range.join("–"))}</div>` : ""}
          ${r.na_reason ? `<div class="small faint">Не применимо: ${esc(r.na_reason)}</div>` : ""}
          <div class="small" style="margin-top:4px">${esc(r.rationale)}</div>` : '<b class="err">нет валидного ответа</b>'}
        ${errs ? `<div class="small err">${errs.map(esc).join("<br>")}</div>` : ""}
        <div class="row tight" style="margin-top:6px">${ids.map((id, i) => `<button class="chip" data-trace-id="${id}">вызов #${id}${i ? " (повтор)" : ""}</button>`).join("")}</div>
      </td>`;
    };
    const roles = ["inspector", "advocate", "judge"];
    return `<div class="trace">
      <div class="table-wrap"><table class="compact"><thead><tr>${roles.map((r) => `<th>${roleName[r]}</th>`).join("")}</tr></thead>
        <tbody><tr>${roles.map(cell).join("")}</tr></tbody></table></div>
      <p class="small faint">Источник итога: ${esc({ judge: "судья", agreement: "M1 и M2 согласны", inspector: "инспектор (судья не ответил)", advocate: "адвокат (судья не ответил)" }[c.source] ?? c.source ?? "—")}
        · согласие ${Math.round((c.confidence ?? 0) * 100)}%. Нажмите на вызов, чтобы увидеть запрос и ответ модели.</p>
      <div data-trace-body></div></div>`;
  }

  async showTrace(btn) {
    const box = btn.closest(".trace").querySelector("[data-trace-body]");
    box.innerHTML = loading();
    try {
      const t = await api.get(`/api/traces/${btn.dataset.traceId}`);
      box.innerHTML = `<div class="form small">
        <b>#${t.id} · ${esc(t.role)} · попытка ${t.attempt} · ${esc(t.model)} · ${t.latency_ms} мс · токены ${t.tokens_in}/${t.tokens_out}</b>
        ${t.error ? `<div class="err">${esc(t.error)}</div>` : ""}
        ${t.validation && Object.keys(t.validation).length ? `<div class="err">Валидатор: ${esc(JSON.stringify(t.validation))}</div>` : ""}
        <details class="disclosure"><summary>Запрос (${t.request.length} ${plural(t.request.length, "сообщение", "сообщения", "сообщений")})</summary>${t.request.map((m) =>
          `<div class="faint" style="margin-top:8px">${esc(m.role)}</div><pre>${esc(m.content)}</pre>`).join("")}</details>
        <details class="disclosure" open><summary>Ответ модели</summary><pre>${esc(pretty(t.response))}</pre></details></div>`;
    } catch (e) { box.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  }

  signalsHtml(s) {
    const lp = (p) => Object.entries(p || {}).map(([k, v]) => `${k} ${v}%`).join(", ") || "—";
    return `<div class="signals small">
      <span class="badge" title="Языки клиента">Клиент: ${esc(lp(s.language_profile.client))}</span>
      <span class="badge" title="Языки оператора">Оператор: ${esc(lp(s.language_profile.operator))}</span>
      ${s.first_operator_reply ? `<button class="chip" data-mid="${esc(s.first_operator_reply.mid)}">первый ответ ${esc(s.first_operator_reply.mid)}</button>` : ""}
      ${s.operator_reply_gaps.length ? `<span class="badge warn" title="Долгие паузы перед ответом оператора">Паузы: ${s.operator_reply_gaps.map((g) => `${esc(g.before)} ${g.seconds} с`).join(", ")}</span>` : ""}
      ${s.risk_hits.length ? `<span class="badge bad" title="Сообщения с рискованными словами">Риск: ${s.risk_hits.map((r) => esc(r.mid)).join(", ")}</span>` : ""}
    </div>`;
  }

  jump(mid) {
    this.querySelectorAll(".msg.hl").forEach((m) => m.classList.remove("hl"));
    const el = this.querySelector(`#msg-${CSS.escape(mid)}`);
    if (!el) return;
    el.classList.add("hl");
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
    const audio = this.querySelector("audio");
    // ponytail: assumes the recording starts at the first transcript timestamp; add an offset if it does not.
    if (audio && el.dataset.t !== "") { audio.currentTime = Number(el.dataset.t); audio.play().catch(() => {}); }
  }

  evaluationHtml(ev) {
    const s = ev.result.score;
    const changed = ev.total_ai !== ev.total;
    const openAppeals = Object.fromEntries(ev.appeals.filter((a) => a.state === "submitted").map((a) => [a.criterion_id, a]));
    const role = getRole();
    const attention = ev.result.criteria.filter((c) => openAppeals[c.criterion_id] || (["needs_review", "failed"].includes(c.status) && !c.decision));
    return `
      <section class="card">
        <div class="scorecard">
          <div class="ring" style="--v:${ev.total ?? 0};--c:var(--${{ warn: "warn", bad: "bad" }[tone(ev.total)] ?? "primary"})" role="img" aria-label="Итог ${fmt(ev.total)} из 100">
            <div><span class="total">${fmt(ev.total)}</span><small>из 100</small></div></div>
          <div class="sections">
            <div class="row tight">${statusBadge(ev.status)}${s.gross_error ? '<span class="badge bad dot">Грубая ошибка</span>' : ""}
              ${changed ? `<span class="layers">ИИ: ${fmt(ev.total_ai)} → итог: ${fmt(ev.total)}</span>` : ""}</div>
            ${s.sections.filter((x) => x.max_points).map((x) => `
              <div class="sec"><span>${esc(x.title)}<b>${fmt(x.points)} / ${fmt(x.max_points)}</b></span>
                <span class="meter ${tone((100 * x.points) / x.max_points)}"><i style="width:${(100 * x.points) / x.max_points}%"></i></span></div>`).join("")}
          </div>
        </div>
        ${attention.length ? `<div class="alert warn" style="margin-top:16px">${icon("alert")}<div><b>Нужен человек:</b>
          ${attention.map((c) => `<button class="link" data-goto="${esc(c.criterion_id)}">${esc(c.title)}</button>`).join(", ")}.
          ${role === "reviewer" ? "Откройте критерий и нажмите «Решить»." : "Решение принимает проверяющий."}</div></div>` : ""}
        <div class="meta-line"><span>Чек-лист v${ev.checklist_version}</span><span>учтённый вес ${s.applicable_weight}</span>
          <span>${esc(ev.models.inspector ?? ev.models.provider)}${ev.models.judge && ev.models.judge !== ev.models.inspector ? ` / ${esc(ev.models.judge)}` : ""}</span>
          <span>$${(ev.cost_usd ?? 0).toFixed(4)}</span><span>${ev.tokens_in}/${ev.tokens_out} токенов</span>
          ${s.total_note ? `<span class="err">итог не рассчитан: ${esc(s.total_note)}</span>` : ""}</div>
      </section>
      ${ev.result.criteria.map((c) => this.criterionHtml(c, s.criteria[c.criterion_id], openAppeals[c.criterion_id], role)).join("")}`;
  }

  criterionHtml(c, pts, appeal, role) {
    const pointsText = c.type === "flag" ? (c.final?.verdict === "yes" ? "нарушение" : "")
      : pts.points == null ? "не учитывается" : `${fmt(pts.points)} / ${fmt(pts.max_points)}`;
    const conf = c.confidence ?? 0;
    const pending = ["needs_review", "failed"].includes(c.status) && !c.decision;
    const canDecide = role === "reviewer" && (appeal || pending);
    const finalKey = this.finalKey(c);
    return `
      <article class="crit ${appeal || pending ? "attn" : ""}" data-crit="${esc(c.criterion_id)}" id="crit-${esc(c.criterion_id)}">
        <div class="crit-head">
          <b class="verdict ${this.verdictClass(c)}">${c.type === "flag" && c.final?.verdict === "no" ? "Нет нарушений" : verdictText(c.final)}</b>
          <h3><span class="cid">${esc(c.criterion_id)}</span>${esc(c.title)}</h3>
          <span class="pts">${pointsText}</span>
        </div>
        ${c.rationale ? `<p class="rationale">${esc(c.rationale)}</p>` : ""}
        ${c.na_reason ? `<p class="na-note">Не применимо: ${esc(c.na_reason)}</p>` : ""}
        ${(c.evidence || []).length ? `<div class="evidence">${c.evidence.map((e) =>
          `<button class="quote chip-like" data-mid="${esc(e.msg)}" title="Показать в переписке"><span class="mid">${esc(e.msg)}</span><span>«${esc(e.quote)}»</span></button>`).join("")}</div>` : ""}
        ${c.checked_range ? `<div class="checked" title="Где модель искала ожидаемое действие">Проверены сообщения <button class="chip" data-mid="${esc(c.checked_range[0])}">${esc(c.checked_range[0])}</button>–<button class="chip" data-mid="${esc(c.checked_range[1])}">${esc(c.checked_range[1])}</button></div>` : ""}
        ${c.decision ? `<div class="decision-note">ИИ: ${esc(verdictText(c.ai))} → решение ${esc(c.decision.by)}: «${esc(c.decision.reason)}»</div>` : ""}
        ${appeal ? `<div class="appeal-note"><b>Апелляция оператора:</b> «${esc(appeal.comment)}»</div>` : ""}
        ${Object.keys(c.errors || {}).length ? `<p class="small err" style="margin-top:8px">Валидатор: ${esc(JSON.stringify(c.errors))}</p>` : ""}
        <div class="crit-foot">
          <span class="votes" title="Голоса: M1 инспектор, M2 адвокат, M3 судья">${Object.entries(c.votes || {}).map(([k, v]) =>
            `<span class="${String(v) === finalKey ? "agree" : ""}">${roleShort[k] ?? esc(k)} ${esc(voteText(v))}</span>`).join("")}</span>
          <span class="badge ${conf >= 0.99 ? "ok" : conf >= 0.66 ? "" : "warn"}" title="Насколько модели согласны с итогом">согласие ${Math.round(conf * 100)}%</span>
          ${pending ? '<span class="badge warn dot">на проверку</span>' : ""}
          ${appeal ? '<span class="badge warn">апелляция</span>' : ""}
          <span class="grow"></span>
          ${role === "operator" && !appeal && !c.decision ? `<button class="sm" data-appeal="${esc(c.criterion_id)}">Оспорить</button>` : ""}
          ${canDecide ? `<button class="sm primary" data-decide="${esc(c.criterion_id)}">Решить</button>` : ""}
          <button class="sm ghost" data-trace-toggle="${esc(c.criterion_id)}" aria-expanded="${this.openTraces.has(c.criterion_id)}">${this.openTraces.has(c.criterion_id) ? "Скрыть трассу" : "Трасса"}</button>
          <label class="gold" title="Правильный ответ по мнению эксперта. Нужен, чтобы мерить точность моделей в «Аналитике».">Эталон
            <select data-gold="${esc(c.criterion_id)}">${this.goldOptions(c)}</select><span></span></label>
        </div>
        ${this.openTraces.has(c.criterion_id) ? this.traceHtml(c) : ""}
        <div data-form></div>
      </article>`;
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

  // Reason field with a live counter and a clear rule; returns the trimmed text or null (and shows why).
  reasonField(ph, hint) {
    return `<div class="field" data-reason><label>Комментарий</label>
      <textarea rows="3" placeholder="${esc(ph)}" maxlength="1000"></textarea>
      <div class="field-foot"><div data-err></div><span class="counter"></span></div><p class="hint">${hint}</p></div>`;
  }

  bindReason(slot) {
    const ta = slot.querySelector("[data-reason] textarea");
    const counter = slot.querySelector("[data-reason] .counter");
    const err = slot.querySelector("[data-reason] [data-err]");
    ta.oninput = () => {
      const n = ta.value.trim().length;
      counter.textContent = n < MIN_REASON ? `ещё ${MIN_REASON - n} ${plural(MIN_REASON - n, "символ", "символа", "символов")}` : "";
      if (n >= MIN_REASON) { err.innerHTML = ""; ta.closest(".field").classList.remove("invalid"); }
    };
    ta.oninput();
    ta.focus();
    return () => {
      const v = ta.value.trim();
      if (v.length >= MIN_REASON) return v;
      err.innerHTML = `<div class="field-error">Поясните подробнее: хотя бы ${MIN_REASON} символов.</div>`;
      ta.closest(".field").classList.add("invalid");
      ta.focus();
      return null;
    };
  }

  async submit(slot, btn, fn) {
    const err = slot.querySelector("[data-submit-err]");
    btn.disabled = true;
    err.textContent = "";
    try { await fn(); await this.load(); window.dispatchEvent(new Event("cqa:queue-changed")); }
    catch (e) { err.textContent = e.message; btn.disabled = false; }
  }

  appealForm(cid) {
    const slot = this.formSlot(cid);
    slot.innerHTML = `<div class="form">
      <b>Оспорить оценку</b>
      ${this.reasonField("Почему вы не согласны с оценкой?", "Сошлитесь на сообщение, например «в m5 я ответил на вопрос», так проверяющему проще.")}
      <div class="end"><span class="err small" data-submit-err></span><button class="ghost" data-cancel>Отмена</button><button class="primary" data-send>Отправить апелляцию</button></div></div>`;
    const reason = this.bindReason(slot);
    slot.querySelector("[data-cancel]").onclick = () => (slot.innerHTML = "");
    slot.querySelector("[data-send]").onclick = (e) => {
      const text = reason();
      if (text) this.submit(slot, e.currentTarget, () => api.post("/api/appeals", { evaluation_id: this.data.latest.id, criterion_id: cid, comment: text }));
    };
  }

  decisionForm(cid) {
    const ev = this.data.latest;
    const c = ev.result.criteria.find((x) => x.criterion_id === cid);
    const appeal = ev.appeals.find((a) => a.criterion_id === cid && a.state === "submitted");
    const options = c.type === "scale" ? c.steps.map((s) => [`scored:${s}`, `${s} ${plural(s, "балл", "балла", "баллов")}`])
      : c.type === "flag" ? [["yes", "Нарушение есть"], ["no", "Нарушения нет"]] : [["yes", "Да"], ["no", "Нет"]];
    if (c.na_allowed) options.push(["na", "Не применимо"]);
    const current = this.finalKey(c);
    const slot = this.formSlot(cid);
    slot.innerHTML = `<div class="form">
      <b>${appeal ? "Решение по апелляции" : "Проверка вердикта"}</b>
      <div class="choices" role="radiogroup" aria-label="Решение">
        <label class="choice"><input type="radio" name="act-${esc(cid)}" value="uphold" data-action checked><b>Оставить</b><span>Итог ИИ верный: ${esc(verdictText(c.final))}</span></label>
        <label class="choice"><input type="radio" name="act-${esc(cid)}" value="change" data-action><b>Изменить</b><span>Выбрать правильный ответ</span></label>
      </div>
      <div class="field" data-verdict-field hidden><span class="label">Правильный ответ</span>
        <div class="seg" role="radiogroup">${options.map(([v, l]) => {
          const key = v.startsWith("scored:") ? v.slice(7) : v;
          return `<label><input type="radio" name="v-${esc(cid)}" value="${esc(v)}" data-verdict ${key === current ? "disabled" : ""}>${esc(l)}${key === current ? " (сейчас)" : ""}</label>`;
        }).join("")}</div><div data-verdict-err></div></div>
      ${this.reasonField("Почему такое решение?", "Причина попадёт в журнал и будет видна оператору.")}
      <div class="end"><span class="err small" data-submit-err></span><button class="ghost" data-cancel>Отмена</button><button class="primary" data-send>Сохранить решение</button></div></div>`;
    const reason = this.bindReason(slot);
    const vf = slot.querySelector("[data-verdict-field]");
    slot.querySelectorAll("[data-action]").forEach((r) => (r.onchange = () => { vf.hidden = r.value !== "change" || !r.checked; }));
    slot.querySelector("[data-cancel]").onclick = () => (slot.innerHTML = "");
    slot.querySelector("[data-send]").onclick = (e) => {
      const action = slot.querySelector("[data-action]:checked").value;
      const picked = slot.querySelector("[data-verdict]:checked");
      const vErr = slot.querySelector("[data-verdict-err]");
      vErr.innerHTML = "";
      if (action === "change" && !picked) { vErr.innerHTML = '<div class="field-error">Выберите правильный ответ.</div>'; return; }
      const text = reason();
      if (!text) return;
      const [verdict, step] = (picked?.value ?? c.final?.verdict ?? "").split(":");
      const body = { action, verdict: action === "change" ? verdict : null, step: step ? Number(step) : null, reason: text };
      this.submit(slot, e.currentTarget, () => appeal
        ? api.post(`/api/appeals/${appeal.id}/decision`, body)
        : api.post(`/api/evaluations/${ev.id}/review`, { ...body, criterion_id: cid }));
    };
  }
}
customElements.define("cqa-dialog-view", CqaDialogView);
