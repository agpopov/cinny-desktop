"""Verify the actual Cinny-generated fallback in a local official Element."""
import os,json,re,time,urllib.request,urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright
opener=urllib.request.build_opener(urllib.request.ProxyHandler({})); HS=os.environ.get('HS_URL','http://127.0.0.1:8008');ELEMENT=os.environ.get('ELEMENT_URL','http://127.0.0.1:18082')
fixture=json.loads(Path(os.environ.get('MENTION_EVENT','/tmp/cinny-group-shots/chromium-1400-event.json')).read_text())
content=fixture.get('content',fixture)
user=content['m.mentions']['user_ids'][0];marker=content['body'].split(' @')[0]
room=fixture.get('room_id') or urllib.parse.unquote(re.findall(r'href="https://matrix.to/#/(![^"?]+)',content['formatted_body'])[-1])
def api(method,path,token=None,body=None):
 req=urllib.request.Request(HS+path,method=method,headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
 with opener.open(req,json.dumps(body).encode() if body else None,timeout=30) as r:return json.loads(r.read() or b'{}')
session=api('POST','/_matrix/client/v3/login',body={'type':'m.login.password','identifier':{'type':'m.id.user','user':user},'password':'pass123'})
notifications=api('GET','/_matrix/client/v3/notifications?only=highlight&limit=100',session['access_token'])['notifications']
assert any(marker in n.get('event',{}).get('content',{}).get('body','') and any(isinstance(a,dict) and a.get('set_tweak')=='highlight' and a.get('value',True) for a in n['actions']) for n in notifications),'Standard Matrix highlight notification absent'
print('PASS Synapse generates standard highlight notification for group recipient',flush=True)
with sync_playwright() as p:
 browser=p.chromium.launch(args=['--no-sandbox','--no-proxy-server']);page=browser.new_page(viewport={'width':1500,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto(ELEMENT+'/');page.get_by_text('Sign in',exact=True).click()
 page.locator('input[name=username]').fill(user);page.locator('input[name=password]').fill('pass123');page.get_by_role('button',name='Sign in',exact=True).click()
 page.wait_for_timeout(7000)
 print('AFTER LOGIN',page.locator('body').inner_text()[:3000],flush=True)
 page.goto(ELEMENT+'/#/room/'+urllib.parse.quote(room,safe=''));page.wait_for_timeout(6000)
 for _ in range(5):
  skip=page.get_by_role('button',name=re.compile('Skip|Later|Continue without|Not now|Dismiss',re.I))
  if skip.count(): skip.first.click();page.wait_for_timeout(700)
  else: break
 print('ROOM BODY',page.locator('body').inner_text()[-5000:],flush=True)
 page.screenshot(path='/tmp/cinny-group-shots/element-fallback.png',full_page=True)
 if '@Бэкенд (API)' in content['body']:assert '@Бэкенд (API)' in page.locator('body').inner_text(), 'Element did not show group label'
 assert '@QA Team' in page.locator('body').inner_text(), 'Element did not show second group label'
 assert user.split(':')[0][1:] in page.locator('body').inner_text(), 'Element did not show linked recipient'
 assert page.locator('[data-mention-group]').count()==0,'Unexpected Cinny-only rendering in Element'
 print('PASS official Element shows group labels with ordinary linked user mentions',flush=True)
 print('Element runtime errors',errors,flush=True)
 browser.close()
