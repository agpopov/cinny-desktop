//! Десктопная интеграция: нативные уведомления Windows, трей, автозапуск,
//! счётчик непрочитанных на панели задач и режим «Не беспокоить».
//!
//! Веб-часть (cinny) вызывает команды `desktop_*` через IPC и слушает события
//! `desktop://*`. Источник правды для режима «Не беспокоить» — веб-настройки,
//! трей лишь отражает состояние и отправляет запросы на его смену.

use std::{
    collections::hash_map::DefaultHasher,
    fs,
    hash::{Hash, Hasher},
    path::PathBuf,
    sync::Mutex,
};

use base64::Engine;
use serde::{Deserialize, Serialize};
use tauri::{
    image::Image,
    menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu},
    tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager, Runtime, UserAttentionType, WebviewWindow, Wry,
};
use tauri_plugin_autostart::ManagerExt as AutostartExt;

pub const MAIN_WINDOW: &str = "main";
pub const MINIMIZED_ARG: &str = "--minimized";

const SETTINGS_FILE: &str = "desktop-settings.json";

const EVT_NOTIFICATION_CLICK: &str = "desktop://notification-click";
const EVT_DND: &str = "desktop://dnd";
const EVT_OPEN_SETTINGS: &str = "desktop://open-settings";

const TRAY_DEFAULT: &[u8] = include_bytes!("../icons/tray/tray-default.png");
const TRAY_UNREAD: &[u8] = include_bytes!("../icons/tray/tray-unread.png");
const TRAY_HIGHLIGHT: &[u8] = include_bytes!("../icons/tray/tray-highlight.png");
const TRAY_DND: &[u8] = include_bytes!("../icons/tray/tray-dnd.png");
#[cfg(target_os = "windows")]
const APP_ICON_PNG: &[u8] = include_bytes!("../icons/128x128.png");

/// Настройки, которые живут только в десктопной обёртке.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct DesktopSettings {
    /// Крестик окна прячет приложение в трей, а не завершает его.
    pub close_to_tray: bool,
    /// При автозапуске стартовать свёрнутым в трей.
    pub start_minimized: bool,
}

impl Default for DesktopSettings {
    fn default() -> Self {
        Self {
            close_to_tray: cfg!(any(target_os = "windows", target_os = "macos")),
            start_minimized: cfg!(target_os = "windows"),
        }
    }
}

/// То, что веб-часть получает из `desktop_get_settings`.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopSettingsView {
    pub close_to_tray: bool,
    pub start_minimized: bool,
    pub autostart: bool,
    pub platform: &'static str,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopSettingsPatch {
    pub close_to_tray: Option<bool>,
    pub start_minimized: Option<bool>,
    pub autostart: Option<bool>,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
enum TrayKind {
    #[default]
    Default,
    Unread,
    Highlight,
    Dnd,
}

#[derive(Default)]
struct TrayMenuItems {
    dnd_off: Option<CheckMenuItem<Wry>>,
    dnd_on: Option<CheckMenuItem<Wry>>,
    autostart: Option<CheckMenuItem<Wry>>,
    close_to_tray: Option<CheckMenuItem<Wry>>,
}

#[derive(Default)]
pub struct DesktopState {
    settings: Mutex<DesktopSettings>,
    tray: Mutex<Option<TrayIcon<Wry>>>,
    tray_kind: Mutex<TrayKind>,
    menu: Mutex<TrayMenuItems>,
    dnd: Mutex<bool>,
    unread_total: Mutex<u32>,
}

fn settings_path<R: Runtime>(app: &AppHandle<R>) -> Option<PathBuf> {
    app.path()
        .app_config_dir()
        .ok()
        .map(|dir| dir.join(SETTINGS_FILE))
}

