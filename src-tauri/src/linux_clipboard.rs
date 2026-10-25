//! GTK reads the clipboard of the actual display, including native Wayland.
//! arboard's default X11 backend can see a different/empty XWayland clipboard.
#[cfg(target_os = "linux")]
use base64::Engine;
use tauri::{AppHandle, Wry};

#[tauri::command]
pub async fn desktop_read_clipboard_image(app: AppHandle<Wry>) -> Result<Option<String>, String> {
    #[cfg(target_os = "linux")]
    {
        let (sender, mut receiver) = tauri::async_runtime::channel(1);
        app.run_on_main_thread(move || {
            // Never block GTK waiting for a selection owned by our own WebView.
            let clipboard = gtk::Clipboard::get(&gtk::gdk::SELECTION_CLIPBOARD);
            clipboard.request_image(move |_, image| {
                let result = image
                    .map(|image| {
                        image
                            .save_to_bufferv("png", &[])
                            .map(|png| base64::engine::general_purpose::STANDARD.encode(png))
                            .map_err(|error| error.to_string())
                    })
                    .transpose();
                let _ = sender.try_send(result);
            });
        })
        .map_err(|error| error.to_string())?;
        receiver.recv().await.ok_or("Clipboard request cancelled")?
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = app;
        Err("GTK clipboard is only available on Linux".into())
    }
}
