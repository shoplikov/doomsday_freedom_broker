"""Real-browser test of the whole demo flow (mock LLM). Skipped unless Playwright is available:

    uv run --with playwright pytest tests/e2e -q
    # browser: system Chromium (CQA_CHROMIUM, default /usr/bin/chromium) or `playwright install chromium`
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def server(fresh_database_url):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    tmp = tempfile.mkdtemp()
    env = {**os.environ, "DATABASE_URL": fresh_database_url(), "CQA_LOG_DIR": f"{tmp}/logs",
           "LLM_PROVIDER": "mock", "DATASET_API_URL": ""}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "cqa.main:app", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/api/health")
            break
        except OSError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(5)


@pytest.fixture(scope="module")
def page(server):
    exe = os.environ.get("CQA_CHROMIUM", "/usr/bin/chromium")
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe if Path(exe).exists() else None)
        pg = browser.new_page(viewport={"width": 1400, "height": 900})
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.on("console", lambda m: m.type == "error" and "example.invalid" not in m.text
              and "Failed to load resource" not in m.text and pg.errors.append(m.text))
        pg.goto(server)
        yield pg
        browser.close()


def test_demo_flow(page, server):
    expect = sync_api.expect
    page.set_default_timeout(15000)

    # 1. list: import SAMPLE and evaluate everything
    page.get_by_text("Загрузить набор").click()
    expect(page.locator("[data-progress]")).to_have_text("Загружено: 5")
    page.get_by_text("Оценить все").click()
    expect(page.locator("[data-progress]")).to_contain_text("Готово: 5/5")
    expect(page.locator("tr[data-id]")).to_have_count(5)
    page.select_option('select[name="language"]', "KZ")
    expect(page.locator("tr[data-id]")).to_have_count(1)
    page.select_option('select[name="language"]', "")

    # 2. card: evidence chip jumps to the message, masked vs raw text, trace of all three roles
    page.locator('tr[data-id="s-ru-001"]').click()
    expect(page.locator(".total")).to_be_visible()
    expect(page.locator(".crit")).to_have_count(8)
    page.locator(".crit .chip[data-mid]").first.click()
    expect(page.locator(".msg.hl")).to_have_count(1)
    expect(page.locator("#msg-m2")).to_contain_text("[IIN_1]")
    page.locator("[data-raw]").check()
    expect(page.locator("#msg-m2")).to_contain_text("900101300123")
    page.locator('[data-trace-toggle="c01"]').click()
    trace = page.locator('[data-crit="c01"] .trace')
    expect(trace).to_contain_text("M1 Инспектор")
    expect(trace).to_contain_text("M3 Судья")
    trace.locator("[data-trace-id]").first.click()
    expect(trace.locator("details[open] pre")).to_contain_text("criteria")  # raw model response
    expect(trace.locator("details pre").first).to_contain_text("quality-assurance panel")  # exact prompt

    # 3. gold label is saved and survives reload
    page.select_option('[data-gold="c01"]', "yes")
    expect(page.locator('[data-gold="c01"] + span')).to_have_text("сохранено")

    # 4. operator appeals, reviewer changes the verdict, total is recalculated
    page.locator('[data-role="operator"]').click()
    page.locator('[data-appeal="c05"]').click()
    page.locator('[data-crit="c05"] textarea').fill("Оператор ответил на проблему в m5")
    page.get_by_text("Отправить апелляцию").click()
    expect(page.locator('[data-crit="c05"]')).to_contain_text("апелляция")
    before = page.locator(".total").inner_text()
    page.locator('[data-role="reviewer"]').click()
    page.goto(server + "/#/review")
    expect(page.locator("cqa-review-queue")).to_contain_text("s-ru-001")
    page.locator("cqa-review-queue [data-href]").first.click()
    page.locator('[data-decide="c05"]').click()
    form = page.locator('[data-crit="c05"] .form')
    form.locator("[data-action]").select_option("change")
    current = page.locator('[data-crit="c05"] .row b').first.inner_text()
    form.locator("[data-verdict]").select_option("no" if current == "Да" else "yes")
    form.locator("textarea").fill("Проверено по m5")
    form.get_by_text("Сохранить решение").click()
    expect(page.locator('[data-crit="c05"]')).to_contain_text("решение reviewer")
    expect(page.locator(".layers")).to_contain_text("ИИ:")
    assert page.locator(".total").inner_text() != before

    # 5. replace a criterion through the UI, publish v2, apply to all, see the diff
    page.goto(server + "/#/checklist")
    crit = page.locator('.editor-crit:has(input[data-f="id"][value="c07"])')
    crit.locator('[data-f="definition"]').fill("Да — оператор предложил мобильное приложение для следующего обращения и попрощался.")
    crit.locator('[data-f="atoms"]').fill("offered_app: Предложил ли оператор мобильное приложение?\nfarewell: Попрощался ли оператор?")
    page.locator("[data-note]").fill("замена c07")
    page.get_by_text("Сохранить как v2").click()
    expect(page.locator("[data-apply]")).to_contain_text("Опубликована v2")
    page.get_by_text("Применить ко всем диалогам").click()
    expect(page.locator("[data-diff] tbody tr")).to_have_count(5)

    # 6. lint blocks a broken criterion
    page.get_by_text("+ Критерий").click()
    page.get_by_text("Проверить").click()
    expect(page.locator("[data-lint]")).to_contain_text("title is empty")

    # 7. analytics for v1 shows the gold label and the panel numbers
    page.goto(server + "/#/analytics")
    page.select_option("[data-version]", "1")
    expect(page.locator(".kpis")).to_contain_text("меток 1")
    expect(page.locator("cqa-analytics")).to_contain_text("Где M1 и M2 разошлись")
    page.locator("summary", has_text="Где M1 и M2 разошлись").click()

    # 8. Dynamics 365 entry point: ?data=<conversation_id> opens the card
    page.goto(server + "/?data=s-call-001")
    expect(page.locator("audio")).to_have_count(1)
    expect(page.locator(".crit")).to_have_count(8)

    assert page.errors == [], page.errors
