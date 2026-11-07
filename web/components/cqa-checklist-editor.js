import { api, esc, fmt } from "../api.js";

// Checklist editor (Criterion Studio v0): edit / add / replace criteria, lint, publish vN+1,
// apply to all dialogs (only changed criteria hit the LLM), then show the before/after diff.
class CqaChecklistEditor extends HTMLElement {
  async connectedCallback() {
    this.innerHTML = '<div class="panel muted">Загрузка…</div>';
    try {
      this.cl = await api.get("/api/checklists/active");
      this.versions = await api.get("/api/checklists");
      this.render();
    } catch (e) { this.innerHTML = `<div class="panel err">${esc(e.message)}</div>`; }
  }

  render() {
    const cl = this.cl;
    this.innerHTML = `
      <div class="panel row">
        <b>${esc(cl.name)}</b><span class="badge">активная v${cl.version}</span>
        <span class="muted small">версии: ${this.versions.map((v) => `v${v.version}${v.note ? ` (${esc(v.note)})` : ""}`).join(", ")}</span>
      </div>
      <div data-criteria>${cl.criteria.map((c, i) => this.criterionHtml(c, i)).join("")}</div>
      <div class="panel">
        <div class="row">
          <button data-act="add">+ Критерий</button>
          <button data-act="lint">Проверить</button>
          <input data-note placeholder="Что изменили? (в журнал)" size="40">
          <button data-act="publish" class="primary">Сохранить как v${Math.max(...this.versions.map((v) => v.version)) + 1}</button>
        </div>
        <div data-lint class="small" style="margin-top:6px"></div>
        <div data-apply style="margin-top:6px"></div>
      </div>`;
    this.querySelector('[data-act="add"]').onclick = () => {
      this.cl = this.collect();
      const n = this.cl.criteria.length + 1;
      this.cl.criteria.push({ id: `c${String(n).padStart(2, "0")}_new`, section: this.cl.sections?.[0]?.id ?? "", title: "", type: "binary",
        weight: 0, na_allowed: false, na_condition: "", rule: "all", definition: "", atoms: [{ id: "a1", q: "" }] });
      this.render();
    };
    this.querySelector('[data-act="lint"]').onclick = () => this.lint();
    this.querySelector('[data-act="publish"]').onclick = () => this.publish();
    this.querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => {
      this.cl = this.collect();
      this.cl.criteria.splice(Number(b.dataset.del), 1);
      this.render();
    }));
  }

  criterionHtml(c, i) {
    const sections = (this.cl.sections || []).map((s) =>
      `<option value="${esc(s.id)}" ${s.id === c.section ? "selected" : ""}>${esc(s.title)}</option>`).join("");
    const atoms = (c.atoms || []).map((a) => `${a.id}: ${a.q}`).join("\n");
    const scale = (c.scale || []).map((s) => `${s.step}: ${s.label}`).join("\n");
    return `
      <div class="editor-crit" data-i="${i}">
        <div class="grid4">
          <div><label>ID</label><input data-f="id" value="${esc(c.id)}"></div>
          <div><label>Раздел</label><select data-f="section">${sections}</select></div>
          <div><label>Тип</label><select data-f="type">${["binary", "scale", "flag"].map((t) =>
            `<option ${t === c.type ? "selected" : ""}>${t}</option>`).join("")}</select></div>
          <div><label>Вес</label><input data-f="weight" type="number" min="0" value="${esc(c.weight)}"></div>
          <div><label>Правило атомов</label><select data-f="rule">${["all", "any"].map((r) =>
            `<option ${r === (c.rule ?? "all") ? "selected" : ""}>${r}</option>`).join("")}</select></div>
          <div><label><input type="checkbox" data-f="na_allowed" ${c.na_allowed ? "checked" : ""}> N/A разрешён</label></div>
        </div>
        <label>Название</label><input data-f="title" value="${esc(c.title)}" style="width:100%">
        <label>Определение (что значит «Да»)</label><textarea data-f="definition" rows="2">${esc(c.definition)}</textarea>
        <label>Условие N/A</label><textarea data-f="na_condition" rows="1">${esc(c.na_condition ?? "")}</textarea>
        <label>Атомы да/нет для binary/flag — по одному на строку: <code>id: вопрос</code></label>
        <textarea data-f="atoms" rows="3">${esc(atoms)}</textarea>
        <label>Шкала для scale — по одной ступени на строку: <code>балл: описание</code></label>
        <textarea data-f="scale" rows="3">${esc(scale)}</textarea>
        <div class="row" style="margin-top:6px"><button data-del="${i}">Удалить критерий</button></div>
      </div>`;
  }

  collect() {
    const lines = (s, key, valKey, num) => s.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
      const at = l.indexOf(":");
      const k = (at < 0 ? l : l.slice(0, at)).trim();
      return { [key]: num ? Number(k) : k, [valKey]: at < 0 ? "" : l.slice(at + 1).trim() };
    });
    const criteria = [...this.querySelectorAll(".editor-crit")].map((el) => {
      const v = (f) => el.querySelector(`[data-f="${f}"]`);
      const c = {
        id: v("id").value.trim(), section: v("section").value, title: v("title").value.trim(), type: v("type").value,
        weight: Number(v("weight").value), na_allowed: v("na_allowed").checked, na_condition: v("na_condition").value.trim(),
        rule: v("rule").value, definition: v("definition").value.trim(),
      };
      if (c.type === "scale") c.scale = lines(v("scale").value, "step", "label", true).sort((a, b) => b.step - a.step);
      else c.atoms = lines(v("atoms").value, "id", "q", false);
      return c;
    });
    return { ...this.cl, criteria };
  }

  showLint(r) {
    this.querySelector("[data-lint]").innerHTML =
      r.errors.map((e) => `<div class="err">✗ ${esc(e)}</div>`).join("") +
      r.warnings.map((w) => `<div style="color:var(--warn)">! ${esc(w)}</div>`).join("") +
      (!r.errors.length && !r.warnings.length ? '<div style="color:var(--ok)">✓ без замечаний</div>' : "");
  }

  async lint() {
    try { this.showLint(await api.post("/api/checklists/lint", { checklist: this.collect() })); }
    catch (e) { this.querySelector("[data-lint]").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  }

  async publish() {
    const draft = this.collect();
    const r = await api.post("/api/checklists/lint", { checklist: draft });
    this.showLint(r);
    if (r.errors.length) return;
    try {
      const prev = this.cl.version;
      const pub = await api.post("/api/checklists", { checklist: draft, note: this.querySelector("[data-note]").value });
      this.cl = await api.get("/api/checklists/active");
      this.versions = await api.get("/api/checklists");
      this.render();
      this.showLint({ errors: [], warnings: pub.warnings });
      const box = this.querySelector("[data-apply]");
      box.innerHTML = `<div class="row"><b>Опубликована v${pub.version}.</b>
        <button class="primary" data-act="apply">Применить ко всем диалогам</button><span class="muted" data-p></span></div><div data-diff></div>`;
      box.querySelector('[data-act="apply"]').onclick = () => this.apply(prev, pub.version);
    } catch (e) { this.querySelector("[data-lint]").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  }

  async apply(from, to) {
    const p = this.querySelector("[data-p]");
    try {
      let run = await api.post("/api/runs", { checklist_version: to });
      while (run.status === "running") {
        p.textContent = `${run.done}/${run.total}…`;
        await new Promise((r) => setTimeout(r, 1500));
        run = await api.get(`/api/runs/${run.id}`);
      }
      p.textContent = `готово ${run.done}/${run.total}${run.failed.length ? `, ошибок ${run.failed.length}` : ""}`;
      const diff = await api.get(`/api/checklists/diff?from_v=${from}&to_v=${to}`);
      this.querySelector("[data-diff]").innerHTML = `<table><thead><tr><th>Диалог</th><th>v${from}</th><th>v${to}</th><th>Δ</th></tr></thead><tbody>
        ${diff.map((r) => `<tr><td><a href="#/dialog/${encodeURIComponent(r.conversation_id)}">${esc(r.conversation_id)}</a></td>
          <td>${fmt(r.from)}</td><td>${fmt(r.to)}</td><td>${r.delta == null ? "—" : (r.delta > 0 ? "+" : "") + fmt(r.delta)}</td></tr>`).join("")}
        </tbody></table>`;
    } catch (e) { p.textContent = `Ошибка: ${e.message}`; }
  }
}
customElements.define("cqa-checklist-editor", CqaChecklistEditor);
