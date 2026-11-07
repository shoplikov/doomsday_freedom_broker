import { api, errorBox, esc, fmt, icon, loading, pageHead, plural } from "../api.js";

// Checklist studio. The checklist is data: edit criteria here, publish a new version, re-evaluate.
// Left: criteria list with a live weight total. Right: one criterion, in plain-language sections.
// Validation runs as you type (shown once a field is touched, or for everything on «Опубликовать»);
// the server lint runs again on publish. Only changed criteria go back to the models.

const VAGUE = ["хорошо", "нормально", "правильно", "адекватно", "properly", "appropriate", "good", "adequate", "жақсы"];
const CODE_RE = /^[a-z0-9_]+$/;
const TYPES = {
  binary: { name: "Да / Нет", hint: "Оператор сделал или не сделал. Полный вес или ноль.", def: "Что считается «Да»" },
  scale: { name: "Шкала", hint: "Несколько ступеней качества, например 0, 5, 10, 15, 20.", def: "Что оценивается" },
  flag: { name: "Нарушение", hint: "Отмечается отдельно и баллы не меняет.", def: "Что считается нарушением" },
};
let uid = 0;
const strip = ({ _k, ...c }) => c;
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const clean = (c) => {
  // What the server receives: only the fields of the chosen type.
  const out = { id: c.id, section: c.section, title: c.title.trim(), type: c.type, weight: c.type === "flag" ? 0 : c.weight,
    na_allowed: c.type === "flag" ? false : !!c.na_allowed, na_condition: c.na_allowed && c.type !== "flag" ? c.na_condition.trim() : "",
    definition: c.definition.trim() };
  if (c.type === "scale") out.scale = c.scale.map((s) => ({ step: Number(s.step), label: s.label.trim() })).sort((a, b) => b.step - a.step);
  else { out.rule = c.rule || "all"; out.atoms = c.atoms.map((a) => ({ id: a.id.trim(), q: a.q.trim() })); }
  return out;
};
const comparable = (c) => JSON.stringify(clean({ atoms: [], scale: [], rule: "all", na_condition: "", ...c }));

class CqaChecklistEditor extends HTMLElement {
  async connectedCallback() {
    this.innerHTML = loading();
    try {
      [this.active, this.versions] = await Promise.all([api.get("/api/checklists/active"), api.get("/api/checklists")]);
      this.startFrom(this.active);
    } catch (e) { this.innerHTML = errorBox(e.message); }
  }

  // ---------- state ----------
  startFrom(cl) {
    this.source = cl.version;
    this.base = Object.fromEntries(this.active.criteria.map((c) => [c.id, comparable(c)]));
    this.draft = structuredClone(cl);
    this.draft.criteria = this.draft.criteria.map((c) => ({ atoms: [], scale: [], rule: "all", na_condition: "", ...c, _k: ++uid, _orig: c.id }));
    this.sel = 0;
    this.touched = new Set();
    this.showAll = false;
    this.result = null;
    this.render();
  }

  isDirty() { return !!this.draft && !this.result && this.changes().total > 0; }

  get cur() { return this.draft.criteria[this.sel]; }

  changes() {
    const ids = new Set(this.draft.criteria.map((c) => c._orig).filter(Boolean));
    const added = this.draft.criteria.filter((c) => !c._orig || !(c._orig in this.base)).length;
    const changed = this.draft.criteria.filter((c) => c._orig in this.base && comparable(c) !== this.base[c._orig]).length;
    const removed = Object.keys(this.base).filter((id) => !ids.has(id)).length;
    const order = !same(this.draft.criteria.map((c) => c._orig), this.active.criteria.map((c) => c.id));
    return { added, changed, removed, total: added + changed + removed + (order && !added && !removed ? 1 : 0) };
  }

  state(c) { return !c._orig || !(c._orig in this.base) ? "new" : comparable(c) !== this.base[c._orig] ? "changed" : ""; }

