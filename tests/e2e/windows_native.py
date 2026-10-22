"""Smoke-test the installed NSIS package through its real Windows WebView2."""
import json
import os
import ctypes
from contextlib import contextmanager
from pathlib import Path
import subprocess
import time
import urllib.request

from playwright.sync_api import sync_playwright

out = Path('windows-artifacts')
out.mkdir(exist_ok=True)
binary = Path(os.environ['CINNY_BINARY'])
assert binary.is_file(), binary
env = os.environ.copy()
env['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--remote-debugging-port=9222'
env['WEBVIEW2_USER_DATA_FOLDER'] = str((out / 'webview-profile').resolve())
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
errors = []


@contextmanager
def webview_debug_policy():
    # WebView2 150+ ignores WEBVIEW2_* overrides for an elevated host. GitHub's
    # Windows runner is elevated; use an app-specific HKLM policy only in that
    # disposable CI VM, and restore it even when the test fails.
    if not ctypes.windll.shell32.IsUserAnAdmin():
        yield
        return
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('Run this test without administrator elevation outside GitHub Actions')
    import winreg
    path = r'SOFTWARE\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments'
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, access=winreg.KEY_ALL_ACCESS) as key:
        try:
            previous = winreg.QueryValueEx(key, binary.name)
        except FileNotFoundError:
            previous = None
        winreg.SetValueEx(key, binary.name, 0, winreg.REG_SZ, env['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'])
        try:
            yield
        finally:
            if previous is None:
                winreg.DeleteValue(key, binary.name)
            else:
                winreg.SetValueEx(key, binary.name, 0, previous[1], previous[0])


with webview_debug_policy(), (out / 'native-launch.log').open('w') as log:
    app = subprocess.Popen([str(binary)], env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        for attempt in range(120):
            assert app.poll() is None, 'Installed app exited during startup'
            try:
                with opener.open('http://127.0.0.1:9222/json/version', timeout=1) as response:
                    runtime = json.load(response)
                break
            except (OSError, ValueError):
                time.sleep(1)
        else:
            raise AssertionError('Installed WebView2 did not expose a debugging endpoint')
        (out / 'webview-runtime.json').write_text(json.dumps(runtime, indent=2))
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp('http://127.0.0.1:9222')
            pages = [page for context in browser.contexts for page in context.pages]
            assert pages, 'Installed app has no WebView page'
            page = next((page for page in pages if not page.url.startswith('devtools:')), pages[0])
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.get_by_role('button', name='Войти', exact=True).wait_for(timeout=60000)
            assert page.evaluate('!!crypto.subtle && !!indexedDB && !!WebAssembly')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            assert '4.12.14' in page.locator('body').inner_text()
            settings = page.evaluate("window.__TAURI_INTERNALS__.invoke('desktop_get_settings')")
            assert settings['platform'] == 'windows', settings
            page.screenshot(path=str(out / 'installed-startup.png'), full_page=True)
            page.evaluate("window.__TAURI_INTERNALS__.invoke('desktop_set_unread', {payload:{total:2,highlight:true,badge:null,hasUnread:true}})")
            page.evaluate("window.__TAURI_INTERNALS__.invoke('desktop_set_unread', {payload:{total:0,highlight:false,badge:null,hasUnread:false}})")
            (out / 'native-settings.json').write_text(json.dumps(settings, indent=2))
            assert not errors, errors
            print('PASS Windows NSIS installation, actual WebView2 startup, version, crypto APIs, viewport and native settings/unread IPC', flush=True)
            browser.close()
    finally:
        (out / 'runtime-errors.json').write_text(json.dumps(errors, indent=2))
        if app.poll() is None:
            subprocess.run(['taskkill', '/PID', str(app.pid), '/T', '/F'], check=False)