pub fn load_settings<R: Runtime>(app: &AppHandle<R>) -> DesktopSettings {
    settings_path(app)
        .and_then(|p| fs::read_to_string(p).ok())
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save_settings<R: Runtime>(app: &AppHandle<R>, settings: &DesktopSettings) {
    if let Some(path) = settings_path(app) {
        if let Some(dir) = path.parent() {
            let _ = fs::create_dir_all(dir);
        }
        if let Ok(json) = serde_json::to_string_pretty(settings) {
            let _ = fs::write(path, json);
        }
    }
}

/// Показать главное окно и вывести его на передний план.
pub fn show_main_window<R: Runtime>(app: &AppHandle<R>) {
    if let Some(window) = app.get_webview_window(MAIN_WINDOW) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn toggle_main_window<R: Runtime>(app: &AppHandle<R>) {
    if let Some(window) = app.get_webview_window(MAIN_WINDOW) {
        let visible = window.is_visible().unwrap_or(false);
        let minimized = window.is_minimized().unwrap_or(false);
        let focused = window.is_focused().unwrap_or(false);
        if visible && !minimized && focused {
            let _ = window.hide();
        } else {
            show_main_window(app);
        }
    }
}

/// Перехват закрытия окна: прячем в трей, если так настроено.
pub fn is_tray_available<R: Runtime>(app: &AppHandle<R>) -> bool {
    app.state::<DesktopState>().tray.lock().map(|tray| tray.is_some()).unwrap_or(false)
}

pub fn should_hide_on_close<R: Runtime>(app: &AppHandle<R>) -> bool {
    if !is_tray_available(app) { return false; }
    app.state::<DesktopState>()
        .settings
        .lock()
        .map(|s| s.close_to_tray)
        .unwrap_or(true)
}

fn tray_image(kind: TrayKind) -> Option<Image<'static>> {
    let bytes = match kind {
        TrayKind::Default => TRAY_DEFAULT,
        TrayKind::Unread => TRAY_UNREAD,
        TrayKind::Highlight => TRAY_HIGHLIGHT,
        TrayKind::Dnd => TRAY_DND,
    };
    Image::from_bytes(bytes).ok()
}

fn update_tray(app: &AppHandle<Wry>) {
    let state = app.state::<DesktopState>();
    let dnd = state.dnd.lock().map(|d| *d).unwrap_or(false);
    let total = state.unread_total.lock().map(|t| *t).unwrap_or(0);
    let kind = *state.tray_kind.lock().unwrap();
    let kind = if dnd { TrayKind::Dnd } else { kind };

    let tooltip = match (dnd, total) {
        (true, 0) => "Cinny — не беспокоить".to_string(),
        (true, n) => format!("Cinny — не беспокоить, непрочитанных: {n}"),
        (false, 0) if matches!(kind, TrayKind::Unread | TrayKind::Highlight) => {
            "Cinny — есть непрочитанные сообщения".to_string()
        }
        (false, 0) => "Cinny".to_string(),
        (false, n) => format!("Cinny — непрочитанных: {n}"),
    };

    if let Some(tray) = state.tray.lock().unwrap().as_ref() {
        let _ = tray.set_icon(tray_image(kind));
        let _ = tray.set_tooltip(Some(tooltip));
    }

    let menu = state.menu.lock().unwrap();
    if let Some(item) = &menu.dnd_off {
        let _ = item.set_checked(!dnd);
    }
    if let Some(item) = &menu.dnd_on {
        let _ = item.set_checked(dnd);
    }
}

fn sync_menu_checks(app: &AppHandle<Wry>) {
    let state = app.state::<DesktopState>();
    let close_to_tray = state.settings.lock().map(|s| s.close_to_tray).unwrap_or(true);
    let autostart = app.autolaunch().is_enabled().unwrap_or(false);
    let menu = state.menu.lock().unwrap();
    if let Some(item) = &menu.autostart {
        let _ = item.set_checked(autostart);
    }
    if let Some(item) = &menu.close_to_tray {
        let _ = item.set_checked(close_to_tray);
    }
}

pub fn setup_tray(app: &AppHandle<Wry>) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Открыть Cinny", true, None::<&str>)?;

    let dnd_off = CheckMenuItem::with_id(app, "dnd-off", "Выключен", true, true, None::<&str>)?;
    let dnd_1h = MenuItem::with_id(app, "dnd-1h", "На 1 час", true, None::<&str>)?;
    let dnd_8h = MenuItem::with_id(app, "dnd-8h", "На 8 часов", true, None::<&str>)?;
    let dnd_tomorrow =
        MenuItem::with_id(app, "dnd-tomorrow", "До завтра (9:00)", true, None::<&str>)?;
    let dnd_on = CheckMenuItem::with_id(
        app,
        "dnd-forever",
        "Пока не выключу",
        true,
        false,
        None::<&str>,
    )?;
    let dnd_menu = Submenu::with_items(
        app,
        "Не беспокоить",
        true,
        &[
            &dnd_off,
            &PredefinedMenuItem::separator(app)?,
            &dnd_1h,
            &dnd_8h,
            &dnd_tomorrow,
            &dnd_on,
        ],
    )?;

    let autostart = CheckMenuItem::with_id(
        app,
        "autostart",
        "Запускать вместе с Windows",
        true,
        app.autolaunch().is_enabled().unwrap_or(false),
        None::<&str>,
    )?;
    let close_to_tray = CheckMenuItem::with_id(
        app,
        "close-to-tray",
        "Сворачивать в трей при закрытии",
        true,
        should_hide_on_close(app),
        None::<&str>,
    )?;
    let notif_settings = MenuItem::with_id(
        app,
        "open-settings",
        "Настройки уведомлений…",
        true,
        None::<&str>,
    )?;
    let quit = MenuItem::with_id(app, "quit", "Выход", true, None::<&str>)?;

    let menu = Menu::with_items(
        app,
        &[
            &open,
            &PredefinedMenuItem::separator(app)?,
            &dnd_menu,
            &notif_settings,
            &PredefinedMenuItem::separator(app)?,
            &autostart,
            &close_to_tray,
            &PredefinedMenuItem::separator(app)?,
            &quit,
        ],
    )?;

    let tray = TrayIconBuilder::with_id("main-tray")
        .icon(tray_image(TrayKind::Default).expect("tray icon"))
        .tooltip("Cinny")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => show_main_window(app),
            "quit" => app.exit(0),
            "open-settings" => {
                show_main_window(app);
                let _ = app.emit_to(MAIN_WINDOW, EVT_OPEN_SETTINGS, ());
            }
            "dnd-off" => {
                let _ = app.emit_to(MAIN_WINDOW, EVT_DND, "off");
                // реальное состояние вернёт веб-часть через desktop_set_dnd
                update_tray(app);
            }
            "dnd-1h" => {
                let _ = app.emit_to(MAIN_WINDOW, EVT_DND, "1h");
            }
            "dnd-8h" => {
                let _ = app.emit_to(MAIN_WINDOW, EVT_DND, "8h");
            }
            "dnd-tomorrow" => {
                let _ = app.emit_to(MAIN_WINDOW, EVT_DND, "tomorrow");
            }
            "dnd-forever" => {
                let _ = app.emit_to(MAIN_WINDOW, EVT_DND, "forever");
                update_tray(app);
            }
            "autostart" => {
                let launcher = app.autolaunch();
                let enabled = launcher.is_enabled().unwrap_or(false);
                let _ = if enabled {
                    launcher.disable()
                } else {
                    launcher.enable()
                };
                sync_menu_checks(app);
            }
            "close-to-tray" => {
                let state = app.state::<DesktopState>();
                let mut settings = state.settings.lock().unwrap();
                settings.close_to_tray = !settings.close_to_tray;
                save_settings(app, &settings);
                drop(settings);
                sync_menu_checks(app);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                toggle_main_window(tray.app_handle());
            }
        })
        .build(app)?;

    let state = app.state::<DesktopState>();
    *state.tray.lock().unwrap() = Some(tray);
    *state.menu.lock().unwrap() = TrayMenuItems {
        dnd_off: Some(dnd_off),
        dnd_on: Some(dnd_on),
        autostart: Some(autostart),
        close_to_tray: Some(close_to_tray),
    };
    Ok(())
}

