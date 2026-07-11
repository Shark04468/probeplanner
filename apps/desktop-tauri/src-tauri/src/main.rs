#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() -> tauri::Result<()> {
    tauri::Builder::default()
        .run(tauri::generate_context!())
}