  // Errors block publishing; warnings only advise. Keys are field paths used by data-path / data-err.
  validate(c) {
    const e = {}, w = {};
    const all = this.draft.criteria;
    if (!c.title.trim()) e.title = "Введите название: так критерий увидят в карточке диалога.";
    else if (all.some((x) => x !== c && x.title.trim().toLowerCase() === c.title.trim().toLowerCase())) w.title = "Критерий с таким названием уже есть.";
    if (!c.definition.trim()) e.definition = c.type === "flag" ? "Опишите, что считается нарушением." : "Опишите, что оценивается. Модели опираются на этот текст.";
    if (c.type !== "flag" && !(Number.isInteger(c.weight) && c.weight >= 0 && c.weight <= 100)) e.weight = "Целое число от 0 до 100.";
    else if (c.type !== "flag" && c.weight === 0) w.weight = "С весом 0 критерий не влияет на итог.";
    if (!CODE_RE.test(c.id)) e.id = "Только латиница в нижнем регистре, цифры и «_», например c09.";
    else if (all.some((x) => x !== c && x.id === c.id)) e.id = `Код «${c.id}» уже занят другим критерием.`;
    else if (c._orig && c._orig in this.base && c.id !== c._orig) w.id = `Эталонная разметка и results.json привязаны к коду «${c._orig}». После смены они перестанут совпадать.`;
    if (c.type !== "flag" && c.na_allowed && !c.na_condition.trim()) e.na_condition = "Опишите, когда критерий неприменим, иначе модели будут ставить N/A наугад.";
    if (c.type === "scale") {
      if (!c.scale.length) e.scale = "Добавьте хотя бы две ступени.";
      const steps = c.scale.map((s) => Number(s.step));
      c.scale.forEach((s, i) => {
        if (s.step === "" || !Number.isFinite(Number(s.step)) || Number(s.step) < 0) e[`scale.${i}.step`] = "Балл — число от 0.";
        else if (steps.indexOf(Number(s.step)) !== i) e[`scale.${i}.step`] = "Такой балл уже есть.";
        if (!s.label.trim()) e[`scale.${i}.label`] = "Опишите, когда ставится эта ступень.";
      });
      if (c.scale.length && Math.max(...steps) <= 0) e.scale = "Нужна ступень с баллом больше 0: она даёт полный вес.";
      else if (c.scale.length && !steps.includes(0)) w.scale = "Нет ступени 0: модели не смогут отметить, что ничего не сделано.";
    } else {
      if (!c.atoms.length) e.atoms = "Добавьте хотя бы один вопрос.";
      const codes = c.atoms.map((a) => a.id.trim());
      c.atoms.forEach((a, i) => {
        if (!a.q.trim()) e[`atoms.${i}.q`] = "Сформулируйте вопрос, на который можно ответить «да» или «нет».";
        if (!CODE_RE.test(codes[i])) e[`atoms.${i}.id`] = "a–z, 0–9 и «_»";
        else if (codes.indexOf(codes[i]) !== i) e[`atoms.${i}.id`] = "Код повторяется.";
      });
    }
    const vague = (t) => VAGUE.find((v) => new RegExp(`(^|[^\\p{L}])${v}($|[^\\p{L}])`, "iu").test(t));
    const v1 = vague(c.definition);
    if (v1 && !e.definition) w.definition = `«${v1}» — расплывчато. Напишите, что именно должен сделать оператор.`;
    c.atoms.forEach((a, i) => { const v = c.type !== "scale" && vague(a.q); if (v && !e[`atoms.${i}.q`]) w[`atoms.${i}.q`] = `«${v}» — расплывчато: что именно считается?`; });
    return { e, w };
  }

  problems() {
    return this.draft.criteria.map((c, i) => ({ i, n: Object.keys(this.validate(c).e).length })).filter((x) => x.n);
  }

  weightSum() { return this.draft.criteria.filter((c) => c.type !== "flag").reduce((s, c) => s + (Number.isFinite(c.weight) ? c.weight : 0), 0); }

  // ---------- render ----------
  render() {
    const latest = Math.max(...this.versions.map((v) => v.version));
    const viewingOld = this.source !== this.active.version;
    this.innerHTML = `<div class="page-enter">
      ${pageHead("Чек-лист",
        "Критерии, по которым модели оценивают диалоги. Правки публикуются новой версией без деплоя, а при переоценке модели заново смотрят только изменённые критерии.",
        `<div class="field" style="width:340px"><label for="ver">Версия</label><select id="ver" data-version>${[...this.versions].reverse().map((v) =>
          `<option value="${v.version}" ${v.version === this.source ? "selected" : ""}>v${v.version}${v.version === this.active.version ? " · активная" : ""}${v.note ? ` · ${esc(v.note)}` : ""}</option>`).join("")}</select></div>`)}
      ${viewingOld ? `<div class="alert info" style="margin-bottom:16px">${icon("undo")}<div>Открыта <b>v${this.source}</b> как основа. Активна v${this.active.version}.
        Опубликуйте, чтобы вернуть эти критерии новой версией v${latest + 1}.</div></div>` : ""}
      <div class="studio">
        <aside class="studio-side" data-side></aside>
        <section class="card editor" data-editor></section>
      </div>
      <div class="publish-bar" data-bar>
        <div class="state" data-state></div>
        <div class="note"><input data-note placeholder="Что изменили и зачем (попадёт в журнал)" maxlength="200" aria-label="Описание изменений"></div>
        <button class="ghost" data-act="reset" title="Вернуть все критерии к активной версии">${icon("undo")}Сбросить</button>
        <button class="primary" data-act="publish">Опубликовать v${latest + 1}</button>
      </div>
      <div data-result></div></div>`;
    this.querySelector("[data-version]").onchange = (e) => this.openVersion(Number(e.target.value));
    this.querySelector('[data-act="publish"]').onclick = () => this.publish();
    this.querySelector('[data-act="reset"]').onclick = () => {
      if (confirm("Сбросить все несохранённые правки?")) this.startFrom(this.active);
    };
    this.querySelector("[data-note]").oninput = (e) => e.target.classList.remove("invalid");
    this.renderSide();
    this.renderEditor();
    this.renderBar();
    if (this.result) this.renderResult();
  }

