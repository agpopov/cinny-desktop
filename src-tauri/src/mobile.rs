//! Mobile shell: native permissions, notification actions and a single app window.
use serde::Deserialize;
use tauri::plugin::{PermissionState, PluginHandle};
use tauri::{AppHandle, Manager, Wry};

struct MobileBridge(PluginHandle<Wry>);
use tauri_plugin_notification::NotificationExt;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Target {
    room_id: String,
    event_id: Option<String>,
    thread_id: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct NotifyPayload {
    title: String,
    body: String,
    target: Target,
}

#[tauri::command]
async fn desktop_notify(app: AppHandle<Wry>, payload: NotifyPayload) -> Result<(), String> {
    let notifications = app.notification();
    if !matches!(
        notifications
            .permission_state()
            .map_err(|e| e.to_string())?,
        tauri::plugin::PermissionState::Granted
    ) {
        return Err("Разрешите уведомления в настройках приложения".to_string());
    }
    app.state::<MobileBridge>()
        .0
        .run_mobile_plugin::<serde_json::Value>(
            "showMessage",
            serde_json::json!({
                "title": payload.title,
                "body": payload.body,
                "target": {
                    "roomId": payload.target.room_id,
                    "eventId": payload.target.event_id,
                    "threadId": payload.target.thread_id,
                },
            }),
        )
        .map(|_| ())
        .map_err(|e| e.to_string())
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct UnreadPayload {
    total: u32,
    #[serde(default)]
    has_unread: bool,
}

#[tauri::command]
async fn desktop_set_unread(app: AppHandle<Wry>, payload: UnreadPayload) {
    if payload.total == 0 && !payload.has_unread {
        let _ = app.notification().remove_all_active();
    }
}

#[tauri::command]
fn desktop_set_dnd(active: bool) {
    let _ = active;
}

#[tauri::command]
fn desktop_show_window(app: AppHandle<Wry>) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.set_focus();
    }
}

#[tauri::command]
async fn native_notification_permission(
    app: AppHandle<Wry>,
    request: bool,
) -> Result<PermissionState, String> {
    let result = if request {
        app.notification().request_permission()
    } else {
        app.notification().permission_state()
    };
    result.map_err(|error| error.to_string())
}

#[tauri::command]
async fn mobile_take_notification_target(app: AppHandle<Wry>) -> Result<serde_json::Value, String> {
    app.state::<MobileBridge>()
        .0
        .run_mobile_plugin("takeNotificationTarget", ())
        .map_err(|error| error.to_string())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(
            tauri::plugin::Builder::<Wry, ()>::new("corp-mobile")
                .setup(|app, api| {
                    let plugin = api.register_android_plugin("in.cinny.app", "CorpPlugin")?;
                    app.manage(MobileBridge(plugin));
                    Ok(())
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            desktop_notify,
            desktop_set_unread,
            desktop_set_dnd,
            desktop_show_window,
            native_notification_permission,
            mobile_take_notification_target
        ])
        .run(tauri::generate_context!())
        .expect("error while running Cinny mobile");
}
