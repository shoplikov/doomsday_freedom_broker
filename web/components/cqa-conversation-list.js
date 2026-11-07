import { api, channelText, errorBox, esc, fmt, getPref, icon, pageHead, plural, scoreBar, setPref, statusBadge } from "../api.js";

const GROUP_RE = /^[A-Za-z0-9_-]{1,40}$/;

// Dialog list: import a dataset by group_id, run a batch evaluation, filter, open a card.
class CqaConversationList extends HTMLElement {
  async connectedCallback() {
    this.rows = [];
    this.busy = false;
    this.health = await api.get("/api/health").catch(() => ({}));
    const group = getPref("group", this.health.dataset_api ? "DEV" : "SAMPLE");
    this.innerHTML = `<div class="page-enter">
      ${pageHead("Диалоги",
        "Каждый чат и звонок разбирают три модели: строгий инспектор, адвокат оператора и судья. Баллы считает код, спорное решают люди.",
        `<button data-act="export" title="Файл с оценками в формате организаторов">${icon("download")}Скачать results.json</button>`)}
      <div data-onboard></div>
      <div data-stats></div>
      <section class="card flush">
        <div class="list-head">
          <form class="toolbar grow" data-import novalidate>
            <div class="field" style="width:170px">
              <label for="group">Набор данных</label>
              <input id="group" name="group" value="${esc(group)}" class="mono" autocomplete="off" spellcheck="false" aria-describedby="group-hint">
            </div>
            <button type="submit" data-act="import">${icon("upload")}Загрузить</button>
            <button type="button" data-act="run" class="primary">${icon("play")}Оценить все</button>
            <span class="sep" aria-hidden="true"></span>
            <div class="field" style="width:200px"><label for="q">Поиск</label>
              <span class="search"><input id="q" type="search" name="q" placeholder="ID диалога" autocomplete="off">${icon("search")}</span></div>
            <div class="field" style="width:160px"><label for="f-status">Статус</label>
              <select id="f-status" name="status">
                <option value="">Все</option><option value="needs_review">Нужна проверка</option>
                <option value="gross">Грубые ошибки</option><option value="none">Не оценены</option></select></div>
            <div class="field" style="width:110px"><label for="f-lang">Язык</label>
              <select id="f-lang" name="language"><option value="">Все</option><option>RU</option><option>KZ</option><option>EN</option></select></div>
            <div class="field" style="width:130px"><label for="f-ch">Канал</label>
              <select id="f-ch" name="channel"><option value="">Все</option><option value="agent chat">Чат</option><option value="voice call">Звонок</option></select></div>
          </form>
        </div>
        <div class="list-head" style="padding-top:10px;padding-bottom:10px">
          <span id="group-hint" class="progress-line" data-progress role="status">${this.health.dataset_api
            ? "Наборы берутся из API организаторов. SAMPLE работает без сети."
            : "API организаторов не настроен: доступен демо-набор SAMPLE."}</span>
          <span class="grow"></span><span class="small faint" data-count></span>
        </div>
        <div data-list><div class="loading">Загрузка…</div></div>
      </section></div>`;
    this.querySelector("[data-import]").onsubmit = (e) => { e.preventDefault(); this.importGroup(); };
    this.querySelector('[data-act="run"]').onclick = () => this.runAll();
    this.querySelector('[data-act="export"]').onclick = () => this.exportResults();
    this.querySelector("#group").oninput = () => this.querySelector("#group").closest(".field").classList.remove("invalid");
    for (const name of ["language", "channel"]) this.querySelector(`[name="${name}"]`).onchange = () => this.load();
    this.querySelector('[name="status"]').onchange = () => this.renderTable();
    this.querySelector('[name="q"]').oninput = () => this.renderTable();
    this.load();
  }

  get group() { return this.querySelector("#group").value.trim(); }

  validateGroup() {
    const ok = GROUP_RE.test(this.group);
    this.querySelector("#group").closest(".field").classList.toggle("invalid", !ok);
    if (!ok) {
      this.progress("Введите код набора: латиница, цифры, «-» или «_», например DEV или SAMPLE.", true);
      this.querySelector("#group").focus();
    }
    return ok;
  }

  progress(text, isErr = false, pct = null) {
    const el = this.querySelector("[data-progress]");
    el.classList.toggle("err", isErr);
    el.innerHTML = `${pct != null ? `<span class="meter"><i style="width:${pct}%"></i></span>` : ""}${esc(text)}`;
  }

  setBusy(on) {
    this.busy = on;
    this.querySelectorAll('[data-act="import"], [data-act="run"]').forEach((b) => (b.disabled = on));
  }

