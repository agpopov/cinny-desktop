# Wry 0.55.1: main-frame-only initialization

This is the unchanged, locked crates.io Wry 0.55.1 source plus a narrow backport
of [tauri-apps/wry#1874](https://github.com/tauri-apps/wry/pull/1874), reviewed from
head `c839fec0cf1088f0721fa27ab6112dbe37378924`. That upstream PR is still open.
The Apache-2.0/MIT licenses and notices are preserved in `wry/`.

WebView2 ignores the main-frame-only flag. This caused Tauri's path plugin to
throw during a sandboxed video frame's initialization. Android document-start
injection needs the same scope check. The backport:

- Guards main-frame-only scripts and the Windows IPC helper with
  `if (window === window.top)`, preserving a leading strict-mode directive.
- Leaves explicitly shared scripts unchanged.
- Excludes main-frame-only scripts from Android subframe HTML in the fallback
  custom-protocol injection path.

The JNI changes are adapted to 0.55.1; no crate versions are upgraded. Linux and
macOS use their existing implementation. Remove the patch when the locked Wry
release incorporates an equivalent fix and the native regression checks pass.

Run the five standalone Rust guard tests without GUI dependencies:

```sh
rustc --test tests/wry_frame_guard.rs -o /tmp/wry-frame-tests
/tmp/wry-frame-tests
```

Windows and Android native tests also require the external frame to decode real
video, keep the app's origin-only Referer, expose no Tauri bridge, and leave the
main window's settings/notification IPC working.
