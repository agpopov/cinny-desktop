use crate::desktop;

#[cfg(target_os = "macos")]
use tauri::TitleBarStyle;
use tauri::{
    webview::{NewWindowResponse, WebviewWindowBuilder},
    Manager, WebviewUrl, WindowEvent,
};
use tauri_plugin_autostart::MacosLauncher;
use tauri_plugin_opener::OpenerExt;
use tauri_plugin_window_state::StateFlags;

#[cfg(feature = "updater")]
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
#[cfg(feature = "updater")]
use tauri_plugin_updater::UpdaterExt;

pub fn run() {
    for key in ["NO_PROXY", "no_proxy"] {
        let current_val = std::env::var(key).unwrap_or_default();
        if !current_val.contains("localhost") {
            let new_val = if current_val.is_empty() {
                "localhost,127.0.0.1".to_string()
            } else {
                format!("{},localhost,127.0.0.1", current_val)
            };
            std::env::set_var(key, new_val);
        }
    }

    let port: u16 = 44548;
    let context = tauri::generate_context!();
    #[cfg(feature = "updater")]
    let mut builder = tauri::Builder::default();
    #[cfg(not(feature = "updater"))]
    let builder = tauri::Builder::default();

    // #[cfg(target_os = "macos")]
    // {
    //     builder = builder.menu(menu::menu());
    // }

    #[cfg(feature = "updater")]
    {
        builder = builder.plugin(tauri_plugin_updater::Builder::new().build());
    }

    let builder = builder
        // Второй запуск (ярлык, автозапуск) просто показывает уже открытое окно.
        // Регистрируется первым, как требует плагин.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            desktop::show_main_window(app);
        }));

    #[cfg(target_os = "linux")]
    let builder = builder.plugin(tauri_plugin_clipboard_manager::init());

    builder
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec![desktop::MINIMIZED_ARG]),
        ))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_localhost::Builder::new(port).build())
        // Видимость окна не сохраняем: иначе после «свернуть в трей → выход»
        // следующий запуск начнётся со скрытым окном.
        .plugin(
            tauri_plugin_window_state::Builder::default()
                .with_state_flags(StateFlags::all() & !StateFlags::VISIBLE)
                .build(),
        )
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            desktop::desktop_notify,
            desktop::desktop_set_unread,
            desktop::desktop_set_dnd,
            desktop::desktop_get_settings,
            desktop::desktop_set_settings,
            desktop::desktop_show_window,
        ])
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == desktop::MAIN_WINDOW
                    && desktop::should_hide_on_close(window.app_handle())
                {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .setup(move |app| {
            desktop::init_state(app.handle());
            desktop::register_app_user_model_id(app.handle());
            if let Err(error) = desktop::setup_tray(app.handle()) {
                eprintln!("System tray unavailable: {error}");
            }

            // При автозапуске (флаг --minimized) стартуем свёрнутыми в трей.
            let start_hidden = std::env::args().any(|a| a == desktop::MINIMIZED_ARG)
                && desktop::load_settings(app.handle()).start_minimized
                && desktop::is_tray_available(app.handle());
            #[cfg(feature = "updater")]
            {
                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    let updater = match handle.updater() {
                        Ok(u) => u,
                        Err(e) => {
                            eprintln!("Updater not available: {}", e);
                            return;
                        }
                    };
                    if let Ok(Some(update)) = updater.check().await {
                        let version = update.version.clone();

                        let should_update = handle
                            .dialog()
                            .message(format!(
                                "Version {} is available.\n\nWould you like to update now?",
                                version
                            ))
                            .title("Update Available")
                            .kind(MessageDialogKind::Info)
                            .buttons(MessageDialogButtons::YesNo)
                            .blocking_show();

                        if should_update {
                            if update.download_and_install(|_, _| {}, || {}).await.is_ok() {
                                handle.restart();
                            }
                        }
                    }
                });
            }

            // Dev: use devUrl from tauri.conf.json (http://localhost:8080) to support HMR
            #[cfg(debug_assertions)]
            let window_url = WebviewUrl::App(Default::default());

            // Release: tauri-plugin-localhost serves bundled frontend assets on this port
            #[cfg(not(debug_assertions))]
            let window_url = {
                let url = format!("http://localhost:{}", port).parse().unwrap();
                WebviewUrl::External(url)
            };

            let app_handle = app.handle().clone();
            let window_builder =
                WebviewWindowBuilder::new(app, desktop::MAIN_WINDOW.to_string(), window_url)
                    .title("Cinny")
                    .inner_size(1100.0, 760.0)
                    .min_inner_size(360.0, 480.0)
                    .visible(!start_hidden)
                    .disable_drag_drop_handler()
                    .on_new_window(move |url, _features| {
                        let _ = app_handle.opener().open_url(url.as_str(), None::<&str>);
                        NewWindowResponse::Deny
                    });

            #[cfg(target_os = "macos")]
            let window_builder = window_builder.title_bar_style(TitleBarStyle::Transparent);

            window_builder.build()?;
            Ok(())
        })
        .build(context)
        .expect("error while building tauri application")
        .run(|app, event| {
            #[cfg(target_os = "macos")]
            if matches!(event, tauri::RunEvent::Reopen { .. }) {
                desktop::show_main_window(app);
            }
            #[cfg(not(target_os = "macos"))]
            let _ = (app, event);
        });
}
