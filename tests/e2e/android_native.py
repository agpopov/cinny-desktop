"""Exercise the installed debug APK through its real Android WebView and plugins."""
import json, os, subprocess, time
import xml.etree.ElementTree as ET
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path('android-artifacts'); OUT.mkdir(exist_ok=True)
APP = 'in.cinny.app'
def adb(*args):
    return subprocess.check_output(['adb', *args], text=True).strip()
def connect(p):
    for _ in range(40):
        try:
            pid = adb('shell', 'pidof', APP).split()[0]
            adb('forward', 'tcp:9222', 'localabstract:webview_devtools_remote_' + pid)
            browser = p.chromium.connect_over_cdp('http://127.0.0.1:9222')
            page = browser.contexts[0].pages[0]
            page.wait_for_function('!!window.__TAURI_INTERNALS__', timeout=15000)
            return browser, page
        except Exception:
            time.sleep(1)
    raise RuntimeError('Android WebView did not become available')
def invoke(page, command, args=None):
    return page.evaluate('([cmd,args])=>window.__TAURI_INTERNALS__.invoke(cmd,args||{})', [command,args])
def launch(target=None):
    args = ['shell','am','start','-n',APP+'/.MainActivity']
    if target:
        args += ['--es','LocalNotficationObject',"'"+json.dumps({'extra':{'target':target}},separators=(',',':'))+"'"]
    adb(*args)

with sync_playwright() as p:
    launch(); browser,page = connect(p)
    page.get_by_role('button',name='Login',exact=True).wait_for(timeout=30000)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    page.screenshot(path=str(OUT/'android-login-webview.png'))
    assert invoke(page,'native_notification_permission',{'request':False}) in ('prompt','prompt-with-rationale','denied')
    adb('shell','pm','grant',APP,'android.permission.POST_NOTIFICATIONS')
    assert invoke(page,'native_notification_permission',{'request':False}) == 'granted'
    target={'roomId':'!native-test:localhost','eventId':'$native-event','threadId':'$native-root'}
    invoke(page,'desktop_notify',{'payload':{'title':'Cinny native check','body':'NATIVE_ANDROID_MESSAGE_41210','target':target}})
    state=adb('shell','dumpsys','notification','--noredact')
    assert 'NATIVE_ANDROID_MESSAGE_41210' in state
    adb('shell','cmd','statusbar','expand-notifications');time.sleep(2)
    with open(OUT/'android-notification-shade.png','wb') as f:
        subprocess.run(['adb','exec-out','screencap','-p'],stdout=f,check=True)
    adb('shell','uiautomator','dump','/sdcard/native-check.xml')
    hierarchy=ET.fromstring(adb('shell','cat','/sdcard/native-check.xml'))
    title=next(node for node in hierarchy.iter('node') if node.get('text')=='Cinny native check')
    bounds=title.get('bounds').replace('][',',').strip('[]').split(',')
    x1,y1,x2,y2=map(int,bounds)
    adb('shell','input','tap',str((x1+x2)//2),str((y1+y2)//2));time.sleep(1)
    assert invoke(page,'mobile_take_notification_target') == target
    assert invoke(page,'mobile_take_notification_target') == {}
    invoke(page,'desktop_set_unread',{'payload':{'total':0,'hasUnread':False}})
    # A cold launch must retain the destination until the authenticated UI is ready.
    browser.close();adb('shell','am','force-stop',APP);launch(target)
    browser,page=connect(p)
    assert invoke(page,'mobile_take_notification_target') == target
    assert invoke(page,'mobile_take_notification_target') == {}
    browser.close()
print('PASS Android native UI, permission, notification delivery, warm/cold target and consume-once checks')
