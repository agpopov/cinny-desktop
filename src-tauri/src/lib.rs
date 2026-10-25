#[cfg(desktop)]
mod desktop;
#[cfg(desktop)]
mod desktop_app;
#[cfg(desktop)]
mod linux_clipboard;
#[cfg(target_os = "android")]
mod mobile;

#[cfg_attr(target_os = "android", tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(desktop)]
    desktop_app::run();
    #[cfg(target_os = "android")]
    mobile::run();
}
