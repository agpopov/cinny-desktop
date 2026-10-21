"""Responsive UI and native mobile bridge checks against a disposable Synapse."""
import json, os, re, sys, uuid
from pathlib import Path
from playwright.sync_api import sync_playwright
from e2e_common import api, login, send, text, treply, APP

out=Path(os.environ.get('PLATFORM_SHOTS','/tmp/cinny-platform-ui-shots'));out.mkdir(parents=True,exist_ok=True)
run=uuid.uuid4().hex[:7]; users=['uialice'+run,'uibob'+run]
for user in users: api('POST','/_matrix/client/v3/register',body={'username':user,'password':'pass123','auth':{'type':'m.login.dummy'}})
a=login(users[0]);b=login(users[1])['access_token']
room=api('POST','/_matrix/client/v3/createRoom',b,{'name':'UI regression '+run,'preset':'private_chat','invite':[a['user_id']]})['room_id']
api('POST',f'/_matrix/client/v3/join/{room}',a['access_token'],{})
root=send(a['access_token'],room,text('UI THREAD ROOT'))
reply=send(b,room,treply(root,'UI THREAD REPLY'))
send(b,room,text('UI MESSAGE READY'))
checks=[]
def check(name, ok, detail=''):
    checks.append(bool(ok));print(('PASS ' if ok else 'FAIL ')+name+(' '+str(detail) if detail else ''),flush=True)

