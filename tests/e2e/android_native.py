"""Exercise the installed debug APK through its real Android WebView and plugins."""
import json, os, subprocess, time, re
import xml.etree.ElementTree as ET
import urllib.request, urllib.parse
import websocket
import atexit
from pathlib import Path
import base64
from native_video import PLAYER_URL, PROVIDER_HTML, CHECK_PLAYER


OUT = Path('android-artifacts') / os.environ.get('ANDROID_TEST_VARIANT','native-debug'); OUT.mkdir(parents=True,exist_ok=True)
APP = 'in.cinny.app'
def adb(*args):
    return subprocess.check_output(['adb', *args], text=True).strip()
def dump_ui(path):
    try: initial_pid=adb('shell','pidof',APP)
    except subprocess.CalledProcessError: initial_pid=None
    for attempt in range(3):
        try: return adb('shell','uiautomator','dump',path)
        except subprocess.CalledProcessError as error:
            # The emulator can kill UIAutomator itself. Never retry a failed
            # app process, a changed PID, an app ANR or another command error.
            if error.returncode!=137 or attempt==2 or not initial_pid: raise
            if adb('shell','pidof',APP)!=initial_pid:
                raise RuntimeError('Cinny restarted while UIAutomator was killed') from error
            log=OUT/'uiautomator-retries.json'
            retries=json.loads(log.read_text()) if log.exists() else []
            retries.append({'path':path,'exit_code':137,'unchanged_app_pid':initial_pid,'attempt':attempt+1})
            log.write_text(json.dumps(retries,indent=2))
            print('UIAutomator exited 137; app PID unchanged; retry '+str(attempt+1),flush=True)
            time.sleep(1)