pub fn init_state(app: &AppHandle<Wry>) {
    let settings = load_settings(app);
    app.manage(DesktopState {
        settings: Mutex::new(settings),
        ..Default::default()
    });
}

// ---------------------------------------------------------------------------
// Windows: регистрация AppUserModelID, чтобы тосты работали и без ярлыка
// ---------------------------------------------------------------------------

#[cfg(target_os = "windows")]
pub fn register_app_user_model_id<R: Runtime>(app: &AppHandle<R>) {
    use windows::core::HSTRING;
    use windows::Win32::UI::Shell::SetCurrentProcessExplicitAppUserModelID;
    use winreg::{enums::HKEY_CURRENT_USER, RegKey};

    let identifier = app.config().identifier.clone();
    let product = app
        .config()
        .product_name
        .clone()
        .unwrap_or_else(|| "Cinny".to_string());

    unsafe {
        let _ = SetCurrentProcessExplicitAppUserModelID(&HSTRING::from(identifier.as_str()));
    }

    // Иконка для заголовка тоста.
    let icon_path = app
        .path()
        .app_data_dir()
        .ok()
        .map(|dir| dir.join("notification-app-icon.png"));
    if let Some(path) = &icon_path {
        if !path.exists() {
            if let Some(dir) = path.parent() {
                let _ = fs::create_dir_all(dir);
            }
            let _ = fs::write(path, APP_ICON_PNG);
        }
    }

    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    if let Ok((key, _)) =
        hkcu.create_subkey(format!("Software\\Classes\\AppUserModelId\\{identifier}"))
    {
        let _ = key.set_value("DisplayName", &product);
        if let Some(path) = &icon_path {
            let _ = key.set_value("IconUri", &path.display().to_string());
        }
    }
}

