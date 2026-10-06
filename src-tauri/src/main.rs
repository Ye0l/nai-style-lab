#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use base64::{engine::general_purpose::STANDARD, Engine as _};
use serde_json::{json, Value};
use std::{
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
    sync::Mutex,
};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

struct Backend {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
    stopped: bool,
}
impl Backend {
    fn start(root: PathBuf, data: PathBuf) -> Result<Self, String> {
        let python = std::env::var_os("NAI_PYTHON").unwrap_or_else(|| "python3".into());
        let mut child = Command::new(python)
            .arg("-u")
            .arg(root.join("app/tauri_backend.py"))
            .arg(data)
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|e| format!("Python 엔진을 시작하지 못했습니다: {e}"))?;
        let input = child.stdin.take().ok_or("Missing stdin")?;
        let output = BufReader::new(child.stdout.take().ok_or("Missing stdout")?);
        let mut backend = Self {
            child,
            input,
            output,
            stopped: false,
        };
        let ready = backend.read()?;
        if ready["ready"] != true {
            return Err(ready["error"]
                .as_str()
                .unwrap_or("Python 엔진 초기화 실패")
                .into());
        }
        Ok(backend)
    }
    fn read(&mut self) -> Result<Value, String> {
        let mut line = String::new();
        if self
            .output
            .read_line(&mut line)
            .map_err(|e| e.to_string())?
            == 0
        {
            return Err("Python 엔진이 종료됐습니다. Python/Pillow 설치 여부와 데이터 사용 중 여부를 확인하세요.".into());
        }
        serde_json::from_str(&line).map_err(|e| e.to_string())
    }
    fn request(&mut self, request: Value) -> Result<Value, String> {
        writeln!(self.input, "{request}").map_err(|e| e.to_string())?;
        self.input.flush().map_err(|e| e.to_string())?;
        let reply = self.read()?;
        if let Some(error) = reply["error"].as_str() {
            return Err(error.into());
        }
        Ok(reply["result"].clone())
    }
    fn shutdown(&mut self) {
        if self.stopped {
            return;
        }
        self.stopped = true;
        let _ = self.request(json!({"kind": "shutdown"}));
        let _ = self.child.wait();
    }
}
impl Drop for Backend {
    fn drop(&mut self) {
        self.shutdown();
        let _ = self.child.kill();
    }
}

#[tauri::command]
async fn call(
    app: tauri::AppHandle,
    method: String,
    path: String,
    body: Option<Value>,
) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        // Native dialogs run outside the backend lock so file rendering can continue.
        let picked = if method == "POST" && path == "/api/data/export" {
            Some(rfd::FileDialog::new().add_filter("ZIP", &["zip"]).set_file_name("nai-style-lab.zip")
                .save_file().map(|p| p.to_string_lossy().into_owned()))
        } else if method == "POST" && path == "/api/data/pick" {
            Some(rfd::FileDialog::new().add_filter("ZIP", &["zip"]).pick_file()
                .map(|p| p.to_string_lossy().into_owned()))
        } else { None };
        let state = app.state::<Mutex<Backend>>();
        let mut backend = state.lock().map_err(|e| e.to_string())?;
        backend.request(json!({"kind": "call", "method": method, "path": path, "body": body, "picked": picked.flatten()}))
    }).await.map_err(|e| e.to_string())?
}

struct Clipboard(Mutex<Option<arboard::Clipboard>>);

#[tauri::command]
fn write_clipboard(state: tauri::State<'_, Clipboard>, text: String) -> Result<(), String> {
    let mut clipboard = state.0.lock().map_err(|e| e.to_string())?;
    if clipboard.is_none() {
        *clipboard = Some(arboard::Clipboard::new().map_err(|e| e.to_string())?);
    }
    clipboard
        .as_mut()
        .unwrap()
        .set_text(text)
        .map_err(|e| e.to_string())
}

fn main() {
    let result = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![call, write_clipboard])
        .register_uri_scheme_protocol("nai", |ctx, request| {
            let app = ctx.app_handle();
            let result = (|| -> Result<_, String> {
                if request.method() != "GET" {
                    return Err("Method not allowed".into());
                }
                let state = app.state::<Mutex<Backend>>();
                let value = state
                    .lock()
                    .map_err(|e| e.to_string())?
                    .request(json!({"kind": "resource", "path": request.uri().path()}))?;
                if value.is_null() {
                    return Err("Not found".into());
                }
                let bytes = STANDARD
                    .decode(value["bytes"].as_str().ok_or("Invalid resource")?)
                    .map_err(|e| e.to_string())?;
                Ok((bytes, value))
            })();
            match result {
                Ok((bytes, value)) => tauri::http::Response::builder()
                    .header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src ipc: http://ipc.localhost")
                    .header(
                        "Content-Type",
                        value["content_type"]
                            .as_str()
                            .unwrap_or("application/octet-stream"),
                    )
                    .header(
                        "Cache-Control",
                        value["cache"].as_str().unwrap_or("no-store"),
                    )
                    .body(bytes)
                    .unwrap(),
                Err(error) => tauri::http::Response::builder()
                    .status(404)
                    .body(error.into_bytes())
                    .unwrap(),
            }
        })
        .setup(|app| {
            let root = if cfg!(debug_assertions) {
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .parent()
                    .unwrap()
                    .to_owned()
            } else {
                app.path().resource_dir()?
            };
            let data = std::env::var_os("NAI_DATA_DIR")
                .map(PathBuf::from)
                .unwrap_or(app.path().app_data_dir()?);
            let backend = Backend::start(root, data).map_err(std::io::Error::other)?;
            app.manage(Mutex::new(backend));
            app.manage(Clipboard(Mutex::new(None)));
            WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::CustomProtocol("nai://localhost/".parse()?),
            )
            .title("NAI Style Lab")
            .inner_size(1502.0, 939.0)
            .min_inner_size(900.0, 600.0)
            .on_navigation(|url| url.scheme() == "nai" && url.host_str() == Some("localhost"))
            .build()?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                if let Some(state) = window.try_state::<Mutex<Backend>>() {
                    if let Ok(mut backend) = state.lock() {
                        backend.shutdown();
                    }
                }
            }
        })
        .run(tauri::generate_context!());
    if let Err(error) = result {
        eprintln!("NAI Style Lab 실행 실패: {error}");
        rfd::MessageDialog::new()
            .set_title("NAI Style Lab 실행 실패")
            .set_description(error.to_string())
            .set_level(rfd::MessageLevel::Error)
            .show();
    }
}
