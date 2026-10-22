"""Group mentions against a disposable Synapse. Never uses production accounts.
HS_URL=http://127.0.0.1:8008 APP_URL=http://127.0.0.1:18080 python3 tests/e2e/mention_groups.py
"""
import json, os, time, uuid, urllib.request, sys, re
from pathlib import Path
from playwright.sync_api import sync_playwright
HS=os.environ.get('HS_URL','http://127.0.0.1:8008'); APP=os.environ.get('APP_URL','http://127.0.0.1:18080')
OUT=Path(os.environ.get('TEST_OUTPUT','/tmp/cinny-group-shots')); OUT.mkdir(parents=True,exist_ok=True)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def api(method,path,token=None,body=None):
    request=urllib.request.Request(HS+path,method=method,headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
    with opener.open(request,json.dumps(body).encode() if body is not None else None,timeout=30) as result: return json.loads(result.read() or b'{}')
def state(token,room,type,key,content): return api('PUT',f'/_matrix/client/v3/rooms/{room}/state/{type}/{key}',token,content)
def send(token,room,content): return api('PUT',f'/_matrix/client/v3/rooms/{room}/send/m.room.message/{uuid.uuid4().hex}',token,content)
def messages(token,room): return api('GET',f'/_matrix/client/v3/rooms/{room}/messages?dir=b&limit=100',token)['chunk']
def wait_event(token,room,predicate):
    for _ in range(60):
        for event in messages(token,room):
            if predicate(event): return event
        time.sleep(.2)
    raise AssertionError('Expected message was not sent')
def check(name,condition=True):
    assert condition,name
    print('PASS '+name,flush=True)
run=uuid.uuid4().hex[:8]
sessions=[api('POST','/_matrix/client/v3/register',body={'username':name+run,'password':'pass123','auth':{'type':'m.login.dummy'}}) for name in ['groupalice','groupbob','groupcarol']]
a,b,c=sessions; token=a['access_token']; ids=[s['user_id'] for s in sessions]
space=api('POST','/_matrix/client/v3/createRoom',token,{'room_version':'10','name':'Mention space '+run,'creation_content':{'type':'m.space'},'invite':ids[1:]})['room_id']
room=api('POST','/_matrix/client/v3/createRoom',token,{'room_version':'10','name':'Group tests '+run,'invite':[b['user_id']]})['room_id']
for session in [b,c]: api('POST',f'/_matrix/client/v3/join/{space}',session['access_token'],{})
api('POST',f'/_matrix/client/v3/join/{room}',b['access_token'],{})
state(token,space,'m.space.child',room,{'via':['localhost']})
groups=[('backend','Бэкенд (API)',ids),('qa','QA Team',[b['user_id']]),('empty','Пустая группа',[])]
for key,name,users in groups: state(token,space,'in.cinny.mention_group',key,{'name':name,'color':'#5865f2','user_ids':users})
long_name='L'*80+' (QA)'
state(token,space,'in.cinny.mention_group','long',{'name':long_name,'color':'#5865f2','user_ids':[b['user_id']]})
send(token,room,{'msgtype':'m.text','body':'GROUP TEST READY'})

def open_page(browser,session,width=1400):
    context=browser.new_context(viewport={'width':width,'height':900 if width>600 else 844})
    page=context.new_page(); page.add_init_script('window.__editInputs=[];for(const type of ["beforeinput","input","compositionstart","compositionend"]){document.addEventListener(type,e=>window.__editInputs.push({type,inputType:e.inputType,data:e.data,cancelable:e.cancelable,selected:window.getSelection()?.toString()}),true)}'); errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.route('**/config.json',lambda route:route.fulfill(json={'homeserverList':[HS],'allowCustomHomeservers':True,'hashRouter':{'enabled':False,'basename':'/'}}))
    page.goto(APP+'/config.json')
    page.evaluate('(s)=>{localStorage.setItem("cinny_access_token",s.access_token);localStorage.setItem("cinny_device_id",s.device_id);localStorage.setItem("cinny_user_id",s.user_id);localStorage.setItem("cinny_hs_base_url",s.hs);}',{**session,'hs':HS})
    page.goto(APP+'/'+space+'/'+room+'/'); page.wait_for_timeout(6000); page.keyboard.press('Escape')
    # Earlier fixtures may be virtualized out after preceding browser runs.
    page.locator('[data-editable-name="RoomInput"]').wait_for(timeout=25000)
    page.wait_for_timeout(1200);page.keyboard.press('Escape')
    latest=page.get_by_role('button',name='Jump to Latest',exact=True)
    if latest.count(): latest.click();page.wait_for_timeout(700)
    return context,page,errors

def select_group(page,editor,name,typed=None,keyboard=False):
    editor.focus();page.keyboard.press('Control+End');page.keyboard.insert_text('@'+(typed or name));
    suggestion=page.get_by_role('button').filter(has=page.get_by_text('@'+name,exact=True)).last
    suggestion.wait_for(timeout=15000)
    if keyboard: page.keyboard.press('Tab')
    else: suggestion.click()
    page.wait_for_timeout(120)

with sync_playwright() as playwright:
  for engine in os.environ.get('BROWSERS','chromium').split(','):
    options={'args':['--no-sandbox','--no-proxy-server']} if engine=='chromium' else {}
    if engine=='webkit' and os.environ.get('WEBKIT_EXECUTABLE'): options['executable_path']=os.environ['WEBKIT_EXECUTABLE']
    browser=getattr(playwright,engine).launch(**options)
    for width in [1400,390]:
      context,page,errors=open_page(browser,a,width)
      label=f'{engine}-{width}'
      try:
        editor=page.locator('[data-slate-editor="true"]').first
        marker='GROUP COMBO '+label+' '+run
        editor.click();page.keyboard.insert_text(marker+' @@ mail@QA.test ')
        for _,name,_ in groups: select_group(page,editor,name,keyboard=True)
        editor.focus();page.keyboard.press('Control+End');page.keyboard.insert_text('@'+b['user_id'].split(':')[0][1:]);
        page.locator('button[data-mention-user="'+b['user_id']+'"]').wait_for(timeout=15000)
        page.keyboard.press('Tab');page.wait_for_timeout(120)
        editor.focus();page.keyboard.press('Control+End');page.keyboard.insert_text('@room');page.get_by_role('button').filter(has=page.get_by_text('@room',exact=True)).last.wait_for(timeout=15000);page.keyboard.press('Tab')
        page.keyboard.insert_text(' extra ')
        page.keyboard.press('Enter')
        event=wait_event(token,room,lambda e:marker in e.get('content',{}).get('body',''))
        latest=page.get_by_role('button',name='Jump to Latest',exact=True)
        if latest.count(): latest.click();page.wait_for_timeout(700)
        content=event['content'];mentions=content.get('m.mentions',{})
        (OUT/(label+'-event.json')).write_text(json.dumps(content,ensure_ascii=False,indent=2))
        print('MENTIONS',mentions,flush=True)
        check(label+' mixed groups+room deduplicate and exclude absent/self',mentions.get('user_ids')==[b['user_id']] and mentions.get('room') is True)
        check(label+' fallback contains group names and user links',all('@'+name in content.get('formatted_body','') for _,name,_ in groups) and 'https://matrix.to/' in content['formatted_body'])
        check(label+' snapshot includes only current channel members',c['user_id'] not in content['formatted_body'])
        check(label+' three compact pills',page.locator('[data-mention-group]').count()>=3)
        check(label+' no script errors',not errors)
        page.screenshot(path=str(OUT/(label+'-mentions.png')))
        # Both forms of edits must avoid re-notifying existing recipients.
        row=page.locator('[data-message-id="'+event['event_id']+'"]')
        page.mouse.move(0,0);row.hover();row.get_by_role('button',name='Edit Message',exact=True).click()
        edit=row.locator('[data-slate-editor="true"]');page.wait_for_function('(el)=>el.textContent.includes("GROUP COMBO")',arg=edit.element_handle());edit.focus();page.keyboard.press('Control+End');page.keyboard.insert_text(' исправлено '+label)
        row.get_by_role('button',name='Save',exact=True).click()
        revised=wait_event(token,room,lambda e:e.get('content',{}).get('m.relates_to',{}).get('event_id')==event['event_id'] and 'исправлено '+label in e['content'].get('body',''))['content']
        check(label+' editing text does not re-ping',revised['m.mentions']=={} and revised['m.new_content']['m.mentions'].get('user_ids')==[b['user_id']])
        page.mouse.move(0,0);row.hover();row.get_by_role('button',name='Edit Message',exact=True).click()
        edit=row.locator('[data-slate-editor="true"]');page.wait_for_function('(el)=>el.textContent.includes("GROUP COMBO")',arg=edit.element_handle());edit.focus();page.keyboard.press('Control+A');page.wait_for_timeout(100);page.keyboard.insert_text('REMOVED ALL MENTIONS '+label)
        row.get_by_role('button',name='Save',exact=True).click()
        removed=wait_event(token,room,lambda e:e.get('content',{}).get('m.relates_to',{}).get('event_id')==event['event_id'] and 'REMOVED ALL MENTIONS '+label in e['content'].get('body',''))['content']
        check(label+' removing mentions clears resolved and delta lists',removed['m.mentions']=={} and removed['m.new_content']['m.mentions']=={})
        editor=page.locator('[data-editable-name="RoomInput"]')
        empty_marker='EMPTY '+label+' '+run
        editor.click();page.keyboard.insert_text(empty_marker+' ');select_group(page,editor,'Пустая группа');page.keyboard.press('Enter')
        empty=wait_event(token,room,lambda e:empty_marker in e.get('content',{}).get('body',''))['content']
        check(label+' empty group remains visible without notification',empty['m.mentions']=={} and '@Пустая группа' in empty['formatted_body'])
        code_marker='CODE BLOCK '+label+' '+run
        editor.focus();page.keyboard.insert_text(code_marker+' ');select_group(page,editor,'QA Team')
        page.keyboard.press('Control+A');page.keyboard.press('Control+;');page.keyboard.press('Control+Enter')
        coded=wait_event(token,room,lambda e:code_marker in e.get('content',{}).get('body',''))['content']
        check(label+' converting group to a code block sends inert code',coded['m.mentions']=={} and '<pre>' in coded['formatted_body'] and 'data-cinny' not in coded['formatted_body'])
        inline_marker='INLINE CODE '+label+' '+run
        editor.focus();page.keyboard.insert_text(inline_marker+' ` ');select_group(page,editor,'QA Team');page.keyboard.insert_text('`');page.keyboard.press('Enter')
        inline=wait_event(token,room,lambda e:inline_marker in e.get('content',{}).get('body',''))['content']
        check(label+' backticks around group do not notify',inline['m.mentions']=={} and '<code' in inline['formatted_body'])
        long_marker='LONG GROUP '+label+' '+run
        editor.focus();page.keyboard.insert_text(long_marker+' ');select_group(page,editor,long_name);page.keyboard.press('Enter')
        long_event=wait_event(token,room,lambda e:long_marker in e.get('content',{}).get('body',''))['content']
        check(label+' long group name resolves normally',long_event['m.mentions']=={'user_ids':[b['user_id']]})
        check(label+' long group does not overflow viewport',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
        editor.click();page.keyboard.insert_text('LITERAL '+label+' @несуществующая группа');page.wait_for_timeout(500);page.keyboard.press('Enter')
        literal=wait_event(token,room,lambda e:'LITERAL '+label in e.get('content',{}).get('body',''))['content']
        check(label+' unmatched @ with spaces remains literal',literal['m.mentions']=={})
        # A group removed after insertion fails visibly and keeps the draft.
        editor.click();page.keyboard.insert_text('DELETED '+label+' ');select_group(page,editor,'QA Team')
        state(token,space,'in.cinny.mention_group','qa',{});page.wait_for_timeout(500);page.keyboard.press('Enter')
        page.get_by_role('alert').filter(has_text='удалена').wait_for(timeout=10000)
        check(label+' deletion during compose retains draft','DELETED '+label in editor.inner_text())
        editor.click();page.keyboard.press('Control+A');page.wait_for_timeout(100);page.keyboard.press('Backspace')
        state(token,space,'in.cinny.mention_group','qa',{'name':'QA Team','color':'#5865f2','user_ids':[b['user_id']]})
        check(label+' no errors after edge cases',not errors)
        if width==1400:
          # Shared groups and administrative CRUD through the actual settings UI.
          page.locator('button[data-id="'+space+'"]').click(button='right')
          page.get_by_text('Space Settings',exact=True).click();page.get_by_role('button',name='Группы упоминаний',exact=True).click()
          page.get_by_role('button',name='Создать группу',exact=True).click()
          new_name='QA [авто] (Web) '+engine+' '+run
          page.get_by_label('Название группы',exact=True).fill(new_name)
          page.get_by_role('button',name='Сохранить группу',exact=True).click()
          page.get_by_role('button',name='@'+new_name+' · 0',exact=True).wait_for(timeout=10000)
          check(label+' creates an empty shared group with Unicode brackets and spaces')
          page.get_by_role('button',name='@'+new_name+' · 0',exact=True).click()
          page.get_by_role('checkbox',name=re.compile(re.escape(b['user_id']))).check()
          page.get_by_role('button',name='Сохранить группу',exact=True).click()
          page.get_by_role('button',name='@'+new_name+' · 1',exact=True).wait_for(timeout=10000)
          check(label+' saves singleton membership')
          all_state=api('GET',f'/_matrix/client/v3/rooms/{space}/state',token)
          created=next(e for e in all_state if e['type']=='in.cinny.mention_group' and e['content'].get('name')==new_name)
          check(label+' server stores shared membership',created['content']['user_ids']==[b['user_id']])
          # Another administrator changes it while the form is open.
          page.get_by_role('button',name='@'+new_name+' · 1',exact=True).click()
          state(token,space,'in.cinny.mention_group',created['state_key'],{'name':new_name,'color':'#123456','user_ids':[]})
          page.get_by_role('button',name='Сохранить группу',exact=True).click()
          page.get_by_role('alert').filter(has_text='другой участник').wait_for(timeout=10000)
          check(label+' concurrent edits are not silently overwritten')
          page.get_by_role('button',name='Закрыть',exact=True).click()
          page.get_by_role('button',name='@'+new_name+' · 0',exact=True).click()
          page.get_by_role('button',name='Удалить группу',exact=True).click();page.get_by_role('button',name='Подтвердить удаление',exact=True).click()
          page.get_by_role('button',name='@'+new_name+' · 0',exact=True).wait_for(state='hidden',timeout=10000)
          check(label+' deletion clears group from shared state')
          page.get_by_role('button',name='Закрыть группы',exact=True).click()
          receiver_context,receiver,receiver_errors=open_page(browser,b)
          receiver.locator('button[data-id="'+space+'"]').click(button='right');receiver.get_by_text('Space Settings',exact=True).click();receiver.get_by_role('button',name='Группы упоминаний',exact=True).click()
          check(label+' ordinary member sees groups without edit controls',receiver.get_by_role('button',name='Создать группу',exact=True).count()==0 and receiver.get_by_role('button',name='@QA Team · 1',exact=True).count()==1)
          receiver.get_by_role('button',name='@QA Team · 1',exact=True).click()
          check(label+' ordinary member sees actual membership',receiver.get_by_role('checkbox',name=re.compile(re.escape(b['user_id']))).is_checked())
          check(label+' ordinary member has no save or delete buttons',receiver.get_by_role('button',name='Сохранить группу',exact=True).count()==0 and receiver.get_by_role('button',name='Удалить группу',exact=True).count()==0)
          check(label+' second client has no script errors',not receiver_errors)
          receiver_context.close()

      except Exception:
        print('BODY',page.locator('body').inner_text()[-4500:],flush=True)
        print('ERRORS',errors,flush=True)
        print('INPUT EVENTS',page.evaluate('window.__editInputs.slice(-15)'),flush=True)
        page.screenshot(path=str(OUT/(label+'-failure.png')))
        raise
      finally: context.close()
    browser.close()
print('All group mention browser scenarios passed',flush=True)
