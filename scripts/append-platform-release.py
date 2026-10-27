"""Attach verified platform packages to an existing release without moving its tag."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def gh(*args):
    return subprocess.check_output(['gh', *map(str, args)], text=True)


def api(path):
    return json.loads(gh('api', path))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def marker(path):
    raw = path.read_bytes()
    return raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig').strip()


def main():
    repo = os.environ['GITHUB_REPOSITORY']
    tag = os.environ['RELEASE_TAG']
    assert re.fullmatch(r'v\d+\.\d+\.\d+', tag), tag
    version = tag[1:]
    platform_run = int(os.environ['PLATFORM_RUN_ID'])
    android_run = int(os.environ['ANDROID_RUN_ID'])
    output = Path('release-verification')
    output.mkdir()
    release_id = json.loads(gh('release', 'view', tag, '-R', repo, '--json', 'databaseId'))['databaseId']
    release = api(f'repos/{repo}/releases/{release_id}')
    assert not release['draft'] and not release['prerelease'] and not release.get('immutable')
    subprocess.check_call(['gh', 'release', 'download', tag, '-R', repo, '--dir', str(output),
                           '--pattern', 'build-info.json', '--pattern', 'release-notes.txt'])
    info = json.loads((output / 'build-info.json').read_text())
    assert info['version'] == version
    desktop = info['desktop_commit']
    web = info['web_commit']
    assert api(f'repos/{repo}/commits/{tag}')['sha'] == desktop
    owner = repo.split('/')[0]
    assert api(f'repos/{owner}/cinny/commits/{tag}')['sha'] == web
    runs = [info['windows_validation_run'], platform_run, android_run]
    for run_id in set(runs):
        run = api(f'repos/{repo}/actions/runs/{run_id}')
        assert run['status'] == 'completed' and run['conclusion'] == 'success', run_id

    windows = [p for p in info['packages'] if p['file'].endswith(('.exe', '.msi'))]
    assert len(windows) == 2
    original_windows = {p['file']: (p['bytes'], 'sha256:' + p['sha256']) for p in windows}
    assets = {a['name']: a for a in release['assets']}
    for name, expected in original_windows.items():
        assert (assets[name]['size'], assets[name]['digest']) == expected, name

    platforms = [
        ('linux', platform_run, f'cinny-{tag}-linux-x86_64', [
            f'Cinny_{version}_amd64.deb', f'Cinny_{version}_amd64.AppImage']),
        ('macos', platform_run, f'cinny-{tag}-macos-universal', [
            f'Cinny_{version}_universal.dmg', f'Cinny_{version}_universal.app.zip']),
        ('android', android_run, f'cinny-{version}-android-release', [
            f'Cinny_{version}_android.apk', f'Cinny_{version}_android-arm64.apk',
            f'Cinny_{version}_android-arm32.apk', f'Cinny_{version}_android-x86_64.apk',
            f'Cinny_{version}_android-release.aab']),
    ]
    packages = list(windows)
    files = []
    for platform, run_id, artifact, names in platforms:
        target = Path('release-downloads') / platform
        subprocess.check_call(['gh', 'run', 'download', str(run_id), '-R', repo,
                               '--name', artifact, '--dir', str(target)])
        for name, expected in [('desktop-source.txt', desktop), ('web-source.txt', web)]:
            paths = list(target.rglob(name))
            assert len(paths) == 1 and marker(paths[0]) == expected, (platform, name)
        manifests = list(target.rglob('SHA256SUMS'))
        assert len(manifests) == 1, platform
        sums = dict(line.split('  ', 1)[::-1] for line in manifests[0].read_text().splitlines())
        for name in names:
            paths = list(target.rglob(name))
            assert len(paths) == 1, (platform, name)
            path = paths[0]
            sha = digest(path)
            assert sums[name] == sha, name
            packages.append({'file': name, 'bytes': path.stat().st_size, 'sha256': sha})
            files.append(path)
        if platform == 'android':
            signature = (target / 'android-signature.txt').read_text()
            cert = re.search(r'Signer #1 certificate SHA-256 digest: ([0-9a-fA-F]+)', signature).group(1)
            expected_cert = Path('android-signing-certificate.sha256').read_text().strip()
            assert cert.lower() == expected_cert.lower()
            install = json.loads((target / 'api33-installation/installation.json').read_text())
            assert install['update_preserved_data'] and install['fresh_install']
            assert install['version'] == version
            for variant in ['native-release', 'native-debug']:
                assert (target / variant / 'native-video.json').is_file(), variant

    info.update({
        'build_scope': ['windows-x64', 'linux-x86_64', 'macos-universal', 'android'],
        'linux_ci_run': platform_run, 'macos_ci_run': platform_run,
        'android_ci_run': android_run, 'android_validation_run': android_run,
        'platform_sources': {name: desktop for name in ['windows', 'linux', 'macos', 'android']},
        'packages': packages,
        'signing': {'android': 'persistent release key; certificate verified', 'macos': 'ad hoc'},
    })
    info['verification'].update({'linux': 'build, native launch and clipboard checks passed',
                                 'macos': 'Universal binaries, bundle signature and native launch passed',
                                 'android': 'APK/AAB signing, Android 13 install/upgrade and Android 35 WebView checks passed'})
    (output / 'build-info.json').write_text(json.dumps(info, ensure_ascii=False, indent=2) + '\n')
    notes = (output / 'release-notes.txt').read_text()
    old = 'Windows x64: EXE и MSI. Опубликованы те же установщики, которые прошли автоматическую проверку установки и WebView2 и были проверены пользователем.'
    new = ('Windows x64: EXE и MSI; сохранены установщики, проверенные пользователем.\n'
           'Linux x64: DEB и AppImage.\n'
           'macOS Universal: DMG и ZIP приложения для Intel и Apple Silicon.\n'
           'Android: универсальный APK, отдельные APK ARM64/ARM32/x86_64 и AAB; versionCode '
           + str(sum(int(n) * m for n, m in zip(version.split('.'), [1000000, 1000, 1]))) + '.\n\n'
           'Android подписан прежним постоянным ключом. macOS — ad hoc, без notarization.')
    if old in notes:
        notes = notes.replace(old, new)
    elif 'Linux x64: DEB и AppImage.' not in notes:
        raise RuntimeError('Unexpected release notes; refusing to replace them')
    (output / 'release-notes.txt').write_text(notes)
    sums = {p['file']: p['sha256'] for p in packages}
    for name in ['build-info.json', 'release-notes.txt']:
        sums[name] = digest(output / name)
    (output / 'SHA256SUMS.txt').write_text(''.join(sha + '  ' + name + '\n' for name, sha in sorted(sums.items())))
    files += [output / name for name in ['build-info.json', 'release-notes.txt', 'SHA256SUMS.txt']]

    # Verify all inputs before changing release assets. Existing Windows files are never replaced.
    for path in files:
        current = assets.get(path.name)
        if current and current['state'] == 'uploaded' and current.get('digest') == 'sha256:' + digest(path):
            continue
        subprocess.check_call(['gh', 'release', 'upload', tag, '-R', repo, '--clobber', str(path)])
    gh('release', 'edit', tag, '-R', repo, '--notes-file', str(output / 'release-notes.txt'))
    actual = api(f'repos/{repo}/releases/{release_id}')
    expected = {p['file']: (p['bytes'], 'sha256:' + p['sha256']) for p in packages}
    for name in ['build-info.json', 'release-notes.txt', 'SHA256SUMS.txt']:
        path = output / name
        expected[name] = (path.stat().st_size, 'sha256:' + digest(path))
    assert {a['name'] for a in actual['assets']} == set(expected)
    for asset in actual['assets']:
        assert asset['state'] == 'uploaded'
        assert (asset['size'], asset['digest']) == expected[asset['name']], asset['name']
    assert api(f'repos/{repo}/commits/{tag}')['sha'] == desktop
    (output / 'published-release.json').write_text(json.dumps({
        'release_url': actual['html_url'], 'tag': tag, 'desktop_commit': desktop,
        'web_commit': web, 'packages': packages, 'windows_unchanged': True,
    }, ensure_ascii=False, indent=2) + '\n')
    print('All platform assets verified: ' + actual['html_url'], flush=True)


if __name__ == '__main__':
    main()
