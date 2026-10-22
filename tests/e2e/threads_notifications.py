"""Сквозная проверка: треды и уведомления Cinny против локального Synapse."""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid

from playwright.sync_api import sync_playwright

HS = os.environ.get("HS_URL", "http://127.0.0.1:8008")
APP = os.environ.get("APP_URL", "http://localhost:8080")
SHOTS = os.environ.get("SHOTS_DIR", os.path.dirname(os.path.abspath(__file__)) + "/shots")
os.makedirs(SHOTS, exist_ok=True)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


def api(method, path, token=None, body=None):
    req = urllib.request.Request(HS + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body is not None else None
    with opener.open(req, data) as r:
        return json.loads(r.read() or b"{}")


def login(user):
    r = api("POST", "/_matrix/client/v3/login", body={
        "type": "m.login.password", "identifier": {"type": "m.id.user", "user": user},
        "password": "pass123"})
    return r


def send(token, room, content, etype="m.room.message"):
    txn = uuid.uuid4().hex
    return api("PUT", f"/_matrix/client/v3/rooms/{room}/send/{etype}/{txn}", token, content)["event_id"]


def text(body):
    return {"msgtype": "m.text", "body": body}


def thread_reply(root, body, extra=None):
    c = text(body)
    c["m.relates_to"] = {"rel_type": "m.thread", "event_id": root, "is_falling_back": True,
                         "m.in_reply_to": {"event_id": root}}
    if extra:
        c.update(extra)
    return c


bob = login("bob")
alice_api = login("alice")
alice_ui = login("alice")
B, A = bob["access_token"], alice_api["access_token"]

room = api("POST", "/_matrix/client/v3/createRoom", B, {
    "name": "Тест-канал", "preset": "private_chat", "invite": ["@alice:localhost"]})["room_id"]
api("POST", f"/_matrix/client/v3/join/{room}", A, {})
# третий участник, чтобы комната не считалась личкой
root_b = send(B, room, text("Корень Боба"))
root_a = send(A, room, text("Корень Алисы"))
r1 = send(B, room, thread_reply(root_b, "ответ1 в треде Боба"))

MOCK = r"""
window.__calls = []; window.__cbs = {}; window.__listeners = []; let __cb = 1;
window.isTauri = true;
window.__TAURI_INTERNALS__ = {
  transformCallback(cb) { const id = __cb++; window.__cbs[id] = cb; return id; },
  unregisterCallback(id) { delete window.__cbs[id]; },
  convertFileSrc: (p) => p,
  invoke: async (cmd, args) => {
    const clean = JSON.parse(JSON.stringify(args ?? {}, (k, v) =>
      (k === 'icon' || k === 'badge') && typeof v === 'string' ? `<png ${v.length}>` : v));
    window.__calls.push({ cmd, args: clean });
    if (cmd === 'plugin:event|listen') { window.__listeners.push({ event: args.event, handler: args.handler }); return window.__listeners.length; }
    if (cmd === 'desktop_get_settings') return { closeToTray: true, startMinimized: true, autostart: false, platform: 'windows' };
    if (cmd === 'desktop_set_settings') return { closeToTray: true, startMinimized: true, autostart: !!args.patch.autostart, platform: 'windows' };
    return null;
  },
};
window.__emit = (event, payload) => window.__listeners.filter((l) => l.event === event)
  .forEach((l) => window.__cbs[l.handler] && window.__cbs[l.handler]({ event, id: 0, payload }));
"""

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-proxy-server", "--autoplay-policy=no-user-gesture-required"])
    ctx = browser.new_context(viewport={"width": 1400, "height": 900}, locale="ru-RU")
    page = ctx.new_page()
    page.route("**/config.json", lambda route: route.fulfill(json={"homeserverList":[HS],"allowCustomHomeservers":True,"hashRouter":{"enabled":False,"basename":"/"}}))
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(MOCK)
    page.goto(APP + "/config.json")
    page.evaluate("""(s) => {
      localStorage.setItem('cinny_access_token', s.access_token);
      localStorage.setItem('cinny_device_id', s.device_id);
      localStorage.setItem('cinny_user_id', s.user_id);
      localStorage.setItem('cinny_hs_base_url', 'http://127.0.0.1:8008');
    }""", alice_ui)
    page.goto(APP + "/")
    page.wait_for_timeout(6000)
    # закрыть возможные диалоги (проверка устройства и т.п.)
    page.keyboard.press("Escape")

    page.goto(APP + "/home/" + room + "/")
    page.wait_for_selector("text=Корень Алисы", timeout=30000)
    page.wait_for_timeout(2000)
    page.screenshot(path=SHOTS + "/01-room.png")

    timeline_text = page.locator("[data-message-id]").all_inner_texts()
    joined = "\n".join(timeline_text)
    check("в ленте есть корневые сообщения", "Корень Боба" in joined and "Корень Алисы" in joined)
    check("ответ из треда не попал в общую ленту", page.locator(f"[data-message-id='{r1}']").count() == 0)
    summary = page.locator("button:has-text('1 ответ')")
    check("под корнем есть плашка треда «1 ответ»", summary.count() >= 1)
    check("сохранение схемы кэша", page.evaluate("localStorage.getItem('cinny_sync_store_schema')") == "threads-v1")

    # Открыть тред по плашке
    summary.first.click()
    page.wait_for_selector("text=ответ1 в треде Боба", timeout=15000)
    page.wait_for_timeout(1500)
    page.screenshot(path=SHOTS + "/02-thread-open.png")
    check("панель треда открылась и показывает ответ", page.locator("text=ответ1 в треде Боба").count() >= 1)

    # Ответить из панели треда
    page.locator("[data-editable-name='ThreadInput']").click()
    page.keyboard.type("Ответ Алисы из панели")
    page.keyboard.press("Enter")
    page.wait_for_timeout(2500)
    msgs = api("GET", f"/_matrix/client/v3/rooms/{room}/messages?dir=b&limit=20", B)["chunk"]
    mine = [e for e in msgs if e.get("content", {}).get("body") == "Ответ Алисы из панели"]
    rel = mine[0]["content"].get("m.relates_to", {}) if mine else {}
    check("сообщение из панели ушло в тред (m.thread → корень Боба)",
          rel.get("rel_type") == "m.thread" and rel.get("event_id") == root_b, json.dumps(rel))
    main_now = "\n".join(page.locator("[data-message-id]").all_inner_texts())
    page.screenshot(path=SHOTS + "/03-thread-reply.png")

    # --- Уведомления ---
    page.evaluate("document.hasFocus = () => false")
    page.evaluate("window.__calls = []")
    m1 = send(B, room, text("Привет всем"))
    page.wait_for_timeout(3000)
    notes = [c for c in page.evaluate("window.__calls") if c["cmd"] == "desktop_notify"]
    check("уведомление о сообщении в общей ленте", any("Привет всем" in n["args"]["payload"]["body"] for n in notes),
          json.dumps([n["args"]["payload"] for n in notes], ensure_ascii=False)[:400])
    if notes:
        pl = notes[-1]["args"]["payload"]
        check("в уведомлении есть аватар и цель клика", pl.get("icon", "").startswith("<png") and pl["target"]["roomId"] == room, json.dumps(pl, ensure_ascii=False))

    page.evaluate("window.__calls = []")
    root_c = send(B, room, text("Корень C"))
    page.wait_for_timeout(1500)
    page.evaluate("window.__calls = []")
    send(B, room, thread_reply(root_c, "чужой тред без упоминания"))
    page.wait_for_timeout(3000)
    notes = [c for c in page.evaluate("window.__calls") if c["cmd"] == "desktop_notify"]
    check("нет уведомления об ответе в чужом треде", len(notes) == 0, json.dumps(notes, ensure_ascii=False)[:300])

    page.evaluate("window.__calls = []")
    ra = send(B, room, thread_reply(root_a, "ответ в тред Алисы"))
    page.wait_for_timeout(3000)
    notes = [c for c in page.evaluate("window.__calls") if c["cmd"] == "desktop_notify"]
    ok = any(n["args"]["payload"]["target"].get("threadId") == root_a for n in notes)
    check("уведомление об ответе в треде Алисы (с threadId)", ok,
          json.dumps([n["args"]["payload"] for n in notes], ensure_ascii=False)[:400])
    if notes:
        check("подпись «В треде: …»", "В треде" in (notes[-1]["args"]["payload"].get("attribution") or ""),
              notes[-1]["args"]["payload"].get("attribution"))

    page.evaluate("window.__calls = []")
    send(B, room, thread_reply(root_c, "@alice:localhost глянь", {
        "m.mentions": {"user_ids": ["@alice:localhost"]},
        "format": "org.matrix.custom.html",
        "formatted_body": '<a href="https://matrix.to/#/@alice:localhost">alice</a> глянь'}))
    page.wait_for_timeout(3000)
    notes = [c for c in page.evaluate("window.__calls") if c["cmd"] == "desktop_notify"]
    check("упоминание в чужом треде всё равно уведомляет", len(notes) >= 1)

    # счётчик на панели задач
    unread_calls = [c for c in page.evaluate("window.__calls") if c["cmd"] == "desktop_set_unread"]
    page.evaluate("window.__calls = []")
    send(B, room, text("для счётчика"))
    page.wait_for_timeout(2500)
    unread_calls = [c for c in page.evaluate("window.__calls") if c["cmd"] == "desktop_set_unread"]
    check("обновление счётчика на панели задач", any(c["args"]["payload"]["total"] > 0 for c in unread_calls),
          json.dumps(unread_calls, ensure_ascii=False)[:300])

    # «Не беспокоить» из трея
    page.evaluate("window.__calls = []")
    page.evaluate("window.__emit('desktop://dnd', '1h')")
    page.wait_for_timeout(800)
    settings = json.loads(page.evaluate("localStorage.getItem('settings')"))
    check("DND из трея сохранился в настройках", settings.get("dndUntil", 0) > time.time() * 1000)
    calls = page.evaluate("window.__calls")
    check("трей получил desktop_set_dnd(true)", any(c["cmd"] == "desktop_set_dnd" and c["args"]["active"] for c in calls))
    page.evaluate("window.__calls = []")
    send(B, room, text("сообщение во время DND"))
    page.wait_for_timeout(2500)
    notes = [c for c in page.evaluate("window.__calls") if c["cmd"] == "desktop_notify"]
    check("в режиме DND уведомлений нет", len(notes) == 0)
    page.evaluate("window.__emit('desktop://dnd', 'off')")
    page.wait_for_timeout(800)
    settings = json.loads(page.evaluate("localStorage.getItem('settings')"))
    check("DND выключается из трея", settings.get("dndUntil") == 0)

    # Клик по уведомлению → тред
    page.evaluate("document.hasFocus = () => true")
    page.goto(APP + "/home/")
    page.wait_for_timeout(2000)
    page.evaluate(f"window.__emit('desktop://notification-click', {{roomId: '{room}', eventId: '{ra}', threadId: '{root_a}'}})")
    try:
        page.wait_for_selector("text=ответ в тред Алисы", timeout=15000)
        found = True
    except Exception:
        found = False
    page.wait_for_timeout(1500)
    page.screenshot(path=SHOTS + "/04-click-to-thread.png")
    check("клик по уведомлению открыл комнату и нужный тред",
          found and room in urllib.parse.unquote(page.url), page.url)
    reply_el = page.locator(f"[data-message-id='{ra}']").last
    rtxt = reply_el.inner_text() if reply_el.count() else ""
    check("в треде нет цитаты-заглушки и метки Thread", "Thread" not in rtxt and "Корень Алисы" not in rtxt, rtxt)

    # Список тредов
    page.get_by_role("button", name="К списку тредов").click()
    page.wait_for_timeout(2500)
    page.screenshot(path=SHOTS + "/05-thread-list.png")
    panel_text = page.locator("text=Треды").count()
    # треды в левом списке комнат — отдельная проверка, здесь только панель справа
    NAV = ":not([aria-label='Активные треды'] *)"
    items = page.locator(f"button:has-text('Корень C'){NAV}").count()
    check("список тредов показывает треды комнаты", items >= 1)
    b_item = page.locator(f"button:has-text('Корень Боба'){NAV}").first.inner_text().split("\n")
    check("прочитанный тред Боба без счётчика", not any(x.strip().isdigit() for x in b_item), str(b_item))
    page.get_by_text("Мои", exact=True).click()
    page.wait_for_timeout(1000)
    page.screenshot(path=SHOTS + "/06-thread-list-mine.png")
    mine_has_c = page.locator(f"button:has-text('Корень C'){NAV}").count()
    mine_has_a = page.locator(f"button:has-text('Корень Алисы'){NAV}").count()
    check("фильтр «Мои»: только треды Алисы", mine_has_a >= 1 and mine_has_c == 0,
          f"A={mine_has_a} C={mine_has_c}")

    # Настройки уведомлений из трея
    page.evaluate("window.__emit('desktop://open-settings', null)")
    page.wait_for_timeout(2000)
    page.screenshot(path=SHOTS + "/07-settings.png", full_page=True)
    check("из трея открываются настройки уведомлений", page.locator("text=Тихие часы по расписанию").count() >= 1)
    check("в настройках есть блок «Приложение»", page.locator("text=Запускать при входе в систему").count() >= 1)

    check("нет JS-ошибок на странице", not errors, "; ".join(errors)[:500])
    browser.close()

failed = [r for r in RESULTS if not r[1]]
print(f"\nИтого: {len(RESULTS) - len(failed)}/{len(RESULTS)} пройдено")
sys.exit(1 if failed else 0)