  async importGroup() {
    if (this.busy || !this.validateGroup()) return;
    setPref("group", this.group);
    this.setBusy(true);
    this.progress(`Загружаю набор ${this.group}…`);
    try {
      const r = await api.post(`/api/datasets/${encodeURIComponent(this.group)}/import`);
      this.progress(`Загружено: ${r.imported}`);
      await this.load();
    } catch (e) { this.progress(`Не удалось загрузить ${this.group}: ${e.message}`, true); }
    this.setBusy(false);
  }

  async runAll() {
    if (this.busy) return;
    if (!this.rows.length) { this.progress("Сначала загрузите набор диалогов.", true); return; }
    this.setBusy(true);
    try {
      let run = await api.post("/api/runs", {});
      while (run.status === "running") {
        const pct = run.total ? Math.round((100 * run.done) / run.total) : 0;
        this.progress(`Оценка: ${run.done}/${run.total}${run.failed.length ? `, ошибок ${run.failed.length}` : ""}`, false, pct);
        await new Promise((r) => setTimeout(r, 1500));
        run = await api.get(`/api/runs/${run.id}`);
      }
      this.progress(`Готово: ${run.done}/${run.total}${run.failed.length
        ? `, ошибок ${run.failed.length}. Запустите ещё раз: оценённые диалоги не пересчитываются.` : ""}`, run.failed.length > 0);
      await this.load();
      window.dispatchEvent(new Event("cqa:queue-changed"));
    } catch (e) { this.progress(`Оценка не запустилась: ${e.message}`, true); }
    this.setBusy(false);
  }

