"""Общие функции сквозных тестов: API Synapse и вход в Cinny."""
import json, os, sys, time, urllib.request, uuid
from playwright.sync_api import sync_playwright
HS="http://127.0.0.1:8008"; APP=os.environ.get("APP_URL","http://localhost:8080")
SHOTS=os.path.dirname(os.path.abspath(__file__))+"/shots"; os.makedirs(SHOTS,exist_ok=True)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def api(method,path,token=None,body=None):
    req=urllib.request.Request(HS+path,method=method); req.add_header("Content-Type","application/json")
    if token: req.add_header("Authorization","Bearer "+token)
    data=json.dumps(body).encode() if body is not None else None
    with opener.open(req,data) as r: return json.loads(r.read() or b"{}")
def login(u): return api("POST","/_matrix/client/v3/login",body={"type":"m.login.password","identifier":{"type":"m.id.user","user":u},"password":"pass123"})
def send(tok,room,content,etype="m.room.message"):
    return api("PUT",f"/_matrix/client/v3/rooms/{room}/send/{etype}/{uuid.uuid4().hex}",tok,content)["event_id"]
def text(b): return {"msgtype":"m.text","body":b}
def treply(root,b): 
    c=text(b); c["m.relates_to"]={"rel_type":"m.thread","event_id":root,"is_falling_back":True,"m.in_reply_to":{"event_id":root}}; return c
def open_ui(p, sess):
    ctx=p.chromium.launch(args=["--no-proxy-server"]).new_context(viewport={"width":1400,"height":900})
    page=ctx.new_page(); page.route('**/config.json',lambda route:route.fulfill(json={'homeserverList':[HS],'allowCustomHomeservers':True,'hashRouter':{'enabled':False,'basename':'/'}})); errs=[]; page.on("pageerror",lambda e:errs.append(str(e)))
    page.goto(APP+"/config.json")
    page.evaluate("""(s)=>{localStorage.setItem('cinny_access_token',s.access_token);localStorage.setItem('cinny_device_id',s.device_id);localStorage.setItem('cinny_user_id',s.user_id);localStorage.setItem('cinny_hs_base_url','http://127.0.0.1:8008');}""",sess)
    page.goto(APP+"/"); page.wait_for_timeout(5000); page.keyboard.press("Escape")
    return page, errs
