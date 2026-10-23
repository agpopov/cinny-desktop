"""Verify release APK installation and an update without losing application data."""

import argparse
import json
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apk', type=Path, required=True)
    parser.add_argument('--previous-apk', type=Path, required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--api', default='33')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # This test intentionally removes its test app. Never accept a physical device.
    if not args.serial.startswith('emulator-'):
        parser.error('Use a disposable Android emulator')
    for apk in (args.apk, args.previous_apk):
        if not apk.is_file():
            parser.error(f'APK does not exist: {apk}')

    args.output.mkdir(parents=True, exist_ok=True)
    report = {'serial': args.serial, 'steps': []}

    def adb(*command, check=True, timeout=180):
        result = subprocess.run(
            ['adb', '-s', args.serial, *map(str, command)],
            capture_output=True, text=True, timeout=timeout,
        )
        report['steps'].append({
            'command': list(map(str, command)), 'returncode': result.returncode,
            'stdout': result.stdout, 'stderr': result.stderr,
        })
        (args.output / 'installation.json').write_text(json.dumps(report, indent=2) + '\n')
        if check and result.returncode:
            raise RuntimeError(f'Android command failed: {command}: {result.stdout}{result.stderr}')
        return result.stdout.strip()

    try:
        assert adb('shell', 'getprop', 'ro.build.version.sdk') == args.api
        adb('root')
        adb('wait-for-device')
        adb('uninstall', 'in.cinny.app', check=False)
        assert 'Success' in adb('install', '--no-streaming', args.previous_apk)
        data = '/data/user/0/in.cinny.app'
        previous_uid = adb('shell', 'stat', '-c', '%u', data)
        assert previous_uid.isdigit(), previous_uid
        probe = data + '/files/cinny-install-regression'
        adb('shell', f'mkdir -p {data}/files && echo keep-cinny-data > {probe}')
        adb('shell', 'chown', previous_uid + ':' + previous_uid, data + '/files', probe)
        adb('shell', 'chmod', '600', probe)

        assert 'Success' in adb('install', '--no-streaming', '-r', args.apk)
        assert adb('shell', 'stat', '-c', '%u', data) == previous_uid
        assert adb('shell', 'cat', probe) == 'keep-cinny-data'
        report['update_preserved_data'] = True

        assert 'Success' in adb('uninstall', 'in.cinny.app')
        assert 'Success' in adb('install', '--no-streaming', args.apk)
        package = adb('shell', 'dumpsys', 'package', 'in.cinny.app')
        version = json.loads((Path(__file__).resolve().parents[2] / 'package.json').read_text())['version']
        major, minor, patch = map(int, version.split('.'))
        assert f'versionName={version}' in package
        assert f'versionCode={major * 1000000 + minor * 1000 + patch}' in package
        report.update({'fresh_install': True, 'version': version, 'api': args.api})
        adb('shell', 'am', 'start', '-n', 'in.cinny.app/.MainActivity')
        time.sleep(20)
        assert adb('shell', 'pidof', 'in.cinny.app')
        report['native_process_started'] = True
        with (args.output / 'startup.png').open('wb') as screenshot:
            subprocess.run(
                ['adb', '-s', args.serial, 'exec-out', 'screencap', '-p'],
                stdout=screenshot, check=True, timeout=60,
            )
        print(f'PASS Android API {args.api}: fresh installation, native process and update preserve application data')
    finally:
        log = adb('logcat', '-d', '-v', 'threadtime', check=False)
        (args.output / 'installation-logcat.txt').write_text(log)
        (args.output / 'installation.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
