"""Group-only notifications, two simultaneous editors and encrypted delivery.

Requires a disposable Synapse at HS_URL. Accounts are generated per run.
"""
import json, os, time, uuid, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

HS=os.environ.get('HS_URL','http://127.0.0.1:8008')
APP=os.environ.get('APP_URL','http://127.0.0.1:18084')
OUT=Path(os.environ.get('TEST_OUTPUT','/tmp/cinny-group-shots'));OUT.mkdir(parents=True,exist_ok=True)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def api(method,path,token=None,body=None):
    req=urllib.request.Request(HS+path,method=method,headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
    with opener.open(req,json.dumps(body).encode() if body is not None else None,timeout=30) as response:return json.loads(response.read() or b'{}')
def state(token,room,kind,key,content):return api('PUT',f'/_matrix/client/v3/rooms/{room}/state/{kind}/{key}',token,content)
def send(token,room,content):return api('PUT',f'/_matrix/client/v3/rooms/{room}/send/m.room.message/{uuid.uuid4().hex}',token,content)['event_id']
def events(token,room):return api('GET',f'/_matrix/client/v3/rooms/{room}/messages?dir=b&limit=100',token)['chunk']
def wait_event(token,room,predicate):
    for _ in range(100):
        match=next((event for event in events(token,room) if predicate(event)),None)
        if match:return match
        time.sleep(.2)
    raise AssertionError('Expected event not sent')
def check(label,condition=True):
    assert condition,label
    print('PASS '+label,flush=True)

run=uuid.uuid4().hex[:8]
a,b=[api('POST','/_matrix/client/v3/register',body={'username':name+run,'password':'pass123','auth':{'type':'m.login.dummy'}}) for name in ['cryptogroupa','cryptogroupb']]
token=a['access_token']
space=api('POST','/_matrix/client/v3/createRoom',token,{'name':'Crypto group space '+run,'creation_content':{'type':'m.space'},'invite':[b['user_id']]})['room_id']
api('POST',f'/_matrix/client/v3/join/{space}',b['access_token'],{})
plain=api('POST','/_matrix/client/v3/createRoom',token,{'name':'Group threads '+run,'invite':[b['user_id']]})['room_id']
encrypted=api('POST','/_matrix/client/v3/createRoom',token,{'name':'Encrypted groups '+run,'invite':[b['user_id']],'initial_state':[{'type':'m.room.encryption','state_key':'','content':{'algorithm':'m.megolm.v1.aes-sha2'}}]})['room_id']
for room in [plain,encrypted]:
    api('POST',f'/_matrix/client/v3/join/{room}',b['access_token'],{})
    state(token,space,'m.space.child',room,{'via':['localhost']})
state(token,space,'in.cinny.mention_group','qa',{'name':'QA Team','color':'#5865f2','user_ids':[b['user_id']]})
root=send(token,plain,{'msgtype':'m.text','body':'GROUP THREAD ROOT '+run})

def open_page(browser,session,room):
    context=browser.new_context(viewport={'width':1400,'height':900})
    page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.route('**/config.json',lambda route:route.fulfill(json={'homeserverList':[HS],'allowCustomHomeservers':True,'hashRouter':{'enabled':False,'basename':'/'}}))
    page.goto(APP+'/config.json');page.evaluate('(s)=>{localStorage.setItem("cinny_access_token",s.access_token);localStorage.setItem("cinny_device_id",s.device_id);localStorage.setItem("cinny_user_id",s.user_id);localStorage.setItem("cinny_hs_base_url",s.hs);}',{**session,'hs':HS})
    page.goto(APP+'/'+space+'/'+room+'/');page.locator('[data-editable-name="RoomInput"]').wait_for(timeout=40000)
    page.wait_for_timeout(4500);page.keyboard.press('Escape')
    return context,page,errors
def group(page,editor,marker):
    editor.focus();page.keyboard.insert_text(marker+' @QA Team')
    page.get_by_role('button').filter(has=page.get_by_text('@QA Team',exact=True)).last.wait_for(timeout=15000)
    page.keyboard.press('Tab');page.wait_for_timeout(150)
def edit(page,row,suffix):
    page.mouse.move(0,0);row.hover();row.get_by_role('button',name='Edit Message',exact=True).click()
    editor=row.locator('[data-slate-editor="true"]');page.wait_for_function('(el)=>el.textContent.includes("GROUP")',arg=editor.element_handle())
    page.wait_for_timeout(200);editor.focus();page.keyboard.press('Control+End');page.keyboard.insert_text(suffix)
    page.wait_for_function('(args)=>args.el.textContent.includes(args.suffix)',arg={'el':editor.element_handle(),'suffix':suffix})
    row.get_by_role('button',name='Save',exact=True).click()

with sync_playwright() as p:
  for engine in os.environ.get('BROWSERS','chromium').split(','):
    options={'args':['--no-sandbox','--no-proxy-server']} if engine=='chromium' else {}
    if engine=='webkit' and os.environ.get('WEBKIT_EXECUTABLE'):options['executable_path']=os.environ['WEBKIT_EXECUTABLE']
    browser=getattr(p,engine).launch(**options)
    context,page,errors=open_page(browser,a,plain)
    try:
      row=page.locator('[data-message-id="'+root+'"]');row.hover()
      summary=row.get_by_role('button').filter(has_text='ответ')
      if summary.count():summary.first.click()
      else:row.locator('button[data-event-id="'+root+'"]').nth(1).click()
      thread=page.locator('[data-editable-name="ThreadInput"]');thread.wait_for()
      main=page.locator('[data-editable-name="RoomInput"]')
      group(page,main,'GROUP MAIN DRAFT '+engine)
      group(page,thread,'GROUP THREAD '+engine+' '+run)
      check(engine+' selecting a group in thread does not consume main draft','GROUP MAIN DRAFT' in main.inner_text())
      thread.press('Enter')
      event=wait_event(token,plain,lambda event:'GROUP THREAD '+engine+' '+run in event.get('content',{}).get('body',''))
      content=event['content']
      check(engine+' group-only thread has standard mentions and correct root',content['m.mentions']=={'user_ids':[b['user_id']]} and content['m.relates_to']['rel_type']=='m.thread' and content['m.relates_to']['event_id']==root)
      thread_row=page.locator('[data-message-id="'+event['event_id']+'"]');thread_row.wait_for()
      edit(page,thread_row,' EDITED')
      changed=wait_event(token,plain,lambda e:e.get('content',{}).get('m.relates_to',{}).get('event_id')==event['event_id'] and 'EDITED' in e['content'].get('body',''))['content']
      check(engine+' thread edit preserves group snapshot without re-ping',changed['m.mentions']=={} and changed['m.new_content']['m.mentions']=={'user_ids':[b['user_id']]})
      main.focus();page.keyboard.press('Control+End');page.keyboard.press('Enter')
      main_event=wait_event(token,plain,lambda e:'GROUP MAIN DRAFT '+engine in e.get('content',{}).get('body',''))
      check(engine+' main draft sends independently of thread',main_event['content'].get('m.relates_to',{}).get('rel_type')!='m.thread')
      notes=api('GET','/_matrix/client/v3/notifications?only=highlight&limit=100',b['access_token'])['notifications']
      check(engine+' recipient gets group-only highlight without @room',any(n['event'].get('event_id')==main_event['event_id'] for n in notes))
      (OUT/('group-only-'+engine+'.json')).write_text(json.dumps({'room_id':plain,**main_event},ensure_ascii=False,indent=2))
      check(engine+' simultaneous editors and edits have no runtime errors',not errors)
    except Exception:
      print('BODY',page.locator('body').inner_text()[-5000:],flush=True);print('ERRORS',errors,flush=True);page.screenshot(path=str(OUT/('threads-'+engine+'-failure.png')));raise
    finally:context.close()
    if engine=='chromium':
      contexts=[];pages=[];error_lists=[]
      for session in [a,b]:
        ctx,pg,errs=open_page(browser,session,encrypted);contexts.append(ctx);pages.append(pg);error_lists.append(errs)
      try:
        page=pages[0];editor=page.locator('[data-editable-name="RoomInput"]');marker='ENCRYPTED GROUP '+run
        group(page,editor,marker);editor.press('Enter')
        pages[1].get_by_text(marker,exact=False).last.wait_for(timeout=40000)
        check('encrypted group decrypts on a second device as compact pill',pages[1].locator('[data-mention-group]').count()==1)
        stored=events(token,encrypted)
        check('server stores ciphertext without group labels or recipient snapshots',any(e['type']=='m.room.encrypted' for e in stored) and not any(marker in json.dumps(e) or 'QA Team' in json.dumps(e.get('content',{})) for e in stored))
        # An edit must continue to encrypt its full snapshot and survive reload.
        first=page.locator('[data-message-id]').filter(has_text=marker).last
        edit(page,first,' ENCRYPTED EDIT')
        pages[1].get_by_text('ENCRYPTED EDIT',exact=False).last.wait_for(timeout=30000)
        pages[1].reload();pages[1].get_by_text('ENCRYPTED EDIT',exact=False).last.wait_for(timeout=40000)
        check('encrypted edited history retains compact group after reload',pages[1].locator('[data-mention-group]').count()==1)
        check('encrypted two-device exchange has no runtime errors',not any(error_lists))
      except Exception:
        print('CRYPTO BODY',pages[0].locator('body').inner_text()[-5000:],flush=True);print('ERRORS',error_lists,flush=True);pages[0].screenshot(path=str(OUT/'crypto-failure.png'));raise
      finally:
        for ctx in contexts:ctx.close()
    browser.close()
print('All thread and encryption group scenarios passed',flush=True)
