"""Use the real macOS status menu and launch agent of an existing app bundle."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import time

OUT = Path(os.environ['TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=True)
binary = Path(os.environ['CINNY_BINARY'])


def apple(body):
    result = subprocess.run(['osascript', '-e', 'tell application "System Events"\ntell process "cinny"\n' + body + '\nend tell\nend tell'], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def menu_item(label):
    return apple('click menu bar item 1 of menu bar 2\ndelay 0.3\nclick menu item "' + label + '" of menu 1 of menu bar item 1 of menu bar 2')


def wait_for(check, label):
    for _ in range(60):
        if check():
            return
        time.sleep(.2)
    raise AssertionError(label)


agent = Path.home() / 'Library/LaunchAgents/Cinny.plist'
before = agent.read_bytes() if agent.exists() else None
config = Path.home() / 'Library/Application Support/in.cinny.app/desktop-settings.json'
config.parent.mkdir(parents=True, exist_ok=True)
previous_settings = config.read_bytes() if config.exists() else None
config.write_text(json.dumps({'closeToTray': True, 'startMinimized': False}))
app = subprocess.Popen([str(binary)], stdout=(OUT / 'native-launch.log').open('w'), stderr=subprocess.STDOUT)
try:
    time.sleep(8)
    assert app.poll() is None, 'App exited during startup'
    # UI scripting is deliberately checked, not replaced with a source-only assertion.
    (OUT / 'accessibility.txt').write_text(apple('get name of every menu bar'))
    label = 'Запускать при старте системы'
    if agent.exists():
        menu_item(label)
        wait_for(lambda: not agent.exists(), 'Autostart did not disable')
    menu_item(label)
    wait_for(agent.exists, 'Autostart did not create LaunchAgent')
    data = plistlib.loads(agent.read_bytes())
    args = data['ProgramArguments']
    assert args[0] == str(binary.resolve()) and '--minimized' in args, data
    menu_item(label)
    wait_for(lambda: not agent.exists(), 'Autostart did not remove LaunchAgent')
    apple('click (first button of window 1 whose subrole is "AXCloseButton")')
    time.sleep(1)
    assert app.poll() is None
    assert int(apple('get count of windows')) == 0, 'Close did not hide the window'
    menu_item('Открыть Cinny')
    wait_for(lambda: int(apple('get count of windows')) > 0, 'Tray did not restore window')
    menu_item('Сворачивать в трей при закрытии')
    assert json.loads(config.read_text())['closeToTray'] is False
    menu_item('Сворачивать в трей при закрытии')
    assert json.loads(config.read_text())['closeToTray'] is True
    subprocess.run(['screencapture', '-x', str(OUT / 'macos-settings.png')], check=True)
    menu_item('Сворачивать в трей при закрытии')
    assert json.loads(config.read_text())['closeToTray'] is False
    apple('click (first button of window 1 whose subrole is "AXCloseButton")')
    wait_for(lambda: app.poll() is not None, 'Close without tray did not exit')
    (OUT / 'macos-settings.json').write_text(json.dumps({'launch_agent_enable_disable': True, 'registered_args': args, 'close_hides': True, 'tray_restores': True, 'close_setting_persists': True, 'close_without_tray_exits': True}, indent=2))
    print('PASS macOS native package: actual LaunchAgent enable/disable, close-to-tray, menu restore and persisted setting', flush=True)
finally:
    try:
        menu_item('Выход')
    except Exception:
        app.terminate()
    if before is not None:
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.write_bytes(before)
    elif agent.exists():
        agent.unlink()
    if previous_settings is not None:
        config.write_bytes(previous_settings)
    elif config.exists():
        config.unlink()
