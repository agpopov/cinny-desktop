import dbus, dbus.service, dbus.mainloop.glib
from gi.repository import GLib
import threading, subprocess, os, time, urllib.request, json

# Run inside dbus-run-session; dependencies: python3-dbus, python3-gi, Xvfb, WebKitWebDriver.
# CINNY_BINARY must point to the packaged application being tested.
os.environ["TAURI_WEBVIEW_AUTOMATION"] = "true"

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus=dbus.SessionBus(); name=dbus.service.BusName('org.freedesktop.Notifications',bus)
class Notifications(dbus.service.Object):
    def __init__(self): super().__init__(bus,'/org/freedesktop/Notifications'); self.calls=[]
    @dbus.service.method('org.freedesktop.Notifications',in_signature='',out_signature='as')
    def GetCapabilities(self): return ['body','actions','body-markup']
    @dbus.service.method('org.freedesktop.Notifications',in_signature='',out_signature='ssss')
    def GetServerInformation(self): return ('Cinny Test','Test','1','1.2')
    @dbus.service.method('org.freedesktop.Notifications',in_signature='susssasa{sv}i',out_signature='u')
    def Notify(self,app,replaces,icon,summary,body,actions,hints,timeout): self.calls.append((app,replaces,icon,summary,body,actions,hints,timeout)); return 1
    @dbus.service.method('org.freedesktop.Notifications',in_signature='u',out_signature='')
    def CloseNotification(self,id): pass
    @dbus.service.signal('org.freedesktop.Notifications',signature='us')
    def ActionInvoked(self,id,action): pass