  renderSide() {
    const sum = this.weightSum();
    const sections = this.draft.sections || [];
    const groups = sections.map((s) => [s, this.draft.criteria.map((c, i) => [c, i]).filter(([c]) => c.section === s.id)]);
    const orphans = this.draft.criteria.map((c, i) => [c, i]).filter(([c]) => !sections.some((s) => s.id === c.section));
    if (orphans.length) groups.push([{ title: "Без раздела" }, orphans]);
    const item = ([c, i]) => {
      const bad = Object.keys(this.validate(c).e).length && (this.showAll || [...this.touched].some((t) => t.startsWith(`${c._k}:`)));
      const st = bad ? "err" : this.state(c) ? "changed" : "";
      const meta = c.type === "flag" ? "нарушение" : `${TYPES[c.type].name.toLowerCase()} · вес ${Number.isFinite(c.weight) ? c.weight : "?"}`;
      return `<button type="button" class="cl-item ${i === this.sel ? "active" : ""}" data-pick="${i}" data-id="${esc(c.id)}" aria-current="${i === this.sel}">
        <span class="n">${i + 1}</span><span class="t"><b>${esc(c.title) || '<span class="faint">Без названия</span>'}</b><span>${meta}</span></span>
        <span class="st ${st}" title="${st === "err" ? "Есть ошибки" : st === "changed" ? (this.state(c) === "new" ? "Новый" : "Изменён") : ""}"></span></button>`;
    };
    this.querySelector("[data-side]").innerHTML = `
      <div class="card weights">
        <div class="row" style="justify-content:space-between"><span class="small muted">Сумма весов</span><span class="v ${sum === 100 ? "" : "poor"}">${sum} / 100</span></div>
        <span class="meter ${sum === 100 ? "" : sum > 100 ? "bad" : "warn"}"><i style="width:${Math.min(100, sum)}%"></i></span>
        <p class="hint">${sum === 100 ? "Ровно 100: веса читаются как баллы." : `Итог всё равно приводится к 100, но удобнее, когда сумма ровно 100 (${sum < 100 ? `не хватает ${100 - sum}` : `лишние ${sum - 100}`}).`}</p>
      </div>
      <nav class="card crit-list" aria-label="Критерии">
        ${groups.filter(([, items]) => items.length).map(([s, items]) => `<div class="group">${esc(s.title)}</div>${items.map(item).join("")}`).join("")}
        <button type="button" class="add-crit" data-act="add">${icon("plus")}Добавить критерий</button>
      </nav>`;
    this.querySelectorAll("[data-pick]").forEach((b) => (b.onclick = () => { this.sel = Number(b.dataset.pick); this.renderSide(); this.renderEditor(); }));
    this.querySelector('[data-act="add"]').onclick = () => this.addCriterion();
  }

  field(path, label, control, { hint = "", opt = false, counter = 0 } = {}) {
    return `<div class="field" data-field="${path}"><label for="f-${path}">${label}${opt ? ' <span class="opt">необязательно</span>' : ""}</label>
      ${control}${hint ? `<p class="hint">${hint}</p>` : ""}
      <div class="field-foot"><div data-err="${path}"></div>${counter ? `<span class="counter" data-counter="${path}" data-max="${counter}"></span>` : ""}</div></div>`;
  }

