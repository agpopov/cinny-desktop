"""Сквозная проверка: опросы (формат Element) и треды в списке комнат против локального Synapse."""
from e2e_common import *
import re
RID=uuid.uuid4().hex[:6]; UA="fa"+RID; UB="fb"+RID
for u in (UA,UB): api("POST","/_matrix/client/v3/register",body={"username":u,"password":"pass123","auth":{"type":"m.login.dummy"}})
bob=login(UB)["access_token"]; alice=login(UA); A=alice["access_token"]
R=[]
def check(n,ok,d=""):
    R.append(ok); print(("PASS " if ok else "FAIL ")+n+(" — "+str(d) if d else ""),flush=True)
room=api("POST","/_matrix/client/v3/createRoom",bob,{"name":"Фичи","preset":"private_chat","invite":[f"@{UA}:localhost"]})["room_id"]
api("POST",f"/_matrix/client/v3/join/{room}",A,{})
rootA=send(bob,room,text("КОРЕНЬ-А")); send(bob,room,treply(rootA,"ответ-А1"))
rootB=send(bob,room,text("КОРЕНЬ-Б")); send(bob,room,treply(rootB,"ответ-Б1")); send(bob,room,treply(rootB,"ответ-Б2"))
# опрос в формате Element от Боба
pq={"org.matrix.msc1767.text":"Что на обед?\n1. Суп\n2. Пицца","org.matrix.msc3381.poll.start":{"question":{"org.matrix.msc1767.text":"Что на обед?","body":"Что на обед?","msgtype":"m.text"},"kind":"org.matrix.msc3381.poll.disclosed","max_selections":1,"answers":[{"id":"soup","org.matrix.msc1767.text":"Суп"},{"id":"pizza","org.matrix.msc1767.text":"Пицца"}]}}
poll=send(bob,room,pq,"org.matrix.msc3381.poll.start")
def vote(tok,ids,pid=poll):
    send(tok,room,{"m.relates_to":{"rel_type":"m.reference","event_id":pid},"org.matrix.msc3381.poll.response":{"answers":ids}},"org.matrix.msc3381.poll.response")
