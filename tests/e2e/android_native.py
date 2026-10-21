"""Exercise the installed debug APK through its real Android WebView and plugins."""
import json, os, subprocess, time
import xml.etree.ElementTree as ET
import urllib.request, urllib.parse
import websocket
import atexit
from pathlib import Path


OUT = Path('android-artifacts') / os.environ.get('ANDROID_TEST_VARIANT','native-debug'); OUT.mkdir(parents=True,exist_ok=True)
APP = 'in.cinny.app'
def adb(*args):
    return subprocess.check_output(['adb', *args], text=True).strip()
class WebView:
    def __init__(self, socket): self.socket=socket; self.counter=0
    def evaluate(self, expression, value=None):
        if value is not None: expression='('+expression+')('+json.dumps(value)+')'
        self.counter+=1
        self.socket.send(json.dumps({'id':self.counter,'method':'Runtime.evaluate','params':{'expression':expression,'awaitPromise':True,'returnByValue':True}}))
        while True:
            message=json.loads(self.socket.recv())
            if message.get('id')==self.counter: break
        if 'error' in message: raise RuntimeError(message['error'])
        result=message['result']
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
def invoke(page, command, args=None):
    return page.evaluate('([cmd,args])=>window.__TAURI_INTERNALS__.invoke(cmd,args||{})', [command,args])
def collect_diagnostics():
    try:
        (OUT/'android-logcat.txt').write_text(adb('logcat','-d'))
        (OUT/'android-sockets.txt').write_text(adb('shell','cat','/proc/net/unix'))
        adb('shell','uiautomator','dump','/sdcard/window.xml')
        adb('pull','/sdcard/window.xml',str(OUT/'android-startup.xml'))
        with open(OUT/'android-startup.png','wb') as f: subprocess.run(['adb','exec-out','screencap','-p'],stdout=f,check=True)
    except Exception as error: print('Diagnostics: '+str(error),flush=True)
atexit.register(collect_diagnostics)
def launch(target=None):
    args = ['shell','am','start','-n',APP+'/.MainActivity']
    if target:
        args += ['--es','cinny.notification.target',"'"+json.dumps(target,separators=(',',':'))+"'"]
    adb(*args)

if __name__ == "__main__":
    launch(); page = connect()
    package=adb('shell','dumpsys','package',APP)
    assert 'versionName='+os.environ.get('EXPECTED_ANDROID_VERSION','4.12.10') in package
    assert 'versionCode='+os.environ.get('EXPECTED_ANDROID_VERSION_CODE','4012010') in package
    page.wait_for_function("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.trim()==='Login')")
    assert page.evaluate('!!crypto.subtle && !!indexedDB && !!WebAssembly')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    with open(OUT/'android-login-webview.png','wb') as f: subprocess.run(['adb','exec-out','screencap','-p'],stdout=f,check=True)
    # Verify that the native root resizes when the software keyboard appears.
    height=page.evaluate('innerHeight')
    adb('shell','uiautomator','dump','/sdcard/login-window.xml')
    tree=ET.fromstring(adb('shell','cat','/sdcard/login-window.xml'))
    view=next(node for node in tree.iter('node') if node.get('class')=='android.webkit.WebView')
    bounds=list(map(int,view.get('bounds').replace('][',',').strip('[]').split(',')))
    point=page.evaluate("(()=>{const r=document.querySelector('input[name=usernameInput]').getBoundingClientRect();return [(r.x+r.width/2)*devicePixelRatio,(r.y+r.height/2)*devicePixelRatio]})()")
    adb('shell','input','tap',str(round(bounds[0]+point[0])),str(round(bounds[1]+point[1])))
    page.wait_for_function('innerHeight<'+str(height-100),timeout=10000)
    assert page.evaluate('document.activeElement.getBoundingClientRect().bottom<=innerHeight')
    with open(OUT/'android-keyboard.png','wb') as f: subprocess.run(['adb','exec-out','screencap','-p'],stdout=f,check=True)
    adb('shell','input','keyevent','4');time.sleep(1)
    adb('shell','pm','revoke',APP,'android.permission.POST_NOTIFICATIONS')
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
    adb('shell','uiautomator','dump','/sdcard/native-check.xml')
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