  renderEditor() {
    const c = this.cur;
    const box = this.querySelector("[data-editor]");
    if (!c) { box.innerHTML = `<div class="empty"><b>Критериев нет</b><p>Добавьте первый критерий слева.</p></div>`; return; }
    const sections = (this.draft.sections || []).map((s) => `<option value="${esc(s.id)}" ${s.id === c.section ? "selected" : ""}>${esc(s.title)}</option>`).join("");
    const sum = this.weightSum();
    const st = this.state(c);
    const ta = (path, val, ph, rows = 2) => `<textarea id="f-${path}" data-path="${path}" rows="${rows}" placeholder="${esc(ph)}">${esc(val)}</textarea>`;
    box.innerHTML = `
      <header class="editor-head">
        <div class="grow"><div class="eyebrow">Критерий ${this.sel + 1} из ${this.draft.criteria.length}</div>
          <h2 data-title>${esc(c.title) || "Новый критерий"}</h2></div>
        <div class="row tight">
          ${st ? `<span class="badge ${st === "new" ? "ok" : "info"}">${st === "new" ? "Новый" : "Изменён"}</span>` : ""}
          ${st === "changed" ? `<button class="ghost sm" data-act="revert">${icon("undo")}Вернуть как было</button>` : ""}
          <span data-del><button class="ghost sm danger" data-act="delete">${icon("trash")}Удалить</button></span>
        </div>
      </header>

      <div class="editor-section"><header><h3>Основное</h3><p>Как критерий называется, к какому разделу относится и сколько весит.</p></header>
        <div class="fields">
          ${this.field("title", "Название", `<input id="f-title" data-path="title" value="${esc(c.title)}" maxlength="80" placeholder="Например: Установление контакта">`, { counter: 80 })}
          <div class="field"><span class="label">Тип оценки</span>
            <div class="choices" role="radiogroup" aria-label="Тип оценки">${Object.entries(TYPES).map(([t, d]) => `
              <label class="choice"><input type="radio" name="type" value="${t}" ${c.type === t ? "checked" : ""} data-type><b>${d.name}</b><span>${d.hint}</span></label>`).join("")}</div></div>
          <div class="fields-2">
            ${this.field("section", "Раздел", `<select id="f-section" data-path="section">${sections}</select>`)}
            ${c.type === "flag"
              ? `<div class="field"><span class="label">Вес</span><p class="hint" style="margin-top:8px">Нарушение не даёт баллов: оно отмечается флагом рядом с итогом.</p></div>`
              : this.field("weight", "Вес", `<div class="with-suffix"><input id="f-weight" data-path="weight" type="number" inputmode="numeric" min="0" max="100" step="1" value="${Number.isFinite(c.weight) ? c.weight : ""}"><span>баллов</span></div>`,
                { hint: `<span data-share>${sum ? `≈ ${Math.round((100 * (c.weight || 0)) / sum)}% итога, если все критерии применимы` : ""}</span>` })}
          </div>
        </div></div>

      <div class="editor-section"><header><h3>Что оценивать</h3><p>Модели читают это определение перед каждым диалогом. Чем конкретнее, тем точнее и стабильнее оценка.</p></header>
        <div class="fields">
          ${this.field("definition", TYPES[c.type].def, ta("definition", c.definition,
            c.type === "flag" ? "Например: оскорбление клиента, сделка вне компании, раскрытие чужих данных." : "Например: Да — оператор поздоровался на языке клиента и назвал своё имя в первом ответе.", 3),
            { hint: "Пишите про действия оператора, которые видно в тексте. Избегайте слов вроде «хорошо» или «правильно»." })}
        </div></div>

      ${c.type === "scale" ? this.scaleHtml(c) : this.atomsHtml(c)}

      ${c.type === "flag" ? "" : `
      <div class="editor-section"><header><h3>Неприменимость</h3><p>Если критерий не к чему применить, его вес делится между остальными. Это не ноль и не ошибка.</p></header>
        <div class="fields">
          <label class="switch"><input type="checkbox" data-path="na_allowed" ${c.na_allowed ? "checked" : ""}><span class="track"></span>
            <span><span class="label">Можно ставить «Не применимо»</span><p class="hint">Например, идентификация не нужна, если клиент спросил про общие тарифы.</p></span></label>
          ${c.na_allowed ? this.field("na_condition", "Когда критерий неприменим", ta("na_condition", c.na_condition, "Например: запрос не требовал раскрытия данных клиента или защищённых действий.")) : ""}
        </div></div>`}

      <div class="editor-section"><header><h3>Тест-драйв</h3><p>Прогоните критерий в этой редакции на нескольких диалогах до публикации. Сначала берутся диалоги, где модели по нему спорили.</p></header>
        <div class="fields"><div class="row"><button type="button" class="sm" data-act="test-drive" ${this.driving ? "disabled" : ""}>${icon("flask")}${this.driving ? "Модели читают диалоги…" : "Проверить на 5 диалогах"}</button>
          <span class="small faint">около минуты, ничего не сохраняется</span></div>
          <div data-drive>${this.driveHtml(c)}</div></div></div>

      <div class="editor-section"><header><h3>Код критерия</h3><p>Технический идентификатор. По нему связаны эталонная разметка и файл results.json.</p></header>
        <div class="fields">
          ${this.field("id", "Код", `<input id="f-id" data-path="id" value="${esc(c.id)}" class="mono" style="max-width:220px" spellcheck="false" autocomplete="off">`,
            { hint: st === "new" ? "Латиница в нижнем регистре, цифры и «_»." : "У существующих критериев код лучше не менять." })}
        </div></div>`;
    this.bindEditor();
    this.refresh();
  }

