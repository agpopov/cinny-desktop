"""Align and sign APKs with all Android signature schemes and the pinned release key."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--keystore', type=Path, required=True)
    parser.add_argument('--alias', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    sdk = Path(os.environ['ANDROID_HOME']) / 'build-tools' / '36.0.0'
    expected = (root / 'android-signing-certificate.sha256').read_text().strip().lower()
    version = json.loads((root / 'package.json').read_text())['version']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    def run(tool, *command):
        result = subprocess.run(
            [str(sdk / tool), *map(str, command)], capture_output=True, text=True,
        )
        if result.returncode:
            raise RuntimeError(f'{tool} failed: {result.stdout}{result.stderr}')
        return result.stdout

    with tempfile.TemporaryDirectory(prefix='cinny-apk-', dir=args.output.parent) as temporary:
        staged = Path(temporary) / 'release.apk'
        run('zipalign', '-P', '16', '-f', '4', args.input, staged)
        run('apksigner', 'sign', '--ks', args.keystore, '--ks-key-alias', args.alias,
            '--ks-pass', 'env:CINNY_ANDROID_STORE_PASSWORD',
            '--key-pass', 'env:CINNY_ANDROID_KEY_PASSWORD',
            '--v1-signing-enabled', 'true', '--v2-signing-enabled', 'true',
            '--v3-signing-enabled', 'true', '--v4-signing-enabled', 'false', staged)
        signatures = {}
        # API 23 forces verification of the JAR signature independently of v2/v3.
        # This does not change the APK's minimum supported Android version.
        for api, scheme in ((23, 'v1'), (26, 'v2'), (33, 'v3')):
            output = run('apksigner', 'verify', '--verbose', '--print-certs',
                         '--min-sdk-version', api, '--max-sdk-version', api, staged)
            assert re.search(rf'Verified using {scheme} scheme .*: true', output), output
            certificate = re.search(r'Signer #1 certificate SHA-256 digest: ([0-9a-f]+)', output).group(1)
            assert certificate == expected, 'Android signing identity mismatch'
            signatures[scheme] = {'verification_api': api, 'verified': True}
        run('zipalign', '-c', '-P', '16', '4', staged)
        badging = run('aapt', 'dump', 'badging', staged)
        assert "package: name='in.cinny.app'" in badging
        assert f"versionName='{version}'" in badging
        major, minor, patch = map(int, version.split('.'))
        assert f"versionCode='{major * 1000000 + minor * 1000 + patch}'" in badging
        with zipfile.ZipFile(staged) as archive:
            abis = sorted({name.split('/')[1] for name in archive.namelist() if name.startswith('lib/')})
            assert abis and set(abis) <= {'arm64-v8a', 'armeabi-v7a', 'x86_64'}
            assert archive.testzip() is None
            # Signing must preserve every application payload entry byte-for-byte.
            with zipfile.ZipFile(args.input) as original:
                for name in original.namelist():
                    signature_entry = name.upper() == 'META-INF/MANIFEST.MF' or (
                        name.upper().startswith('META-INF/') and
                        name.upper().endswith(('.SF', '.RSA', '.DSA', '.EC'))
                    )
                    if not signature_entry:
                        assert archive.read(name) == original.read(name), name
        shutil.copyfile(staged, args.output)

    report = {
        'file': args.output.name, 'version': version, 'abis': abis,
        'certificate_sha256': expected, 'signature_schemes': signatures,
        'zip_alignment': 16384, 'payload_preserved': True,
        'bytes': args.output.stat().st_size,
        'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
    }
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