#[cfg(not(target_os = "windows"))]
pub fn register_app_user_model_id<R: Runtime>(_app: &AppHandle<R>) {}

// ---------------------------------------------------------------------------
// Команды для веб-части
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NotificationTarget {
    pub room_id: String,
    pub event_id: Option<String>,
    pub thread_id: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NotifyPayload {
    pub title: String,
    pub body: String,
    /// Подпись под текстом (например, название пространства).
    pub attribution: Option<String>,
    /// PNG/JPEG в base64 (без префикса data:).
    pub icon: Option<String>,
    /// Ключ кэша иконки (например, mxc-адрес аватара).
    pub icon_key: Option<String>,
    pub target: NotificationTarget,
}

fn cache_icon<R: Runtime>(app: &AppHandle<R>, key: &str, b64: &str) -> Option<PathBuf> {
    let mut hasher = DefaultHasher::new();
    key.hash(&mut hasher);
    let dir = app.path().app_cache_dir().ok()?.join("notification-icons");
    let path = dir.join(format!("{:016x}.png", hasher.finish()));
    if path.exists() {
        return Some(path);
    }
    let bytes = base64::engine::general_purpose::STANDARD.decode(b64).ok()?;
    fs::create_dir_all(&dir).ok()?;
    fs::write(&path, bytes).ok()?;
    Some(path)
}

fn emit_notification_click<R: Runtime>(app: &AppHandle<R>, target: NotificationTarget) {
    show_main_window(app);
    let _ = app.emit_to(MAIN_WINDOW, EVT_NOTIFICATION_CLICK, target);
}

#[cfg(target_os = "windows")]
fn show_native_notification(app: &AppHandle<Wry>, payload: NotifyPayload) -> Result<(), String> {
    use tauri_winrt_notification::{IconCrop, Toast};

    let identifier = app.config().identifier.clone();
    let icon_path = match (&payload.icon, &payload.icon_key) {
        (Some(icon), Some(key)) => cache_icon(app, key, icon),
        (Some(icon), None) => cache_icon(app, icon, icon),
        _ => None,
    };

    let handle = app.clone();
    let target = payload.target.clone();
    let mut toast = Toast::new(&identifier)
        .title(&payload.title)
        .text1(&payload.body)
        // звук проигрывает веб-часть, чтобы работали настройки громкости и DND
        .sound(None)
        .on_activated(move |_action| {
            let handle = handle.clone();
            let target = target.clone();
            let main = handle.clone();
            let _ = main.run_on_main_thread(move || emit_notification_click(&handle, target));
            Ok(())
        });

    if let Some(attribution) = payload.attribution.as_deref().filter(|a| !a.is_empty()) {
        toast = toast.text2(attribution);
    }
    if let Some(path) = icon_path {
        toast = toast.icon(&path, IconCrop::Circular, "");
    }

    toast.show().map_err(|e| e.to_string())
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
fn show_native_notification(app: &AppHandle<Wry>, payload: NotifyPayload) -> Result<(), String> {
    use notify_rust::{Notification, NotificationResponse};
    let mut notification = Notification::new();
    notification.appname("Cinny").summary(&payload.title).body(&payload.body)
        .action("default", "Открыть");
    #[cfg(target_os = "linux")]
    {
        let body = payload.body.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;");
        notification.body(&body)
            .hint(notify_rust::Hint::SuppressSound(true))
            .hint(notify_rust::Hint::DesktopEntry(app.config().identifier.clone()));
    }
    #[cfg(target_os = "macos")]
    {
        let _ = notify_rust::set_application(&app.config().identifier);
        if let Some(attribution) = &payload.attribution { notification.subtitle(attribution); }
    }
    if let Some(icon) = &payload.icon {
        let key = payload.icon_key.as_deref().unwrap_or(icon);
        if let Some(path) = cache_icon(app, key, icon) {
            notification.image_path(&path.to_string_lossy());
        }
    }
    let handle = notification.show().map_err(|e| e.to_string())?;
    let app = app.clone();
    std::thread::spawn(move || {
        let result = handle.wait_for_response(|response: &NotificationResponse| {
            let clicked = matches!(response, NotificationResponse::Default)
                || matches!(response, NotificationResponse::Action(action) if action == "default");
            if clicked {
                let target = payload.target.clone();
                let handle = app.clone();
                let _ = app.run_on_main_thread(move || emit_notification_click(&handle, target));
            }
        });
        if let Err(error) = result { eprintln!("Notification response failed: {error}"); }
    });
    Ok(())
}

/// Показать нативное уведомление о сообщении.
#[tauri::command]
pub async fn desktop_notify(app: AppHandle<Wry>, payload: NotifyPayload) -> Result<(), String> {
    show_native_notification(&app, payload)?;
    flash_window(&app);
    Ok(())
}

fn flash_window(app: &AppHandle<Wry>) {
    if let Some(window) = app.get_webview_window(MAIN_WINDOW) {
        if !window.is_focused().unwrap_or(false) {
            let _ = window.request_user_attention(Some(UserAttentionType::Informational));
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UnreadPayload {
    /// Сколько уведомлений (сообщений) не прочитано.
    pub total: u32,
    /// Есть ли упоминания / личные сообщения.
    pub highlight: bool,
    /// Есть непрочитанные сообщения, включая комнаты без числового счётчика.
    #[serde(default)]
    pub has_unread: bool,
    /// PNG-бейдж для панели задач в base64 (рисует веб-часть).
    pub badge: Option<String>,
}

fn set_overlay(window: &WebviewWindow<Wry>, badge: Option<&str>) {
    #[cfg(target_os = "windows")]
    {
        let image = badge
            .and_then(|b| base64::engine::general_purpose::STANDARD.decode(b).ok())
            .and_then(|bytes| Image::from_bytes(&bytes).ok().map(|i| i.to_owned()));
        let _ = window.set_overlay_icon(image);
    }
    #[cfg(target_os = "macos")]
    {
        let _ = window.set_badge_label(badge.map(|_| "●".to_string()));
    }
    #[cfg(target_os = "linux")]
    {
        let _ = (window, badge);
    }
}

/// Обновить счётчик непрочитанных: иконка трея, подсказка, бейдж на панели задач.
#[tauri::command]
pub fn desktop_set_unread(app: AppHandle<Wry>, payload: UnreadPayload) {
    let state = app.state::<DesktopState>();
    *state.unread_total.lock().unwrap() = payload.total;
    *state.tray_kind.lock().unwrap() = if payload.highlight {
        TrayKind::Highlight
    } else if payload.total > 0 || payload.has_unread {
        TrayKind::Unread
    } else {
        TrayKind::Default
    };
    update_tray(&app);

    if let Some(window) = app.get_webview_window(MAIN_WINDOW) {
        let badge = if payload.total > 0 || payload.has_unread {
            payload.badge.as_deref()
        } else {
            None
        };
        set_overlay(&window, badge);
    }
}

/// Сообщить обёртке, включён ли режим «Не беспокоить».
#[tauri::command]
pub fn desktop_set_dnd(app: AppHandle<Wry>, active: bool) {
    *app.state::<DesktopState>().dnd.lock().unwrap() = active;
    update_tray(&app);
}

#[tauri::command]
pub fn desktop_get_settings(app: AppHandle<Wry>) -> DesktopSettingsView {
    let settings = app.state::<DesktopState>().settings.lock().unwrap().clone();
    DesktopSettingsView {
        close_to_tray: settings.close_to_tray,
        start_minimized: settings.start_minimized,
        autostart: app.autolaunch().is_enabled().unwrap_or(false),
        platform: std::env::consts::OS,
    }
}

#[tauri::command]
pub fn desktop_set_settings(
    app: AppHandle<Wry>,
    patch: DesktopSettingsPatch,
) -> Result<DesktopSettingsView, String> {
    {
        let state = app.state::<DesktopState>();
        let mut settings = state.settings.lock().unwrap();
        if let Some(v) = patch.close_to_tray {
            settings.close_to_tray = v;
        }
        if let Some(v) = patch.start_minimized {
            settings.start_minimized = v;
        }
        save_settings(&app, &settings);
    }
    if let Some(enable) = patch.autostart {
        let launcher = app.autolaunch();
        let res = if enable {
            launcher.enable()
        } else {
            launcher.disable()
        };
        res.map_err(|e| e.to_string())?;
    }
    sync_menu_checks(&app);
    Ok(desktop_get_settings(app))
}

/// Вывести окно на передний план (например, по клику на браузерное уведомление).
#[tauri::command]
pub fn desktop_show_window(app: AppHandle<Wry>) {
    show_main_window(&app);
}