server=Notifications();loop=GLib.MainLoop();threading.Thread(target=loop.run,daemon=True).start()
procs=[]
def req(path,body=None,method=None):
    request=urllib.request.Request('http://127.0.0.1:4456'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'},method=method)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request,timeout=60) as response:return json.load(response)['value']
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode()) from e
try:
    procs.append(subprocess.Popen([os.environ.get('XVFB_BINARY','Xvfb'),':98','-screen','0','1280x900x24'],stdout=open('/tmp/cinny-native-xvfb.log','w'),stderr=subprocess.STDOUT));time.sleep(1)
    procs.append(subprocess.Popen([os.environ.get('WEBKIT_DRIVER','WebKitWebDriver'),'--port=4456','--host=127.0.0.1'],stdout=open('/tmp/cinny-native-driver.log','w'),stderr=subprocess.STDOUT));time.sleep(1)
    session=req('/session',{'capabilities':{'alwaysMatch':{'webkitgtk:browserOptions':{'binary':os.environ['CINNY_BINARY']}}}})['sessionId']; path='/session/'+session
    req(path+'/timeouts',{'script':15000})
    def js(script,args=[]):return req(path+'/execute/sync',{'script':script,'args':args})
    for _ in range(30):
        if js('return !!window.__TAURI_INTERNALS__'):break
        time.sleep(1)
    js("window.__clicked=null;return window.__TAURI_INTERNALS__.invoke('plugin:event|listen',{event:'desktop://notification-click',target:{kind:'Any'},handler:window.__TAURI_INTERNALS__.transformCallback(e=>window.__clicked=e.payload)});")
    target={'roomId':'!linux:localhost','eventId':'$event','threadId':'$root'}
    result=req(path+'/execute/async',{'script':"const done=arguments[arguments.length-1];window.__TAURI_INTERNALS__.invoke('desktop_notify',{payload:{title:'Native Linux test',body:'<b>message & text</b>',target:arguments[0]}}).then(()=>done('ok'),e=>done(String(e)))",'args':[target]})
    assert result=='ok',result
    for _ in range(30):
        if server.calls:break
        time.sleep(.2)
    assert server.calls,'No native D-Bus notification'
    call=server.calls[-1];assert call[3]=='Native Linux test';assert '&lt;b&gt;message &amp; text&lt;/b&gt;'==call[4],call[4]
    assert 'default' in call[5];assert bool(call[6].get('suppress-sound'))
    time.sleep(1);server.ActionInvoked(1,'default')
    for _ in range(30):
        clicked=js('return window.__clicked')
        if clicked:break
        time.sleep(.2)
    assert clicked==target,clicked
    print('PASS native Linux packaged app: actual D-Bus notification, escaped text, silent flag, default action and exact room/thread/event target',flush=True)
    if os.environ.get('MENTION_TEST_HS'):
        from e2e_common import api
        import uuid, base64
        run=uuid.uuid4().hex[:8]
        a,b=[api('POST','/_matrix/client/v3/register',body={'username':u+run,'password':'pass123','auth':{'type':'m.login.dummy'}}) for u in ['nativegroupa','nativegroupb']]
        space=api('POST','/_matrix/client/v3/createRoom',a['access_token'],{'name':'Native groups '+run,'creation_content':{'type':'m.space'},'invite':[b['user_id']]})['room_id']
        room=api('POST','/_matrix/client/v3/createRoom',a['access_token'],{'name':'Native group channel '+run,'invite':[b['user_id']]})['room_id']
        for target_room in [space,room]:api('POST','/_matrix/client/v3/join/'+target_room,b['access_token'],{})
        api('PUT',f'/_matrix/client/v3/rooms/{space}/state/m.space.child/{room}',a['access_token'],{'via':['localhost']})
        api('PUT',f'/_matrix/client/v3/rooms/{space}/state/in.cinny.mention_group/qa',a['access_token'],{'name':'QA Team','color':'#5865f2','user_ids':[b['user_id']]})
        js("const s=arguments[0];localStorage.setItem('cinny_access_token',s.access_token);localStorage.setItem('cinny_device_id',s.device_id);localStorage.setItem('cinny_user_id',s.user_id);localStorage.setItem('cinny_hs_base_url',s.hs);location.hash='/'+arguments[1]+'/'+arguments[2]+'/';location.reload();return true;",[{**a,'hs':os.environ['MENTION_TEST_HS']},space,room])
        def until(script):
            for _ in range(120):
                try:
                    if js(script):return
                except RuntimeError:pass
                time.sleep(.5)
            raise AssertionError('Native WebView condition timed out: '+script)
        until("return !!document.querySelector('[data-editable-name=RoomInput]')")
        time.sleep(2)
        js("const el=document.querySelector('[data-editable-name=RoomInput]');el.focus();document.execCommand('insertText',false,'NATIVE GROUP '+arguments[0]+' @QA Team');return true;",[run])
        until("return Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('@QA Team'))")
        js("Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('@QA Team')).click();return true;")
        js("document.querySelector('[data-editable-name=RoomInput]').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',bubbles:true,cancelable:true}));return true;")
        event=None
        for _ in range(80):
            messages=api('GET',f'/_matrix/client/v3/rooms/{room}/messages?dir=b&limit=20',a['access_token'])['chunk']
            event=next((e for e in messages if 'NATIVE GROUP '+run in e.get('content',{}).get('body','')),None)
            if event:break
            time.sleep(.25)
        assert event,'Native group message not sent'
        assert event['content']['m.mentions']=={'user_ids':[b['user_id']]},event['content']
        until("return !!document.querySelector('[data-mention-group]')")
        output=os.environ.get('NATIVE_TEST_OUTPUT','/tmp/cinny-groups-native-linux')
        os.makedirs(output,exist_ok=True)
        with open(output+'/group-mentions.png','wb') as screenshot:screenshot.write(base64.b64decode(req(path+'/screenshot')))
        print('PASS native Linux WebKit: actual packaged frontend loads shared group, selects pill, sends standard recipient list and renders compact group',flush=True)
    req(path,method='DELETE')
finally:
    for proc in reversed(procs):proc.terminate()
    loop.quit()
