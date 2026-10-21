fn main() {
    // Команды десктопной интеграции (src/desktop.rs). Для них генерируются
    // права allow-<команда>, которые выдаются в capabilities/corp-desktop.json.
    // Без этого Tauri 2.11+ блокирует вызовы со страницы http://localhost:44548.
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(&[
            "desktop_notify",
            "desktop_set_unread",
            "desktop_set_dnd",
            "desktop_get_settings",
            "desktop_set_settings",
            "desktop_show_window",
            "native_notification_permission",
            "mobile_take_notification_target",
        ])),
    )
    .expect("failed to run tauri-build");
}