NATIVE=r'''
window.isTauri=true; window.__nativeCalls=[];window.__targets=[];
window.__permission='prompt-with-rationale';let cb=1;const callbacks={};
window.__TAURI_INTERNALS__={
 transformCallback(f){callbacks[cb]=f;return cb++;},unregisterCallback(id){delete callbacks[id];},convertFileSrc:p=>p,
 invoke:async(cmd,args)=>{
  window.__nativeCalls.push({cmd,args});
  if(cmd==='mobile_take_notification_target')return window.__targets.shift()||{};
  if(cmd==='native_notification_permission'){if(args.request)window.__permission='granted';return window.__permission;}
  if(cmd==='plugin:event|listen')return 1;
  if(cmd==='desktop_get_settings')return {platform:'macos',autostart:false,closeToTray:true,startMinimized:false};
  return null;
 }
};
'''
with sync_playwright() as p:
    for engine in (['chromium'] if os.environ.get('MOBILE_BRIDGE_ONLY') else ['chromium','webkit','firefox']):
        browser_type=getattr(p,engine)
        opts={}
        if engine=='chromium':opts['args']=['--no-proxy-server']
        if engine=='webkit' and os.environ.get('WEBKIT_EXECUTABLE'):opts['executable_path']=os.environ['WEBKIT_EXECUTABLE']
        browser=browser_type.launch(**opts)
        for width,height,scheme in ([] if os.environ.get('MOBILE_BRIDGE_ONLY') else [(1366,900,'light'),(768,1024,'dark'),(390,844,'light'),(320,568,'dark')]):
            label=f'{engine}-{width}-{scheme}';ctx=browser.new_context(viewport={'width':width,'height':height},color_scheme=scheme,locale='ru-RU')
            page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            session=login(users[0]);page.goto(APP+'/config.json')
            page.evaluate('s=>{localStorage.setItem("cinny_access_token",s.access_token);localStorage.setItem("cinny_device_id",s.device_id);localStorage.setItem("cinny_user_id",s.user_id);localStorage.setItem("cinny_hs_base_url","http://localhost:8008");}',session)
            try:
                page.goto(APP+'/home/'+room+'/');page.get_by_text('UI MESSAGE READY',exact=True).wait_for(timeout=35000);page.keyboard.press('Escape')
                editor=page.locator('[data-editable-name="RoomInput"]');editor.wait_for();box=editor.bounding_box()
                check(label+' composer in viewport',box and box['x']>=-1 and box['x']+box['width']<=width+1 and box['y']+box['height']<=height+1,box)
                check(label+' no page overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                sent='UI SEND '+label;editor.fill(sent);editor.press('Enter');page.get_by_text(sent,exact=True).last.wait_for(timeout=10000)
                check(label+' sends message',True)
                page.screenshot(path=str(out/(label+'.png')))
                page.get_by_role('button',name=re.compile('Открыть тред:')).first.click()
                page.get_by_text('UI THREAD REPLY',exact=True).last.wait_for(timeout=15000)
                thread_editor=page.locator('[contenteditable="true"]').last
                check(label+' opens thread',thread_editor.is_visible())
                if width<=390:
                    page.set_viewport_size({'width':width,'height':400});page.wait_for_timeout(500);box=thread_editor.bounding_box()
                    check(label+' input visible with reduced height',box and box['y']>=0 and box['y']+box['height']<=401,box)
                check(label+' no JavaScript errors',not errors,errors)
            except Exception as error:
                check(label+' scenario',False,str(error)[:350]);page.screenshot(path=str(out/(label+'-failure.png')))
            ctx.close()
        if engine=='chromium':
            ctx=browser.new_context(viewport={'width':390,'height':844},user_agent='Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0.0.0 Mobile Safari/537.36')
            page=ctx.new_page();page.add_init_script(NATIVE);errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            session=login(users[0]);page.goto(APP+'/config.json');page.evaluate('s=>{localStorage.setItem("cinny_access_token",s.access_token);localStorage.setItem("cinny_device_id",s.device_id);localStorage.setItem("cinny_user_id",s.user_id);localStorage.setItem("cinny_hs_base_url","http://localhost:8008");}',session)
            page.goto(APP+'/home/'+room+'/');page.get_by_text('UI MESSAGE READY',exact=True).wait_for(timeout=35000);page.wait_for_timeout(1500)
            check('mobile bridge requests notification permission',page.evaluate('window.__nativeCalls.some(c=>c.cmd==="native_notification_permission"&&c.args.request)'))
            page.evaluate('target=>{window.__targets.push(target);window.dispatchEvent(new Event("focus"));}',{'roomId':room,'threadId':root,'eventId':reply})
            page.get_by_text('UI THREAD REPLY',exact=True).last.wait_for(timeout=10000)
            check('mobile notification target opens thread',True)
            check('mobile consumes pending target',page.evaluate('window.__targets.length===0'))
            check('mobile bridge has no JavaScript errors',not errors,errors)
            page.screenshot(path=str(out/'android-bridge-thread.png'))
            page.goto(APP+'/home/');page.get_by_role('button',name='User Settings',exact=True).click()
            page.get_by_text('Notifications',exact=True).click()
            page.get_by_text('Системные уведомления',exact=True).wait_for()
            check('mobile settings hide desktop-only controls',page.get_by_text('Запускать при входе в систему',exact=True).count()==0)
            check('mobile settings explain background delivery status',page.get_by_text('Доставка при закрытом приложении ещё не подключена.',exact=False).count()>0)
            page.screenshot(path=str(out/'android-notification-settings.png'))
            page.evaluate('window.__permission="denied";window.dispatchEvent(new Event("focus"));')
            page.get_by_text('Уведомления заблокированы. Разрешите их в системных настройках Cinny.',exact=True).wait_for()
            check('mobile shows native denied permission',True)
            page.evaluate('window.__permission="prompt-with-rationale";window.dispatchEvent(new Event("focus"));')
            page.get_by_role('button',name='Включить',exact=True).click()
            check('mobile allows permission retry with rationale',page.evaluate('window.__permission==="granted"'))
            ctx.close()
            # Real packaged apps use hash routing. Keep an early tap until auth is ready.
            ctx=browser.new_context(viewport={'width':390,'height':844},user_agent='Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0.0.0 Mobile Safari/537.36')
            page=ctx.new_page();target={'roomId':room,'threadId':root,'eventId':reply}
            page.add_init_script(NATIVE.replace('window.__targets=[]','window.__targets='+json.dumps([target])))
            page.route('**/config.json',lambda route:route.fulfill(json={'homeserverList':['http://localhost:8008'],'allowCustomHomeservers':True,'hashRouter':{'enabled':True,'basename':'/'}}))
            session=login(users[0]);page.goto(APP+'/config.json');page.evaluate('s=>{localStorage.setItem("cinny_access_token",s.access_token);localStorage.setItem("cinny_device_id",s.device_id);localStorage.setItem("cinny_user_id",s.user_id);localStorage.setItem("cinny_hs_base_url","http://localhost:8008");}',session)
            page.goto(APP+'/');page.get_by_text('UI THREAD REPLY',exact=True).last.wait_for(timeout=35000)
            check('cold start notification reaches thread with hash router','#/home/' in page.url and room in page.url)
            check('cold start target consumed once',page.evaluate('window.__targets.length===0'))
            page.screenshot(path=str(out/'android-cold-start-target.png'));ctx.close()
        browser.close()
print(f'{sum(checks)}/{len(checks)} passed',flush=True)
sys.exit(0 if all(checks) else 1)
