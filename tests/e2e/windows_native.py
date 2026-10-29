"""Smoke-test the installed NSIS package through its real Windows WebView2."""
import json
import sys
import os
import ctypes
from contextlib import contextmanager
from pathlib import Path
import subprocess
import time
import urllib.request

from playwright.sync_api import sync_playwright
from native_video import PLAYER_URL, PROVIDER_HTML, CHECK_PLAYER

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'cinny/tests/e2e'))
from session_fixture import ROOM, install_session_fixture, seed_session, assert_session_ready
from session_navigation import assert_client_navigation
from tenor_previews import assert_tenor_preview
from new_threads import assert_new_thread
from media_only import assert_media_only
from desktop_settings_windows import assert_windows_settings

out = Path('windows-artifacts')
out.mkdir(exist_ok=True)
binary = Path(os.environ['CINNY_BINARY'])
assert binary.is_file(), binary
expected_version = os.environ.get('EXPECTED_VERSION') or json.loads(
    (Path(__file__).resolve().parents[2] / 'package.json').read_text()
)['version']
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
            page.on('pageerror', lambda error: errors.append({'message':str(error),'stack':error.stack}))
            page.get_by_role('button', name='Войти', exact=True).wait_for(timeout=60000)
            assert page.evaluate('!!crypto.subtle && !!indexedDB && !!WebAssembly')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            assert expected_version in page.locator('body').inner_text()
            settings = page.evaluate("window.__TAURI_INTERNALS__.invoke('desktop_get_settings')")
            assert settings['platform'] == 'windows', settings
            page.screenshot(path=str(out / 'installed-startup.png'), full_page=True)
            player_requests = []
            def provider_fixture(route):
                player_requests.append(dict(route.request.headers))
                route.fulfill(content_type='text/html; charset=utf-8', body=PROVIDER_HTML)
            page.context.route(PLAYER_URL, provider_fixture)
            video = page.evaluate(CHECK_PLAYER, PLAYER_URL)
            assert video['isolated'] and video['bridge'] == 'undefined' and video['width'] == 160 and video['height'] == 90 and video['ready'] >= 2, video
            assert len(player_requests) == 1 and player_requests[0].get('referer') == video['appOrigin'] + '/', player_requests
            (out / 'native-video.json').write_text(json.dumps(video, indent=2))
            print('PASS installed Windows WebView2: CSP allows pinned provider, frame is isolated, origin-only Referer and real WebM decodes', flush=True)
            page.evaluate("window.__TAURI_INTERNALS__.invoke('desktop_set_unread', {payload:{total:2,highlight:true,badge:null,hasUnread:true}})")
            page.evaluate("window.__TAURI_INTERNALS__.invoke('desktop_set_unread', {payload:{total:0,highlight:false,badge:null,hasUnread:false}})")
            (out / 'native-settings.json').write_text(json.dumps(settings, indent=2))
            assert not errors, errors
            print('PASS Windows NSIS installation, actual WebView2 startup, version, crypto APIs, viewport and native settings/unread IPC', flush=True)
            # Test the authenticated client, not only the unauthenticated shell.
            origin = page.evaluate('location.origin')
            (out / 'installed-index.html').write_text(page.content(), encoding='utf-8')
            session_requests = install_session_fixture(page)
            seed_session(page)
            page.goto(origin + '/#/home/' + ROOM + '/')
            page.reload()
            assert_session_ready(page)
            page.screenshot(path=str(out / 'installed-saved-session.png'), full_page=True)
            page.wait_for_timeout(1500)
            page.reload()
            assert_session_ready(page)
            page.screenshot(path=str(out / 'installed-saved-session-reload.png'), full_page=True)
            page.wait_for_timeout(1500)
            navigation = assert_client_navigation(page, out)
            (out / 'native-navigation.json').write_text(json.dumps(navigation, indent=2))
            assert not any('/join/' in r['path'] or r['path'].endswith('/join') for r in session_requests), session_requests
            assert any(r['path'].endswith('/keys/upload') for r in session_requests), session_requests
            assert any(r['path'].endswith('/sync') for r in session_requests), session_requests
            (out / 'session-requests.json').write_text(json.dumps(session_requests, indent=2))
            assert not errors, errors
            print('PASS installed Windows: saved session restores with cold and warm IndexedDB, real Rust crypto and rendered room; no Node global alias', flush=True)
            assert_tenor_preview(page, out)
            assert_new_thread(page, out)
            assert_media_only(page, out)
            # Exercise sends in the installed WebView2 using the web client's regression.
            page.goto(origin + '/config.json')
            page.evaluate('''async () => {
                localStorage.clear();
                for (const db of await indexedDB.databases()) {
                    await new Promise((resolve,reject) => {
                        const request = indexedDB.deleteDatabase(db.name);
                        request.onsuccess = resolve;
                        request.onerror = reject;
                    });
                }
            }''')
            page.unroute_all(behavior='wait')
            import timeline_scroll
            timeline_scroll.OUT = out / 'scroll'
            timeline_scroll.OUT.mkdir(exist_ok=True)
            timeline_scroll.scenario(browser, 'windows-webview2', 0, 25, 1280,
                                     native_page=page, app_url=origin)
            assert not errors, errors
            assert_windows_settings(page, app, binary, env, p.chromium, out)
            try:
                browser.close()
            except Exception:
                if app.poll() is None:
                    raise
    finally:
        (out / 'runtime-errors.json').write_text(json.dumps(errors, indent=2))
        if app.poll() is None:
            subprocess.run(['taskkill', '/PID', str(app.pid), '/T', '/F'], check=False)
