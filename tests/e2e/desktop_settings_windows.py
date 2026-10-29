"""Exercise installed Windows settings, real Run registration and native window close."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import subprocess
import time
import winreg
from playwright.sync_api import expect


def assert_windows_settings(page, app, binary, env, chromium, output):
    output = Path(output)
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

    def window(pid):
        matches = []
        @callback_type
        def visit(hwnd, _):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            if owner.value == pid and title.value == 'Cinny':
                matches.append(hwnd)
            return True
        user32.EnumWindows(visit, 0)
        return matches[0] if matches else None

    def wait_for(check, label):
        for _ in range(150):
            if check():
                return
            time.sleep(.1)
        raise AssertionError(label)

    def invoke(cmd, args=None, target=page):
        return target.evaluate('(p) => window.__TAURI_INTERNALS__.invoke(p.cmd,p.args)', {'cmd':cmd,'args':args or {}})

    run_key = r'Software\Microsoft\Windows\CurrentVersion\Run'
    def run_entries():
        found = []
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key) as key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    name, value, _ = winreg.EnumValue(key, index)
                    if isinstance(value, str) and str(binary).lower() in value.lower():
                        found.append((name, value))
        except FileNotFoundError:
            pass
        return found

    original = invoke('desktop_get_settings')
    assert original['platform'] == 'windows'
    invoke('desktop_set_settings', {'patch': {'autostart': False}})
    page.get_by_role('button', name='Настройки пользователя', exact=True).click()
    page.get_by_text(re.compile(r'^(Уведомления|Notifications)$')).first.click()
    control = page.get_by_role('switch', name='Запускать при старте системы', exact=True)
    expect(control).to_be_enabled()
    control.scroll_into_view_if_needed()
    control.click()
    expect(control).to_have_attribute('aria-checked', 'true')
    entries = run_entries()
    assert len(entries) == 1 and '--minimized' in entries[0][1], entries
    command = entries[0][1]
    assert command.startswith('"'), command
    control.click()
    expect(control).to_have_attribute('aria-checked', 'false')
    assert not run_entries()
    assert invoke('desktop_set_settings', {'patch': {'autostart': False}})['autostart'] is False
    page.screenshot(path=str(output / 'windows-desktop-settings.png'))
    invoke('desktop_set_settings', {'patch': {'closeToTray': True, 'startMinimized': True}})
    hwnd = window(app.pid)
    assert hwnd and user32.IsWindowVisible(hwnd)
    user32.PostMessageW(hwnd, 0x0010, 0, 0)
    wait_for(lambda: not user32.IsWindowVisible(hwnd), 'Close did not hide window to tray')
    assert app.poll() is None
    invoke('desktop_show_window')
    wait_for(lambda: user32.IsWindowVisible(hwnd), 'Tray restore did not show window')
    invoke('desktop_set_settings', {'patch': {'closeToTray': False}})
    user32.PostMessageW(hwnd, 0x0010, 0, 0)
    app.wait(timeout=15)

    # Launch the exact command that Windows would execute at sign-in.
    child = subprocess.Popen(command, env=env, stdout=(output / 'windows-autostart-launch.log').open('w'), stderr=subprocess.STDOUT)
    connection = None
    try:
        wait_for(lambda: window(child.pid), 'Autostart command did not create application window')
        hwnd = window(child.pid)
        assert not user32.IsWindowVisible(hwnd), 'Start-minimized did not hide the window'
        assert child.poll() is None
        for _ in range(100):
            try:
                connection = chromium.connect_over_cdp('http://127.0.0.1:9222')
                target = connection.contexts[0].pages[0]
                settings = invoke('desktop_get_settings', target=target)
                break
            except Exception:
                time.sleep(.1)
        else:
            raise AssertionError('Autostart WebView did not expose native settings')
        assert settings['startMinimized'] and not settings['closeToTray']
        invoke('desktop_show_window', target=target)
        wait_for(lambda: user32.IsWindowVisible(hwnd), 'Minimized startup window could not be restored')
        invoke('desktop_set_settings', {'patch': {'autostart': original['autostart'], 'startMinimized': original['startMinimized'], 'closeToTray': False}}, target=target)
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
        child.wait(timeout=15)
        config = Path(os.environ['APPDATA']) / 'in.cinny.app/desktop-settings.json'
        config.write_text(json.dumps({'closeToTray': original['closeToTray'], 'startMinimized': original['startMinimized']}))
    finally:
        if connection:
            try:
                connection.close()
            except Exception:
                pass
        if child.poll() is None:
            subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'], check=False)
    (output / 'windows-desktop-settings.json').write_text(json.dumps({'autostart_ui_enable_disable': True, 'registered_command':command, 'registered_command_launches_minimized':True, 'close_hides':True, 'tray_restore':True, 'close_without_tray_exits':True}, indent=2))
    print('PASS Windows native settings: renamed UI toggle, real Run registration/removal, exact registered command starts minimized, close hides, restore works, close without tray exits', flush=True)
