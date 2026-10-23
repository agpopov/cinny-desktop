import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, mkdir, copyFile, readFile, writeFile, rm, cp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';

test('fresh Android preparation declares call permissions and remains idempotent', async () => {
  const root = await mkdtemp(join(tmpdir(), 'cinny-android-manifest-'));
  const app = join(root, 'src-tauri/gen/android/app');
  try {
    await mkdir(join(root, 'scripts'), { recursive: true });
    await mkdir(join(root, 'src-tauri/android'), { recursive: true });
    await mkdir(join(app, 'src/main'), { recursive: true });
    await copyFile(new URL('../scripts/prepare-android.mjs', import.meta.url), join(root, 'scripts/prepare-android.mjs'));
    for (const name of ['MainActivity.kt', 'CorpPlugin.kt', 'ic_stat_cinny.xml']) {
      await copyFile(new URL('../src-tauri/android/' + name, import.meta.url), join(root, 'src-tauri/android', name));
    }
    await cp(new URL('../src-tauri/android/res/', import.meta.url), join(root, 'src-tauri/android/res'), { recursive: true });
    await writeFile(join(root, 'package.json'), JSON.stringify({ version: '4.13.0' }));
    await writeFile(join(app, 'build.gradle.kts'), 'getProperty("tauri.android.versionCode", "1")\ngetProperty("tauri.android.versionName", "1.0.0")\n    buildTypes {\n');
    await writeFile(join(app, 'src/main/AndroidManifest.xml'), '<manifest xmlns:android="http://schemas.android.com/apk/res/android"><uses-permission android:name="android.permission.INTERNET" /><application /></manifest>');
    const prepare = () => execFileSync(process.execPath, [join(root, 'scripts/prepare-android.mjs')], { stdio: 'pipe' });
    prepare();
    const first = await readFile(join(app, 'src/main/AndroidManifest.xml'), 'utf8');
    prepare();
    assert.equal(await readFile(join(app, 'src/main/AndroidManifest.xml'), 'utf8'), first);
    for (const permission of ['INTERNET', 'CAMERA', 'RECORD_AUDIO', 'MODIFY_AUDIO_SETTINGS']) {
      assert.equal(first.split(`android:name="android.permission.${permission}"`).length - 1, 1);
    }
    for (const feature of ['camera', 'camera.autofocus', 'microphone']) {
      assert.ok(first.includes(`android:name="android.hardware.${feature}" android:required="false"`));
    }
    assert.match(first, /android:icon="@mipmap\/ic_launcher"/);
    assert.match(first, /android:roundIcon="@mipmap\/ic_launcher_round"/);
    for (const name of ['ic_launcher', 'ic_launcher_round']) {
      const adaptive = await readFile(join(app, `src/main/res/mipmap-anydpi-v26/${name}.xml`), 'utf8');
      assert.match(adaptive, /<adaptive-icon/);
      assert.match(adaptive, /@drawable\/ic_cinny_foreground/);
      const themed = await readFile(join(app, `src/main/res/mipmap-anydpi-v33/${name}.xml`), 'utf8');
      assert.match(themed, /<monochrome android:drawable="@drawable\/ic_cinny_foreground"/);
      assert.match(await readFile(join(app, `src/main/res/mipmap-anydpi/${name}.xml`), 'utf8'), /<vector/);
    }
    const foreground = await readFile(join(app, 'src/main/res/drawable/ic_cinny_foreground.xml'), 'utf8');
    assert.match(foreground, /viewportWidth="108"/);
    assert.match(foreground, /fillType="evenOdd"/);
    const metadata = await readFile(join(app, 'tauri.properties'), 'utf8');
    assert.match(metadata, /versionCode=4013000/);
    assert.match(metadata, /versionName=4\.13\.0/);
    const nativeActivity = await readFile(join(app, 'src/main/java/in/cinny/app/MainActivity.kt'), 'utf8');
    assert.equal(nativeActivity, await readFile(new URL('../src-tauri/android/MainActivity.kt', import.meta.url), 'utf8'));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
