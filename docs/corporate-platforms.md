# Corporate Cinny 4.12.10 platform builds

The Windows v4.12.10 release tag remains immutable. Platform support is built
from the corporate branch after that tag. Every artifact includes the exact
desktop and web source commit IDs and SHA256 checksums. The displayed app
version is still 4.12.10. Do not replace tags to make platform history match.

## Packages and current deployment status

| Platform | Packages | Status |
| --- | --- | --- |
| Windows | Existing MSI / EXE | Existing v4.12.10 release |
| Linux x86_64 | DEB / AppImage | Pilot builds; native launch checks |
| macOS Intel + Apple Silicon | Universal DMG / app ZIP | Ad-hoc signature; company Developer ID and notarization required before broad deployment |
| Android 8+ | Release unsigned APK / AAB; debug APK | Preview; permanent signing identity and background push required before broad deployment |

Linux close-to-tray defaults to disabled because GNOME installations may have
no tray extension. A missing tray cannot make the application invisible.
macOS Dock reopen restores the main window. Native macOS and Linux notification
actions carry the room, event and thread destination. Notification sound is
controlled by the web client to avoid duplicate audio.

Android asks for native notification permission. A denied permission is explained
in settings; retryable permissions can be requested again. Notification intents
are retained until the authenticated UI consumes them, including cold launch.
Safe-area padding and a resizing viewport accommodate narrow screens and keyboards.

**Android notifications currently require a running app.** There is no Firebase,
UnifiedPush or Matrix push gateway configured. Android can suspend or kill the
WebView, so a debug APK is not a reliable background messenger. Do not roll this
preview out as the company's primary mobile client.

## Reproducible builds

Dispatch `Corp macOS and Linux build` (`platform-build.yml`) or
`Corp Android build and emulator checks` (`android-build.yml`) on the corporate
branch with an explicit source commit. The workflow resolves even a branch input
to a full SHA before checkout and pins the web submodule. Android's generated
native project is archived with the artifacts; the pinned Tauri CLI generates it
and `scripts/prepare-android.mjs` applies version 4.12.10 / code 4012010 and copies
the versioned `src-tauri/android/CorpPlugin.kt` into it. The optimized preview
APK uses the disposable debug key; unsigned release APK/AAB files are also kept.
The emulator launches both the optimized preview and the debug test build.

No production signing keys are generated automatically. Keep private keys and
passwords in CI secrets or the organization's signing service, never in git.
Use one durable Android application signing key from the first production
installation: updates must preserve both application ID and signing identity.
Unsigned release artifacts must be signed before installation or distribution.
Debug APKs use disposable debug signing and cannot be upgraded in place to a
production-signed APK without uninstalling them.

The native updater is disabled to prevent upstream packages from replacing this
corporate build. Choose MDM or an internal distribution/update process before
fleet rollout. Windows and Android use the current application ID `in.cinny.app`;
check for existing upstream installations before migration.

## Validation and rollout gates

Automated regression tests cover unread receipts, thread navigation, notification
filtering and target navigation. `tests/e2e/encryption_smoke.py` exchanges
encrypted messages between two devices, checks that the server receives no
plaintext message events and verifies decrypted history after reload. `tests/e2e/platform_ui.py` covers Chromium,
Firefox and WebKit at desktop, tablet and narrow mobile sizes, including reduced
viewport height, message sending, permission denial/retry and cold-start target
handling. These tests use disposable local Synapse accounts, never company users.

The Linux/macOS workflow launches the actual packaged app and records a screenshot.
It verifies macOS universal architectures and signature integrity. These checks do
not prove macOS Notification Center authorization or delivery with a production
Developer ID. `tests/e2e/android_native.py` exercises the installed debug APK's
real permission and notification plugin, Android notification records, warm/cold
intent handling and consume-once behavior in an emulator. It does not emulate
server push delivery or prove OEM battery-management behavior.

Before broad rollout:

1. Configure Developer ID signing and notarization for Mac; test a clean Mac with
   quarantine enabled, notification permission, Dock reopen and OS updates.
2. Select Android distribution (MDM/direct APK/managed Play), retain a permanent
   signing key, and connect Matrix push to FCM or an agreed UnifiedPush provider.
3. Verify push privacy and encrypted-room behavior, then test foreground,
   background, force-stop, reboot, offline recovery, permission denial, Do Not
   Disturb, battery saver and notification tap into rooms/threads on real devices.
4. Pilot with representative users on Windows, Intel/Apple Silicon Mac,
   Ubuntu/GNOME/KDE and company Android devices. Check proxy/SSO, attachments,
   encrypted history, session verification, screen/keyboard scaling and upgrades.
5. Expand distribution only after pilot feedback and a documented rollback path.