  driveHtml(c) {
    const r = this.drives?.[c._k];
    if (!r) return "";
    if (r.error) return `<p class="err small">${esc(r.error)}</p>`;
    const lbl = (v) => v == null ? "—" : c.type === "flag" ? ({ yes: "Нарушение", no: "Нет нарушений" }[v] ?? v)
      : ({ yes: "Да", no: "Нет", na: "Не применимо" }[v] ?? `${v} ${plural(Number(v), "балл", "балла", "баллов")}`);
    const stale = r.fingerprint !== comparable(c);
    return `<div class="alert ${r.changed ? "warn" : "ok"}">${icon(r.changed ? "alert" : "check")}<div>
        <b>Изменится ${r.changed} ${plural(r.changed, "вердикт", "вердикта", "вердиктов")} из ${r.dialogs}.</b>
        Модели единогласны в ${r.unanimous} из ${r.dialogs}${r.unanimous < r.dialogs ? " — где спорят, формулировку стоит уточнить" : ""}.
        ${stale ? '<br><span class="faint">Критерий изменён после проверки — запустите заново.</span>' : ""}</div></div>
      ${(r.warnings || []).length ? `<p class="small warn-text">${r.warnings.map(esc).join("; ")}</p>` : ""}
      <div class="table-wrap"><table class="compact"><thead><tr><th>Диалог</th><th>Сейчас</th><th>С новой редакцией</th><th class="num">Согласие</th></tr></thead>
        <tbody>${r.rows.map((x) => x.error ? `<tr><td class="mono">${esc(x.conversation_id)}</td><td colspan="3" class="err">${esc(x.error)}</td></tr>` : `<tr>
          <td><a class="mono" href="#/dialog/${encodeURIComponent(x.conversation_id)}">${esc(x.conversation_id)}</a></td>
          <td>${esc(lbl(x.before))}</td><td><b>${esc(lbl(x.after))}</b>${x.before != null && x.before !== x.after ? ' <span class="badge warn">изменится</span>' : ""}</td>
          <td class="num">${Math.round((x.confidence ?? 0) * 100)}%</td></tr>
          <tr class="sub"><td colspan="4" class="small">${esc(x.rationale ?? "")}${x.na_reason ? ` <span class="faint">Не применимо: ${esc(x.na_reason)}</span>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
  }

  async testDrive() {
    const c = this.cur;
    const key = c._k;
    this.drives = this.drives ?? {};
    this.driving = true; this.renderEditor();
    try {
      const fp = comparable(c);
      this.drives[key] = { ...(await api.post("/api/checklists/test-drive", { criterion: clean(c) })), fingerprint: fp };
    } catch (e) { this.drives[key] = { error: e.message }; }
    this.driving = false;
    if (this.cur?._k === key) { this.renderEditor(); this.querySelector("[data-drive]")?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
  }

  atomsHtml(c) {
    const one = c.atoms.length <= 1;
    return `<div class="editor-section"><header><h3>Вопросы для моделей</h3>
        <p>Модели отвечают на каждый вопрос «да» или «нет» и приводят цитату из диалога. Ответ по критерию собирает код.</p></header>
      <div class="fields">
        <div class="rule-box"><span class="label">${c.type === "flag" ? "Нарушение есть, если" : "«Да» по критерию, если"}</span>
          <div class="seg" role="radiogroup" aria-label="Правило">
            <label><input type="radio" name="rule" value="all" ${c.rule !== "any" ? "checked" : ""} data-rule>на все вопросы «да»</label>
            <label><input type="radio" name="rule" value="any" ${c.rule === "any" ? "checked" : ""} data-rule>хотя бы на один «да»</label></div></div>
        <div class="items" data-items>
          <div class="item-head"><span>№</span><span>Вопрос</span><span>Код</span><span></span></div>
          ${c.atoms.map((a, i) => `<div class="item-row">
            <span class="n">${i + 1}</span>
            <div class="field" data-field="atoms.${i}.q"><textarea data-path="atoms.${i}.q" rows="1" aria-label="Вопрос ${i + 1}"
              placeholder="Например: Назвал ли оператор своё имя в первом ответе?">${esc(a.q)}</textarea><div data-err="atoms.${i}.q"></div></div>
            <div class="field code" data-field="atoms.${i}.id"><input data-path="atoms.${i}.id" value="${esc(a.id)}" aria-label="Код вопроса ${i + 1}" spellcheck="false" autocomplete="off"><div data-err="atoms.${i}.id"></div></div>
            <button type="button" class="ghost icon sm" data-rm-atom="${i}" ${one ? "disabled" : ""} aria-label="Удалить вопрос ${i + 1}" title="${one ? "Нужен хотя бы один вопрос" : "Удалить вопрос"}">${icon("x")}</button>
          </div>`).join("")}
          <div data-err="atoms"></div>
        </div>
        <div><button type="button" class="sm" data-act="add-atom">${icon("plus")}Добавить вопрос</button></div>
      </div></div>`;
  }

  scaleHtml(c) {
    return `<div class="editor-section"><header><h3>Ступени шкалы</h3>
        <p>Модель выбирает одну ступень. Высшая ступень даёт полный вес, остальные — долю: 15 из 20 = 75% веса.</p></header>
      <div class="fields"><div class="items">
        <div class="item-head scale-head"><span>Балл</span><span>Когда ставится</span><span></span></div>
        ${c.scale.map((s, i) => `<div class="item-row scale-row">
          <div class="field" data-field="scale.${i}.step"><input data-path="scale.${i}.step" type="number" min="0" step="1" inputmode="numeric" value="${esc(s.step)}" aria-label="Балл ступени ${i + 1}"><div data-err="scale.${i}.step"></div></div>
          <div class="field" data-field="scale.${i}.label"><textarea data-path="scale.${i}.label" rows="1" aria-label="Описание ступени ${i + 1}"
            placeholder="Например: основной вопрос решён, упущена второстепенная деталь">${esc(s.label)}</textarea><div data-err="scale.${i}.label"></div></div>
          <button type="button" class="ghost icon sm" data-rm-step="${i}" ${c.scale.length <= 2 ? "disabled" : ""} aria-label="Удалить ступень" title="${c.scale.length <= 2 ? "Нужно хотя бы две ступени" : "Удалить ступень"}">${icon("x")}</button>
        </div>`).join("")}
        <div data-err="scale"></div>
      </div>
      <div><button type="button" class="sm" data-act="add-step">${icon("plus")}Добавить ступень</button></div></div></div>`;
  }

  bindEditor() {
    const box = this.querySelector("[data-editor]");
    const c = this.cur;
    box.querySelectorAll("[data-path]").forEach((el) => {
      const path = el.dataset.path;
      const ev = el.type === "checkbox" || el.tagName === "SELECT" ? "change" : "input";
      el.addEventListener(ev, () => {
        this.set(path, el.type === "checkbox" ? el.checked : el.value);
        if (path === "na_allowed") { this.renderEditor(); if (el.checked) this.querySelector('[data-path="na_condition"]')?.focus(); return; }
        this.refresh();
      });
      el.addEventListener("blur", () => {
        this.touched.add(`${c._k}:${path}`);
        if (/^scale\.\d+\.step$/.test(path)) { this.sortSteps(); return; }
        this.refresh();
      });
    });
    box.querySelectorAll("[data-type]").forEach((r) => (r.onchange = () => this.setType(r.value)));
    box.querySelector('[data-act="test-drive"]')?.addEventListener("click", () => this.testDrive());
    box.querySelectorAll("[data-rule]").forEach((r) => (r.onchange = () => { c.rule = r.value; this.refresh(); }));
    box.querySelectorAll("[data-rm-atom]").forEach((b) => (b.onclick = () => { c.atoms.splice(Number(b.dataset.rmAtom), 1); this.renderEditor(); }));
    box.querySelectorAll("[data-rm-step]").forEach((b) => (b.onclick = () => { c.scale.splice(Number(b.dataset.rmStep), 1); this.renderEditor(); }));
    box.querySelector('[data-act="add-atom"]')?.addEventListener("click", () => {
      const used = new Set(c.atoms.map((a) => a.id));
      let n = c.atoms.length + 1;
      while (used.has(`q${n}`)) n++;
      c.atoms.push({ id: `q${n}`, q: "" });
      this.renderEditor();
      box.querySelector(`[data-path="atoms.${c.atoms.length - 1}.q"]`)?.focus();
    });
    box.querySelector('[data-act="add-step"]')?.addEventListener("click", () => {
      c.scale.push({ step: "", label: "" });
      this.renderEditor();
      box.querySelector(`[data-path="scale.${c.scale.length - 1}.step"]`)?.focus();
    });
    box.querySelector('[data-act="revert"]')?.addEventListener("click", () => {
      const orig = this.active.criteria.find((x) => x.id === c._orig);
      this.draft.criteria[this.sel] = { atoms: [], scale: [], rule: "all", na_condition: "", ...structuredClone(orig), _k: c._k, _orig: c._orig };
      this.renderSide(); this.renderEditor();
    });
    box.querySelector('[data-act="delete"]').onclick = () => {
      const slot = box.querySelector("[data-del]");
      slot.innerHTML = `<span class="danger-zone small"><span>Удалить «${esc(c.title || "без названия")}»?</span>
        <button class="sm danger" data-act="del-yes">Удалить</button><button class="sm ghost" data-act="del-no">Отмена</button></span>`;
      slot.querySelector('[data-act="del-no"]').onclick = () => this.renderEditor();
      slot.querySelector('[data-act="del-yes"]').onclick = () => {
        this.draft.criteria.splice(this.sel, 1);
        this.sel = Math.max(0, Math.min(this.sel, this.draft.criteria.length - 1));
        this.renderSide(); this.renderEditor();
      };
      slot.querySelector('[data-act="del-no"]').focus();
    };
  }

  set(path, value) {
    const c = this.cur;
    const parts = path.split(".");
    if (parts.length === 3) { c[parts[0]][Number(parts[1])][parts[2]] = value; return; }
    c[path] = path === "weight" ? (value === "" ? NaN : Number(value)) : value;
  }

  setType(t) {
    const c = this.cur;
    c.type = t;
    if (t === "scale" && c.scale.length < 2) c.scale = [{ step: 10, label: "" }, { step: 5, label: "" }, { step: 0, label: "" }];
    if (t !== "scale" && !c.atoms.length) c.atoms = [{ id: "q1", q: "" }];
    if (t === "flag") { c.rule = c.rule || "any"; c.na_allowed = false; }
    this.renderSide(); this.renderEditor();
  }

  sortSteps() {
    const c = this.cur;
    const before = c.scale.map((s) => s.step).join();
    c.scale.sort((a, b) => (b.step === "" ? -1 : a.step === "" ? 1 : Number(b.step) - Number(a.step)));
    if (c.scale.map((s) => s.step).join() !== before) this.renderEditor(); else this.refresh();
  }

  addCriterion() {
    const used = new Set(this.draft.criteria.map((c) => c.id));
    let n = this.draft.criteria.length + 1;
    while (used.has(`c${String(n).padStart(2, "0")}`)) n++;
    const section = this.cur?.section ?? this.draft.sections?.[0]?.id ?? "";
    // Insert after the last criterion of the same section, so the numbering in the list stays in order.
    const last = this.draft.criteria.map((c) => c.section).lastIndexOf(section);
    const at = last < 0 ? this.draft.criteria.length : last + 1;
    this.draft.criteria.splice(at, 0, { id: `c${String(n).padStart(2, "0")}`, section, title: "", type: "binary", weight: 10, na_allowed: false,
      na_condition: "", rule: "all", definition: "", atoms: [{ id: "q1", q: "" }], scale: [], _k: ++uid });
    this.sel = at;
    this.renderSide(); this.renderEditor();
    this.querySelector("#f-title")?.focus();
  }

  // Cheap update after typing: messages, counters, title, side list, bar. Inputs are not re-rendered (focus stays).
  refresh() {
    const c = this.cur;
    if (!c) return;
    const { e, w } = this.validate(c);
    this.querySelectorAll("[data-editor] [data-err]").forEach((slot) => {
      const p = slot.dataset.err;
      const show = this.showAll || this.touched.has(`${c._k}:${p}`) || (p === "atoms" || p === "scale");
      const msg = show && e[p] ? `<div class="field-error">${esc(e[p])}</div>` : w[p] ? `<div class="field-warn">${esc(w[p])}</div>` : "";
      slot.innerHTML = msg;
      slot.closest(".field")?.classList.toggle("invalid", !!(show && e[p]));
    });
    this.querySelectorAll("[data-counter]").forEach((el) => {
      const len = (c[el.dataset.counter] || "").length;
      el.textContent = len > el.dataset.max * 0.7 ? `${len} / ${el.dataset.max}` : "";
    });
    const t = this.querySelector("[data-title]");
    if (t) t.textContent = c.title.trim() || "Новый критерий";
    const share = this.querySelector("[data-share]");
    const sum = this.weightSum();
    if (share) share.textContent = sum && Number.isFinite(c.weight) ? `≈ ${Math.round((100 * c.weight) / sum)}% итога, если все критерии применимы` : "";
    this.renderSide();
    this.renderBar();
  }

  renderBar() {
    const ch = this.changes();
    const probs = this.problems();
    const parts = [ch.changed && `изменено ${ch.changed}`, ch.added && `добавлено ${ch.added}`, ch.removed && `удалено ${ch.removed}`].filter(Boolean);
    const errCount = probs.reduce((s, p) => s + p.n, 0);
    const el = this.querySelector("[data-state]");
    el.innerHTML = !ch.total
      ? `<span class="dotc"></span><span>${this.source === this.active.version ? `Это активная версия v${this.active.version}. Изменений нет.` : `Без изменений относительно v${this.active.version}.`}</span>`
      : `<span class="dotc ${errCount && this.showAll ? "err" : "changed"}"></span><span><b>${parts.join(", ") || "изменён порядок"}</b>${parts.length ? ` ${plural(ch.changed + ch.added + ch.removed, "критерий", "критерия", "критериев")}` : ""}
        ${errCount ? ` · <button class="link" data-act="first-error">${errCount} ${plural(errCount, "ошибка", "ошибки", "ошибок")}: показать</button>` : ""}</span>`;
    el.querySelector('[data-act="first-error"]')?.addEventListener("click", () => this.goToFirstError());
    this.querySelector('[data-act="publish"]').disabled = !ch.total;
    this.querySelector('[data-act="reset"]').hidden = !ch.total;
  }

  goToFirstError() {
    this.showAll = true;
    const p = this.problems()[0];
    if (!p) return;
    this.sel = p.i;
    this.renderSide(); this.renderEditor();
    this.querySelector("[data-editor] .field.invalid input, [data-editor] .field.invalid textarea, [data-editor] .field.invalid select")?.focus();
    this.querySelector("[data-editor]").scrollIntoView({ block: "start", behavior: "smooth" });
  }

  openVersion(v) {
    if (this.isDirty() && !confirm("Открыть другую версию? Несохранённые правки пропадут.")) {
      this.querySelector("[data-version]").value = this.source; return;
    }
    if (v === this.active.version) { this.startFrom(this.active); return; }
    api.get(`/api/checklists/${v}`).then((cl) => this.startFrom(cl)).catch((e) => alert(e.message));
  }

  // ---------- publish ----------
  async publish() {
    this.showAll = true;
    if (this.problems().length) { this.goToFirstError(); this.renderBar(); return; }
    const note = this.querySelector("[data-note]");
    if (!note.value.trim()) {
      note.classList.add("invalid"); note.focus();
      note.placeholder = "Опишите изменения: без этого версию не опубликовать";
      return;
    }
    const btn = this.querySelector('[data-act="publish"]');
    btn.disabled = true; btn.textContent = "Публикую…";
    const draft = { ...strip(this.draft), criteria: this.draft.criteria.map(clean) };
    try {
      const lint = await api.post("/api/checklists/lint", { checklist: draft });
      if (lint.errors.length) throw new Error(lint.errors.join("; "));
      const prev = this.active.version;
      const pub = await api.post("/api/checklists", { checklist: draft, note: note.value.trim() });
      [this.active, this.versions] = await Promise.all([api.get("/api/checklists/active"), api.get("/api/checklists")]);
      const sel = this.sel;
      this.startFrom(this.active);
      this.sel = Math.min(sel, this.draft.criteria.length - 1);
      this.result = { from: prev, to: pub.version, warnings: pub.warnings };
      this.render();
      this.querySelector("[data-result]").scrollIntoView({ block: "center", behavior: "smooth" });
    } catch (e) {
      btn.disabled = false; btn.textContent = `Опубликовать v${Math.max(...this.versions.map((v) => v.version)) + 1}`;
      this.querySelector("[data-state]").innerHTML = `<span class="dotc err"></span><span class="err">Не опубликовано: ${esc(e.message)}</span>`;
    }
  }

  renderResult() {
    const r = this.result;
    const box = this.querySelector("[data-result]");
    box.innerHTML = `<section class="card gap-top" data-apply>
      <div class="alert ok">${icon("check", 2.2)}<div><b>Опубликована v${r.to}.</b> Новые оценки пойдут по ней.
        Уже оценённые диалоги останутся на v${r.from}, пока вы их не переоцените.</div></div>
      ${r.warnings?.length ? `<div class="alert warn" style="margin-top:10px">${icon("alert")}<div>Замечания проверки: ${r.warnings.map(esc).join("; ")}</div></div>` : ""}
      <div class="row" style="margin-top:14px">
        <button class="primary" data-act="apply">${icon("refresh")}Применить ко всем диалогам</button>
        <span class="progress-line" data-p>Модели заново посмотрят только изменённые критерии, остальное возьмётся из кэша.</span>
      </div>
      <div data-diff></div></section>`;
    box.querySelector('[data-act="apply"]').onclick = (e) => this.apply(r.from, r.to, e.currentTarget);
  }

  async apply(from, to, btn) {
    const p = this.querySelector("[data-p]");
    btn.disabled = true;
    try {
      let run = await api.post("/api/runs", { checklist_version: to });
      while (run.status === "running") {
        const pct = run.total ? Math.round((100 * run.done) / run.total) : 0;
        p.innerHTML = `<span class="meter"><i style="width:${pct}%"></i></span>Переоценка: ${run.done}/${run.total}`;
        await new Promise((res) => setTimeout(res, 1500));
        run = await api.get(`/api/runs/${run.id}`);
      }
      p.textContent = `Готово: ${run.done}/${run.total}${run.failed.length ? `, ошибок ${run.failed.length}` : ""}`;
      const diff = await api.get(`/api/checklists/diff?from_v=${from}&to_v=${to}`);
      const moved = diff.filter((d) => d.delta);
      const avg = moved.length ? moved.reduce((s, d) => s + d.delta, 0) / diff.length : 0;
      this.querySelector("[data-diff]").innerHTML = `
        <div class="kpis" style="margin-top:16px">
          <div class="kpi"><span class="k">Итог изменился</span><span class="v">${moved.length}</span><span class="s">из ${diff.length} ${plural(diff.length, "диалога", "диалогов", "диалогов")}</span></div>
          <div class="kpi"><span class="k">Средний сдвиг</span><span class="v ${avg > 0 ? "good" : avg < 0 ? "poor" : ""}">${avg > 0 ? "+" : ""}${fmt(avg)}</span><span class="s">баллов на диалог</span></div>
        </div>
        <div class="table-wrap" style="margin-top:12px"><table class="compact"><thead><tr><th>Диалог</th><th class="num">v${from}</th><th class="num">v${to}</th><th class="num">Разница</th></tr></thead><tbody>
        ${diff.map((d) => `<tr><td><a class="id" href="#/dialog/${encodeURIComponent(d.conversation_id)}">${esc(d.conversation_id)}</a></td>
          <td class="num">${fmt(d.from)}</td><td class="num">${fmt(d.to)}</td>
          <td class="num ${d.delta > 0 ? "good" : d.delta < 0 ? "poor" : "faint"}">${d.delta == null ? "—" : d.delta ? (d.delta > 0 ? "+" : "") + fmt(d.delta) : "0"}</td></tr>`).join("")}
        </tbody></table></div>`;
    } catch (e) { p.textContent = `Не получилось: ${e.message}`; btn.disabled = false; }
  }
}
customElements.define("cqa-checklist-editor", CqaChecklistEditor);
