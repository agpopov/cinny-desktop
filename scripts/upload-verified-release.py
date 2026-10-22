"""Populate a draft prerelease with existing, checksum-verified CI packages.

Run from GitHub Actions to avoid sending large binaries through a developer's
uplink. Publication remains a separate action after reviewing all release assets.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def gh(*args):
    return subprocess.check_output(['gh', *args], text=True)


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return 'sha256:' + value.hexdigest()


def source_marker(path):
    raw = path.read_bytes()
    return raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig').strip()


def main():
    repo = os.environ['GITHUB_REPOSITORY']
    tag = os.environ['RELEASE_TAG']
    release_id = json.loads(gh('release', 'view', tag, '-R', repo, '--json', 'databaseId'))['databaseId']
    release = json.loads(gh('api', f'repos/{repo}/releases/{release_id}'))
    if not release['draft'] or not release['prerelease']:
        raise RuntimeError('Only an existing draft prerelease can be populated')

    output = Path('verified-release-assets')
    output.mkdir()
    subprocess.check_call([
        'gh', 'release', 'download', tag, '-R', repo, '--dir', str(output),
        '--pattern', 'SHA256SUMS.txt', '--pattern', 'build-info.json',
    ])
    info = json.loads((output / 'build-info.json').read_text())
    version = info['version']
    release_document = info.get('release_document', 'mention-groups.md')
    web_archive = info.get('web_archive')
    extra_assets = [release_document] + ([web_archive] if web_archive else [])
    for name in extra_assets:
        if not isinstance(name, str) or Path(name).name != name:
            raise RuntimeError('Invalid release metadata asset name')
    subprocess.check_call([
        'gh', 'release', 'download', tag, '-R', repo, '--dir', str(output),
        *[arg for name in extra_assets for arg in ['--pattern', name]],
    ])
    if tag != 'v' + version:
        raise RuntimeError('Release tag and build version differ')
    web = json.loads(gh('api', f'repos/{repo.rsplit("/", 1)[0]}/cinny/commits/{tag}'))
    if web['sha'] != info['web_commit']:
        raise RuntimeError('Web tag is not the source of these binaries')

    checksums = {}
    for line in (output / 'SHA256SUMS.txt').read_text().splitlines():
        sha, name = line.split('  ', 1)
        if Path(name).name != name or len(sha) != 64:
            raise RuntimeError('Invalid checksum manifest entry')
        checksums[name] = 'sha256:' + sha
    for name in ['build-info.json', *extra_assets]:
        if digest(output / name) != checksums[name]:
            raise RuntimeError(f'Metadata checksum mismatch: {name}')

    platforms = [
        ('windows', info['windows_validation_run'], 'cinny-desktop-windows', [
            f'Cinny_{version}_x64-setup.exe', f'Cinny_{version}_x64_en-US.msi']),
        ('linux', info['linux_ci_run'], f'cinny-{tag}-linux-x86_64', [
            f'Cinny_{version}_amd64.deb', f'Cinny_{version}_amd64.AppImage']),
        ('android', info['android_ci_run'], f'cinny-{version}-android-preview', [
            f'Cinny_{version}_android_preview.apk', f'Cinny_{version}_android-release.aab']),
        ('macos', info['macos_ci_run'], f'cinny-{tag}-macos-universal', [
            f'Cinny_{version}_universal.dmg', f'Cinny_{version}_universal.app.zip']),
    ]
    aliases = {
        f'Cinny_{version}_android_preview.apk': f'Cinny_{version}_android-preview.apk',
        f'Cinny_{version}_android-release.aab': 'app-universal-release.aab',
    }
    packages = []
    for platform, run, artifact, names in platforms:
        target = Path('release-downloads') / platform
        print(f'Downloading verified {platform} build from run {run}', flush=True)
        subprocess.check_call([
            'gh', 'run', 'download', str(run), '-R', repo,
            '--name', artifact, '--dir', str(target),
        ])
        for marker, expected in [('desktop-source.txt', info['desktop_commit']),
                                 ('web-source.txt', info['web_commit'])]:
            markers = list(target.rglob(marker))
            if len(markers) != 1 or source_marker(markers[0]) != expected:
                raise RuntimeError(f'{platform} source mismatch: {marker}')
        for name in names:
            matches = list(target.rglob(aliases.get(name, name)))
            if len(matches) != 1 or digest(matches[0]) != checksums[name]:
                raise RuntimeError(f'Package missing or checksum mismatch: {name}')
            shutil.copy2(matches[0], output / name)
            packages.append(output / name)

    # Create the desktop tag only after every platform package has passed
    # verification. Actions' GITHUB_TOKEN prevents redundant tag-triggered builds.
    lookup = subprocess.run(['gh', 'api', f'repos/{repo}/git/ref/tags/{tag}'],
                            capture_output=True, text=True)
    if lookup.returncode:
        if '404' not in lookup.stderr:
            raise RuntimeError('Could not check the release tag: ' + lookup.stderr)
        tagged_object = json.loads(gh('api', f'repos/{repo}/git/tags', '--method', 'POST',
                                      '-f', 'tag=' + tag, '-f', 'message=Cinny ' + version,
                                      '-f', 'object=' + info['desktop_commit'], '-f', 'type=commit'))
        gh('api', f'repos/{repo}/git/refs', '--method', 'POST',
           '-f', 'ref=refs/tags/' + tag, '-f', 'sha=' + tagged_object['sha'])
    tagged = json.loads(gh('api', f'repos/{repo}/commits/{tag}'))
    if tagged['sha'] != info['desktop_commit']:
        raise RuntimeError('Desktop tag is not the source of these binaries')

    # Verify every package before modifying any release assets.
    for package in packages:
        existing = next((a for a in release['assets'] if a['name'] == package.name), None)
        if existing and existing['state'] == 'uploaded' and existing.get('digest') == checksums[package.name]:
            print(f'Already verified: {package.name}', flush=True)
            continue
        print(f'Uploading: {package.name}', flush=True)
        subprocess.check_call(['gh', 'release', 'upload', tag, '-R', repo, '--clobber', str(package)])

    actual = json.loads(gh('api', f'repos/{repo}/releases/{release["id"]}'))
    expected_names = set(checksums) | {'SHA256SUMS.txt'}
    if {a['name'] for a in actual['assets']} != expected_names:
        raise RuntimeError('Release asset set differs from the manifest')
    for asset in actual['assets']:
        path = output / asset['name']
        if asset['state'] != 'uploaded' or asset['size'] != path.stat().st_size or asset.get('digest') != digest(path):
            raise RuntimeError(f'Uploaded asset mismatch: {asset["name"]}')
    print(f'All {len(expected_names)} release assets verified; draft is ready to publish.', flush=True)


if __name__ == '__main__':
    main()