class WebView:
    def __init__(self, socket): self.socket=socket; self.counter=0; self.player_requests=[]
    def request(self, method, params):
        self.counter+=1; command_id=self.counter
        self.socket.send(json.dumps({'id':command_id,'method':method,'params':params}))
        while True:
            message=json.loads(self.socket.recv())
            if message.get('method')=='Fetch.requestPaused':
                paused=message['params'];self.player_requests.append(paused['request'])
                self.counter+=1
                self.socket.send(json.dumps({'id':self.counter,'method':'Fetch.fulfillRequest','params':{
                    'requestId':paused['requestId'],'responseCode':200,
                    'responseHeaders':[{'name':'Content-Type','value':'text/html; charset=utf-8'}],
                    'body':base64.b64encode(PROVIDER_HTML.encode()).decode(),
                }}))
            if message.get('id')==command_id: break
        if 'error' in message: raise RuntimeError(message['error'])
        return message['result']
    def evaluate(self, expression, value=None):
        if value is not None: expression='('+expression+')('+json.dumps(value)+')'
        result=self.request('Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True})
        if 'exceptionDetails' in result: raise RuntimeError(result['exceptionDetails'])
        return result['result'].get('value')
    def wait_for_function(self, expression, timeout=30000):
        for _ in range(timeout//500):
            if self.evaluate(expression): return
            time.sleep(.5)
        raise RuntimeError('WebView condition timed out: '+expression)
    def close(self): self.socket.close()
def connect():
    last=None
    deadline=time.monotonic()+110
    while time.monotonic()<deadline:
        try:
            pid=adb('shell','pidof',APP).split()[0]
            adb('forward','tcp:9222','localabstract:webview_devtools_remote_'+pid)
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open('http://127.0.0.1:9222/json/list',timeout=3) as response: targets=json.load(response)
            target=next(t for t in targets if t.get('type')=='page')
            parsed=urllib.parse.urlsplit(target['webSocketDebuggerUrl'])
            url=urllib.parse.urlunsplit(('ws','127.0.0.1:9222',parsed.path,parsed.query,''))
            page=WebView(websocket.create_connection(url,timeout=20,suppress_origin=True,http_no_proxy=['127.0.0.1']))
            page.wait_for_function('!!window.__TAURI_INTERNALS__',timeout=15000)
            return page
        except Exception as error:
            last=error;time.sleep(1)
    raise RuntimeError('Android WebView unavailable: '+repr(last))
def webview_bounds():
    for attempt in range(6):
        dump_ui('/sdcard/login-window.xml')
        tree=ET.fromstring(adb('shell','cat','/sdcard/login-window.xml'))
        view=next((node for node in tree.iter('node') if node.get('class')=='android.webkit.WebView'),None)
        if view is not None:
            return list(map(int,view.get('bounds').replace('][',',').strip('[]').split(',')))
        title=next((node.get('text') for node in tree.iter('node') if node.get('resource-id')=='android:id/alertTitle'),'')
        # The emulator launcher may ANR during cold boot. Never dismiss an ANR
        # from Cinny, and never count a hidden WebView as a usable native screen.
        if title not in ("Quickstep isn't responding", "Launcher3 isn't responding"):
            raise RuntimeError('Native WebView is not visible; foreground dialog: '+title)
        close=next(node for node in tree.iter('node') if node.get('resource-id')=='android:id/aerr_close')
        x1,y1,x2,y2=map(int,close.get('bounds').replace('][',',').strip('[]').split(','))
        adb('shell','input','tap',str((x1+x2)//2),str((y1+y2)//2))
        launch();time.sleep(1)
    raise RuntimeError('The emulator launcher repeatedly blocked the WebView')

def invoke(page, command, args=None):
    return page.evaluate('([cmd,args])=>window.__TAURI_INTERNALS__.invoke(cmd,args||{})', [command,args])
def collect_diagnostics():
    try:
        (OUT/'android-logcat.txt').write_text(adb('logcat','-d'))
        (OUT/'android-sockets.txt').write_text(adb('shell','cat','/proc/net/unix'))
        dump_ui('/sdcard/window.xml')
        adb('pull','/sdcard/window.xml',str(OUT/'android-startup.xml'))
        with open(OUT/'android-startup.png','wb') as f: subprocess.run(['adb','exec-out','screencap','-p'],stdout=f,check=True)
    except Exception as error: print('Diagnostics: '+str(error),flush=True)
def launch(target=None):
    args = ['shell','am','start','-n',APP+'/.MainActivity']
    if target:
        args += ['--es','cinny.notification.target',"'"+json.dumps(target,separators=(',',':'))+"'"]
    adb(*args)

if __name__ == "__main__":
    atexit.register(collect_diagnostics)
    adb('shell','pm','revoke',APP,'android.permission.POST_NOTIFICATIONS')
    launch(); page = connect()
    package=adb('shell','dumpsys','package',APP)
    assert 'versionName='+os.environ.get('EXPECTED_ANDROID_VERSION','4.13.0') in package
    assert 'versionCode='+os.environ.get('EXPECTED_ANDROID_VERSION_CODE','4013000') in package
    page.wait_for_function("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.trim()==='Войти')")
    assert page.evaluate('!!crypto.subtle && !!indexedDB && !!WebAssembly')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    with open(OUT/'android-login-webview.png','wb') as f: subprocess.run(['adb','exec-out','screencap','-p'],stdout=f,check=True)
    page.request('Fetch.enable',{'patterns':[{'urlPattern':'https://www.youtube-nocookie.com/embed/*','resourceType':'Document'}]})
    video=page.evaluate(CHECK_PLAYER,PLAYER_URL)
    assert video['isolated'] and video['bridge']=='undefined' and video['width']==160 and video['height']==90 and video['ready']>=2,video
    headers=[{k.lower():v for k,v in request['headers'].items()} for request in page.player_requests]
    assert len(headers)==1 and headers[0].get('referer')==video['appOrigin']+'/',headers
    (OUT/'native-video.json').write_text(json.dumps(video,indent=2))
    page.request('Fetch.disable',{})
    print('PASS Android WebView: CSP allows pinned provider, isolated frame, origin-only Referer and real WebM decodes',flush=True)
    # Verify that the native root resizes when the software keyboard appears.
    height=page.evaluate('innerHeight')
    bounds=webview_bounds()
    point=page.evaluate("(()=>{const r=document.querySelector('input[name=usernameInput]').getBoundingClientRect();return [(r.x+r.width/2)*devicePixelRatio,(r.y+r.height/2)*devicePixelRatio]})()")
    adb('shell','input','tap',str(round(bounds[0]+point[0])),str(round(bounds[1]+point[1])))
    page.wait_for_function('innerHeight<'+str(height-100),timeout=10000)
    assert page.evaluate('document.activeElement.getBoundingClientRect().bottom<=innerHeight')
    with open(OUT/'android-keyboard.png','wb') as f: subprocess.run(['adb','exec-out','screencap','-p'],stdout=f,check=True)
    adb('shell','input','keyevent','4');time.sleep(1)
    # Exercise the actual Android system Back callback, independently of history.
    page.evaluate("window.nativeBackCount=0;window.nativeBackProbe=e=>{e.preventDefault();window.nativeBackCount++};window.addEventListener('cinny:back',window.nativeBackProbe)")
    adb('shell','input','keyevent','4')
    page.wait_for_function('nativeBackCount===1',timeout=10000)
    assert APP in adb('shell','dumpsys','window','windows').split('mCurrentFocus=')[-1].split('\n')[0]
    page.evaluate("window.removeEventListener('cinny:back',window.nativeBackProbe)")
    print('PASS Android physical Back reaches the SPA and keeps the channel activity in foreground',flush=True)
    assert invoke(page,'native_notification_permission',{'request':False}) in ('prompt','prompt-with-rationale','denied')
    adb('shell','pm','grant',APP,'android.permission.POST_NOTIFICATIONS')
    assert invoke(page,'native_notification_permission',{'request':False}) == 'granted'
    target={'roomId':'!native-test:localhost','eventId':'$native-event','threadId':'$native-root'}
    invoke(page,'desktop_notify',{'payload':{'title':'Cinny native check','body':'NATIVE_ANDROID_MESSAGE_41210','target':target}})
    state=adb('shell','dumpsys','notification','--noredact')
    assert 'NATIVE_ANDROID_MESSAGE_41210' in state
    print('PASS Android native notification contains message body',flush=True)
    adb('shell','cmd','statusbar','expand-notifications');time.sleep(2)
    with open(OUT/'android-notification-shade.png','wb') as f:
        subprocess.run(['adb','exec-out','screencap','-p'],stdout=f,check=True)
    dump_ui('/sdcard/native-check.xml')
    hierarchy=ET.fromstring(adb('shell','cat','/sdcard/native-check.xml'))
    title=next(node for node in hierarchy.iter('node') if node.get('text')=='Cinny native check')
    bounds=title.get('bounds').replace('][',',').strip('[]').split(',')
    x1,y1,x2,y2=map(int,bounds)
    adb('shell','input','tap',str((x1+x2)//2),str((y1+y2)//2));time.sleep(1)
    assert (received := invoke(page,'mobile_take_notification_target')) == target, received
    assert invoke(page,'mobile_take_notification_target') == {}
    invoke(page,'desktop_set_unread',{'payload':{'total':0,'hasUnread':False}})
    # A cold launch must retain the destination until the authenticated UI is ready.
    page.close();adb('shell','am','force-stop',APP);launch(target)
    page=connect()
    assert (received := invoke(page,'mobile_take_notification_target')) == target, received
    assert invoke(page,'mobile_take_notification_target') == {}
    page.close()
print('PASS Android native UI, permission, notification delivery, warm/cold target and consume-once checks')

if __name__ == "__main__":
    # Inspect the icon as the launcher actually masks it, after the app smoke checks.
    adb('shell', 'input', 'keyevent', 'KEYCODE_HOME')
    time.sleep(1)
    width, height = map(int, re.findall(r'(\d+)x(\d+)', adb('shell', 'wm', 'size'))[-1])
    adb('shell', 'input', 'swipe', str(width//2), str(round(height*.85)), str(width//2), str(round(height*.2)), '350')
    for attempt in range(3):
        time.sleep(1)
        dump_ui('/sdcard/cinny-launcher.xml')
        hierarchy = ET.fromstring(adb('shell', 'cat', '/sdcard/cinny-launcher.xml'))
        icon = next((node for node in hierarchy.iter('node') if node.get('text') == 'Cinny' or node.get('content-desc') == 'Cinny'), None)
        if icon is not None:
            break
    assert icon is not None, 'Installed Cinny icon is missing from the Android launcher'
    with open(OUT/'android-launcher-icon.png', 'wb') as stream:
        subprocess.run(['adb', 'exec-out', 'screencap', '-p'], stdout=stream, check=True)
    (OUT/'android-launcher.xml').write_text(adb('shell', 'cat', '/sdcard/cinny-launcher.xml'))
    x1, y1, x2, y2 = map(int, icon.get('bounds').replace('][', ',').strip('[]').split(','))
    adb('shell', 'input', 'tap', str((x1+x2)//2), str((y1+y2)//2))
    print('PASS Android launcher displays the installed Cinny icon; screenshot captured', flush=True)
