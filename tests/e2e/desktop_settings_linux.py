"""Validate autostart and close-to-tray in an existing Linux package, without building."""
import base64
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.request
import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib
from Xlib import X, display, protocol

OUT = Path(os.environ['TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=True)
os.environ['TAURI_WEBVIEW_AUTOMATION'] = 'true'
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()
watcher_name = dbus.service.BusName('org.kde.StatusNotifierWatcher', bus)


class Watcher(dbus.service.Object):
    def __init__(self):
        super().__init__(bus, '/StatusNotifierWatcher')
        self.items = []

    @dbus.service.method('org.kde.StatusNotifierWatcher', in_signature='s', out_signature='', sender_keyword='sender')
    def RegisterStatusNotifierItem(self, service, sender=None):
        self.items.append((str(sender), str(service)))
        self.StatusNotifierItemRegistered(str(service))

    @dbus.service.method('org.kde.StatusNotifierWatcher', in_signature='s', out_signature='')
    def RegisterStatusNotifierHost(self, service):
        pass

    @dbus.service.signal('org.kde.StatusNotifierWatcher', signature='s')
    def StatusNotifierItemRegistered(self, service):
        pass

    @dbus.service.method('org.freedesktop.DBus.Properties', in_signature='ss', out_signature='v')
    def Get(self, interface, key):
        return self.GetAll(interface)[key]

    @dbus.service.method('org.freedesktop.DBus.Properties', in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        return {'ProtocolVersion': dbus.Int32(0), 'IsStatusNotifierHostRegistered': dbus.Boolean(True),
                'RegisteredStatusNotifierItems': dbus.Array([item[1] for item in self.items], signature='s')}


watcher = Watcher()
loop = GLib.MainLoop()
threading.Thread(target=loop.run, daemon=True).start()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(path, body=None, method=None):
    req = urllib.request.Request('http://127.0.0.1:4456' + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json'}, method=method)
    with opener.open(req, timeout=60) as response:
        result = json.load(response)['value']
    if isinstance(result, dict) and 'error' in result:
        raise AssertionError(result)
    return result


def wait_for(check, label):
    for _ in range(100):
        if check():
            return
        time.sleep(.1)
    raise AssertionError(label)


driver = subprocess.Popen(['WebKitWebDriver', '--port=4456', '--host=127.0.0.1'],
                          stdout=(OUT / 'driver.log').open('w'), stderr=subprocess.STDOUT)
entry = Path.home() / '.config/autostart/Cinny.desktop'
before = entry.read_bytes() if entry.exists() else None
session = None
try:
    time.sleep(1)
    session = request('/session', {'capabilities': {'alwaysMatch': {'webkitgtk:browserOptions': {'binary': os.environ['CINNY_BINARY']}}}})['sessionId']
    endpoint = '/session/' + session
    request(endpoint + '/timeouts', {'script': 15000})

    def invoke(command, args=None):
        return request(endpoint + '/execute/async', {'script': "const done=arguments[arguments.length-1];window.__TAURI_INTERNALS__.invoke(arguments[0],arguments[1]).then(done,e=>done({error:String(e)}));", 'args': [command, args or {}]})

    settings = invoke('desktop_get_settings')
    assert settings['platform'] == 'linux', settings
    enabled = invoke('desktop_set_settings', {'patch': {'autostart': True}})
    assert enabled['autostart'] and entry.exists(), enabled
    desktop_entry = entry.read_text()
    assert os.environ['CINNY_BINARY'] in desktop_entry and '--minimized' in desktop_entry, desktop_entry
    assert invoke('desktop_set_settings', {'patch': {'autostart': False}})['autostart'] is False
    assert not entry.exists()
    wait_for(lambda: watcher.items, 'Native tray did not register with StatusNotifierWatcher')
    connection = display.Display()

    def find_window(window):
        if window.get_wm_name() == 'Cinny':
            return window
        for child in window.query_tree().children:
            found = find_window(child)
            if found:
                return found
        return None

    window = find_window(connection.screen().root)
    assert window is not None

    def close_window():
        window.send_event(protocol.event.ClientMessage(window=window,
                          client_type=connection.intern_atom('WM_PROTOCOLS'),
                          data=(32, [connection.intern_atom('WM_DELETE_WINDOW'), X.CurrentTime, 0, 0, 0])))
        connection.flush()

    invoke('desktop_set_settings', {'patch': {'closeToTray': True}})
    close_window()
    wait_for(lambda: window.get_attributes().map_state != X.IsViewable, 'Close did not hide window')
    assert invoke('desktop_get_settings')['closeToTray'] is True
    invoke('desktop_show_window')
    wait_for(lambda: window.get_attributes().map_state == X.IsViewable, 'Tray restore did not show window')
    (OUT / 'linux-settings.png').write_bytes(base64.b64decode(request(endpoint + '/screenshot')))
    invoke('desktop_set_settings', {'patch': {'closeToTray': False}})
    assert invoke('desktop_get_settings')['closeToTray'] is False
    state = Path.home() / '.config/in.cinny.app/desktop-settings.json'
    assert json.loads(state.read_text())['closeToTray'] is False
    close_window()
    wait_for(lambda: subprocess.run(['pgrep', '-x', 'cinny'], stdout=subprocess.DEVNULL).returncode != 0, 'Close without tray did not exit')
    (OUT / 'linux-settings.json').write_text(json.dumps({'autostart_enable_disable': True, 'desktop_entry': desktop_entry, 'tray_registered': True, 'close_hides': True, 'restore_shows': True, 'settings_persisted': True, 'close_without_tray_exits': True}, indent=2))
    print('PASS Linux native package: actual autostart registration/removal, StatusNotifier tray, close hides, restore shows, setting persists', flush=True)
finally:
    if session:
        try:
            request('/session/' + session, method='DELETE')
        except Exception:
            pass
    driver.terminate()
    loop.quit()
    if before is not None:
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_bytes(before)
    elif entry.exists():
        entry.unlink()