  // The organisers' results file (format 1.0) for the group in the input, as a download.
  async exportResults() {
    if (!this.validateGroup()) return;
    try {
      const doc = await api.get(`/api/results/${encodeURIComponent(this.group)}`);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2) + "\n"], { type: "application/json" }));
      a.download = "results.json";
      a.click();
      URL.revokeObjectURL(a.href);
      const errors = doc.results.filter((r) => r.status !== "ok").length;
      this.progress(`results.json для ${this.group}: ${doc.results.length} ${plural(doc.results.length, "диалог", "диалога", "диалогов")}`
        + (errors ? `, ${errors} с ошибкой. Оцените их перед сдачей.` : ", все оценены."), errors > 0);
    } catch (e) { this.progress(`Не удалось собрать results.json: ${e.message}`, true); }
  }

  onboardHtml() {
    if (getPref("onboard-hidden") === "1") return "";
    const scored = this.rows.filter((r) => r.total != null).length;
    const waiting = this.rows.filter((r) => r.status === "needs_review").length;
    const s1 = this.rows.length > 0, s2 = s1 && scored === this.rows.length, s3 = s2 && !waiting;
    const cls = (done, now) => (done ? "done" : now ? "now" : "");
    return `<section class="card onboard-card">
      <div class="card-head"><h2>Как это работает</h2><button class="ghost sm" data-act="hide-onboard">Понятно, скрыть</button></div>
      <div class="onboard">
        <div class="step ${cls(s1, !s1)}"><span class="n">${s1 ? "✓" : 1}</span><b>Загрузите диалоги</b>
          <p>Введите код набора ниже и нажмите «Загрузить». Для пробы подойдёт SAMPLE.</p></div>
        <div class="step ${cls(s2, s1 && !s2)}"><span class="n">${s2 ? "✓" : 2}</span><b>Оцените их</b>
          <p>«Оценить все» отправит диалоги моделям. Откройте любой, чтобы увидеть баллы и цитаты-доказательства.</p></div>
        <div class="step ${cls(s3, s2 && !s3)}"><span class="n">${s3 ? "✓" : 3}</span><b>Разберите спорное</b>
          <p>${waiting ? `<a href="#/review">${waiting} ${plural(waiting, "диалог ждёт", "диалога ждут", "диалогов ждут")} человека.</a> ` : ""}Где модели не уверены, решает проверяющий. Всё пишется в журнал.</p></div>
      </div></section>`;
  }

  stats(rows) {
    const scored = rows.filter((r) => r.total != null);
    const avg = scored.length ? scored.reduce((s, r) => s + r.total, 0) / scored.length : null;
    const count = (st) => rows.filter((r) => r.status === st).length;
    const bins = Array.from({ length: 10 }, (_, i) => scored.filter((r) => Math.min(9, Math.floor(r.total / 10)) === i).length);
    const peak = Math.max(1, ...bins);
    const left = rows.length - scored.length;
    return `<div class="overview">
      <div class="kpis">
        <div class="kpi"><span class="k">Средний итог</span><span class="v">${avg == null ? "—" : fmt(avg)}</span><span class="s">из 100 по ${scored.length} ${plural(scored.length, "оценённому", "оценённым", "оценённым")}</span></div>
        <div class="kpi"><span class="k">Диалогов</span><span class="v">${rows.length}</span><span class="s">${left ? `${left} ещё без оценки` : "все оценены"}</span></div>
        <div class="kpi"><span class="k">Принято</span><span class="v good">${count("auto_accepted") + count("reviewed")}</span><span class="s">из них проверено людьми: ${count("reviewed")}</span></div>
        <div class="kpi"><span class="k">Ждут человека</span><span class="v">${count("needs_review")}</span><span class="s">грубых ошибок: ${rows.filter((r) => r.gross_error).length}</span></div>
      </div>
      <div class="kpi"><span class="k">Распределение итогов</span>
        <div class="histo" role="img" aria-label="Сколько диалогов в каждом диапазоне баллов">${bins.map((n, i) => `<div title="${i * 10}–${i * 10 + 10} баллов: ${n}">
          <span class="bar"><i class="${n ? (i < 6 ? "low" : "") : "zero"}" style="height:${(n / peak) * 100}%"></i></span><span>${i * 10}</span></div>`).join("")}</div>
      </div></div>`;
  }

  async load() {
    const q = new URLSearchParams();
    for (const name of ["language", "channel"]) {
      const v = this.querySelector(`[name="${name}"]`).value;
      if (v) q.set(name, v);
    }
    try {
      this.rows = await api.get(`/api/conversations?${q}`);
      this.querySelector("[data-stats]").innerHTML = this.rows.length ? this.stats(this.rows) : "";
      this.querySelector("[data-onboard]").innerHTML = this.onboardHtml();
      const hide = this.querySelector('[data-act="hide-onboard"]');
      if (hide) hide.onclick = () => { setPref("onboard-hidden", "1"); this.querySelector("[data-onboard]").innerHTML = ""; };
      this.renderTable();
    } catch (e) { this.querySelector("[data-list]").innerHTML = `<div style="padding:16px">${errorBox(e.message)}</div>`; }
  }

  renderTable() {
    const box = this.querySelector("[data-list]");
    const text = this.querySelector('[name="q"]').value.trim().toLowerCase();
    const st = this.querySelector('[name="status"]').value;
    const rows = this.rows.filter((r) => (!text || r.id.toLowerCase().includes(text))
      && (!st || (st === "gross" ? r.gross_error : st === "none" ? r.total == null : r.status === st)));
    this.querySelector("[data-count]").textContent = !this.rows.length ? ""
      : rows.length === this.rows.length ? `${rows.length} ${plural(rows.length, "диалог", "диалога", "диалогов")}` : `показано ${rows.length} из ${this.rows.length}`;
    if (!this.rows.length) {
      box.innerHTML = `<div class="empty"><b>Здесь пока пусто</b><p>Загрузите набор диалогов кнопкой «Загрузить» выше. SAMPLE — пять демо-диалогов, работает без сети.</p></div>`;
      return;
    }
    if (!rows.length) {
      box.innerHTML = `<div class="empty"><b>Ничего не нашлось</b><p>Ни один диалог не подходит под поиск и фильтры.</p>
        <div class="row"><button data-act="reset">Сбросить поиск и статус</button></div></div>`;
      box.querySelector('[data-act="reset"]').onclick = () => {
        this.querySelector('[name="q"]').value = ""; this.querySelector('[name="status"]').value = ""; this.renderTable();
      };
      return;
    }
    box.innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th>ID</th><th>Канал</th><th>Язык</th><th>Итог</th><th>Статус</th><th>Грубая ошибка</th><th>Чек-лист</th></tr></thead>
      <tbody>${rows.map((r) => `
        <tr class="click" data-id="${esc(r.id)}" tabindex="0">
          <td><span class="id">${esc(r.id)}</span></td>
          <td>${esc(channelText[r.channel] ?? r.channel)}</td>
          <td><span class="badge">${esc(r.language)}</span></td>
          <td style="min-width:160px">${scoreBar(r.total)}</td>
          <td>${statusBadge(r.status)}</td>
          <td>${r.gross_error ? '<span class="badge bad">Есть</span>' : '<span class="faint">—</span>'}</td>
          <td class="faint">${r.checklist_version ? `v${r.checklist_version}` : ""}</td>
        </tr>`).join("")}</tbody></table></div>`;
    box.querySelectorAll("tr[data-id]").forEach((tr) => {
      const open = () => (location.hash = `#/dialog/${encodeURIComponent(tr.dataset.id)}`);
      tr.onclick = open;
      tr.onkeydown = (e) => { if (e.key === "Enter") open(); };
    });
  }
}
customElements.define("cqa-conversation-list", CqaConversationList);
