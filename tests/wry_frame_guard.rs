//! Standalone unit harness: no GUI libraries or application build required.
#[derive(Clone)]
pub struct InitializationScript {
  pub script: String,
  pub for_main_frame_only: bool,
}
#[path = "../src-tauri/vendor/wry/src/main_frame_only.rs"]
mod main_frame_only;