vote(bob,["soup"])
with sync_playwright() as p:
    page,errs=open_ui(p,alice)
    page.goto(APP+"/home/"+room+"/"); page.wait_for_selector("text=Что на обед?",timeout=30000); page.wait_for_timeout(2500)
    page.screenshot(path=SHOTS+"/f1-room.png")
    # --- опрос ---
    poll_el=page.locator(f"[data-message-id='{poll}']")
    t=poll_el.inner_text()
    check("опрос от Element отрисован с вариантами", "Суп" in t and "Пицца" in t, t.replace("\n"," | "))
    check("видны текущие голоса (1 · 100%)", "1 · 100%" in t, t.replace("\n"," | "))
    poll_el.get_by_role("radio",name=re.compile("Пицца")).click(); page.wait_for_timeout(2500)
    t=poll_el.inner_text()
    check("после голосования 2 голоса 50/50", t.count("1 · 50%")==2 and "2 голоса" in t, t.replace("\n"," | "))
    ev=[e for e in api("GET",f"/_matrix/client/v3/rooms/{room}/messages?dir=b&limit=10",A)["chunk"] if e["type"]=="org.matrix.msc3381.poll.response" and e["sender"]==f"@{UA}:localhost"]
    check("голос ушёл в формате MSC3381", ev and ev[0]["content"]["org.matrix.msc3381.poll.response"]["answers"]==["pizza"] and ev[0]["content"]["m.relates_to"]["rel_type"]=="m.reference", json.dumps(ev[:1]))
    vote(bob,["pizza"]); page.wait_for_timeout(2000)
    check("живое обновление: Боб переголосовал", "2 · 100%" in poll_el.inner_text() or "100%" in poll_el.inner_text(), poll_el.inner_text().replace("\n"," | "))
    # создание опроса из UI
    page.get_by_role("button",name="Прикрепить").click(); page.get_by_text("Опрос",exact=True).last.click(); page.wait_for_timeout(500)
    page.screenshot(path=SHOTS+"/f2-poll-dialog.png")
    page.get_by_placeholder("О чём спросить?").fill("Кто идёт?")
    page.get_by_placeholder("Вариант 1").fill("Я"); page.get_by_placeholder("Вариант 2").fill("Не я")
    page.get_by_role("button",name="Добавить вариант").click(); page.get_by_placeholder("Вариант 3").fill("Может быть")
    page.get_by_text("Можно выбрать несколько ответов").click()
    page.get_by_role("button",name="Отправить опрос").click(); page.wait_for_selector("text=Кто идёт?",timeout=10000); page.wait_for_timeout(1500)
    pe=[e for e in api("GET",f"/_matrix/client/v3/rooms/{room}/messages?dir=b&limit=10",B if False else bob)["chunk"] if e["type"]=="org.matrix.msc3381.poll.start" and e["sender"]==f"@{UA}:localhost"]
    c=pe[0]["content"] if pe else {}
    st=c.get("org.matrix.msc3381.poll.start",{})
    check("опрос из UI: формат Element, 3 ответа, multi", len(st.get("answers",[]))==3 and st.get("max_selections")==3 and st.get("kind")=="org.matrix.msc3381.poll.disclosed" and "Кто идёт?" in c.get("org.matrix.msc1767.text",""), json.dumps(c,ensure_ascii=False)[:300])
    pid2=pe[0]["event_id"] if pe else None
    print(json.dumps(c,ensure_ascii=False)); page.screenshot(path=SHOTS+"/f2b.png")
    el2=page.locator(f"[data-message-id='{pid2}']")
    el2.get_by_role("checkbox",name=re.compile("Я")).first.click(); el2.get_by_role("checkbox",name=re.compile("Может")).click(); page.wait_for_timeout(2000)
    check("несколько выбранных вариантов", "2 · 100%" in el2.inner_text() or el2.get_by_role("checkbox",checked=True).count()==2, el2.inner_text().replace("\n"," | "))
    el2.get_by_role("button",name="Завершить опрос").click(); el2.get_by_role("button",name="Завершить",exact=True).click(); page.wait_for_timeout(2000)
    end=[e for e in api("GET",f"/_matrix/client/v3/rooms/{room}/messages?dir=b&limit=10",bob)["chunk"] if e["type"]=="org.matrix.msc3381.poll.end"]
    check("завершение опроса отправлено", len(end)==1)
    check("после завершения показано «Опрос завершён»", "Опрос завершён" in el2.inner_text(), el2.inner_text().replace("\n"," | "))
    vote(bob,["Я" if False else pe[0]["content"]["org.matrix.msc3381.poll.start"]["answers"][1]["id"]],pid2); page.wait_for_timeout(1500)
    check("голос после завершения не учитывается", "2 голоса" not in el2.inner_text() and "Не я" in el2.inner_text(), el2.inner_text().replace("\n"," | "))
    page.screenshot(path=SHOTS+"/f3-poll.png")
    # --- треды слева ---
    nav=page.locator("[aria-label='Активные треды']").filter(has_text='КОРЕНЬ-Б')
    page.wait_for_timeout(1500); page.screenshot(path=SHOTS+"/f4-nav.png")
    check("в списке слева два активных треда", nav.count()==1 and "КОРЕНЬ-А" in nav.inner_text() and "КОРЕНЬ-Б" in nav.inner_text(), nav.inner_text() if nav.count() else "нет")
    nav.get_by_role("button",name=re.compile("КОРЕНЬ-Б")).click(); page.wait_for_selector("text=ответ-Б2",timeout=10000); page.wait_for_timeout(1500)
    page.screenshot(path=SHOTS+"/f5-main-thread.png")
    pos=page.evaluate("""()=>['КОРЕНЬ-Б','ответ-Б1','ответ-Б2'].map(w=>{const e=[...document.querySelectorAll('[data-message-id]')].filter(x=>x.innerText.includes(w)).pop();return e?Math.round(e.getBoundingClientRect().top):null})""")
    check("тред открыт на всю область, корень первый", all(pos) and pos[0]<pos[1]<pos[2], pos)
    check("общая лента скрыта", page.locator("text=Что на обед?").count()==0)
    page.get_by_role("button",name=re.compile("Свернуть в боковую")).click() if page.get_by_role("button",name=re.compile("Свернуть в боковую")).count() else page.locator("button:has(svg)").first
    page.wait_for_timeout(800)
    page.screenshot(path=SHOTS+"/f6-side.png")
    check("после сворачивания лента комнаты и панель видны вместе", page.locator("text=Что на обед?").count()>=1 and page.locator("text=ответ-Б2").count()>=1)
    nav.get_by_role("button",name=re.compile("КОРЕНЬ-А")).click(); page.wait_for_selector("text=ответ-А1",timeout=10000); page.wait_for_timeout(1000)
    check("переключение на другой тред из списка", page.locator("text=ответ-А1").count()>=1 and page.locator("text=Что на обед?").count()==0)
    page.locator("a:has-text('Фичи')").first.click(); page.wait_for_timeout(1500)
    check("клик по комнате возвращает общую ленту", page.locator("text=Что на обед?").count()>=1)
    # новый тред появляется живьём
    rootC=send(bob,room,text("КОРЕНЬ-В")); page.wait_for_timeout(800); send(bob,room,treply(rootC,"ответ-В1")); page.wait_for_timeout(2500)
    check("новый тред добавился в список без перезагрузки", "КОРЕНЬ-В" in nav.inner_text(), nav.inner_text())
    check("нет JS-ошибок", not errs, errs)
print(f"Итого: {sum(R)}/{len(R)} пройдено")
sys.exit(0 if all(R) else 1)
