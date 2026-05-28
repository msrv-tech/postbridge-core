use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

#[derive(Clone, Serialize)]
struct ServiceStatus {
    state: String,
    port: Option<u16>,
    detail: Option<String>,
}

#[derive(Clone, Serialize)]
struct RuntimeStatus {
    state: String,
    data_dir: String,
    runtime_dir: String,
    platform: String,
    services: BTreeMap<String, ServiceStatus>,
}

#[derive(Clone)]
struct RuntimeService {
    id: &'static str,
    label: &'static str,
    bin: Option<PathBuf>,
    args: Vec<String>,
    port: Option<u16>,
    required: bool,
}

#[derive(Default)]
struct RuntimeState {
    children: BTreeMap<String, Child>,
    logs: Vec<String>,
}

#[derive(Deserialize)]
struct RuntimeManifest {
    platform: String,
    postgres: ManifestBinary,
    queue: ManifestBinary,
    core: ManifestCore,
    web: ManifestWeb,
}

#[derive(Deserialize)]
struct ManifestBinary {
    kind: Option<String>,
    bin: String,
    initdb: Option<String>,
    createdb: Option<String>,
}

#[derive(Deserialize)]
struct ManifestCore {
    api: String,
    worker: String,
    migrate: String,
}

#[derive(Deserialize)]
struct ManifestWeb {
    dist: String,
}

static RUNTIME_STATE: OnceLock<Mutex<RuntimeState>> = OnceLock::new();

fn runtime_state() -> &'static Mutex<RuntimeState> {
    RUNTIME_STATE.get_or_init(|| Mutex::new(RuntimeState::default()))
}

fn timestamp() -> String {
    let seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or_default();
    format!("{seconds}")
}

fn push_log_locked(state: &mut RuntimeState, message: &str) {
    state.logs.push(format!("[{}] {}", timestamp(), message));
    if state.logs.len() > 500 {
        let overflow = state.logs.len() - 500;
        state.logs.drain(0..overflow);
    }
}

fn push_log(message: &str) {
    if let Ok(mut guard) = runtime_state().lock() {
        push_log_locked(&mut guard, message);
    }
}

fn platform_id() -> String {
    let os = std::env::consts::OS;
    let arch = std::env::consts::ARCH;
    match (os, arch) {
        ("windows", "x86_64") => "windows-x64".to_string(),
        ("linux", "x86_64") => "linux-x64".to_string(),
        _ => format!("{os}-{arch}"),
    }
}

fn data_dir_path() -> PathBuf {
    if let Ok(value) = std::env::var("POSTBRIDGE_DESKTOP_DATA_DIR") {
        if !value.trim().is_empty() {
            return PathBuf::from(value);
        }
    }
    if cfg!(target_os = "windows") {
        if let Ok(value) = std::env::var("PROGRAMDATA") {
            return PathBuf::from(value).join("Postbridge");
        }
        if let Ok(value) = std::env::var("SYSTEMDRIVE") {
            return PathBuf::from(format!("{value}\\ProgramData")).join("Postbridge");
        }
    }
    if let Ok(value) = std::env::var("XDG_DATA_HOME") {
        return PathBuf::from(value).join("postbridge");
    }
    if let Ok(value) = std::env::var("HOME") {
        return PathBuf::from(value)
            .join(".local")
            .join("share")
            .join("postbridge");
    }
    PathBuf::from(".postbridge")
}

fn runtime_root() -> PathBuf {
    if let Ok(value) = std::env::var("POSTBRIDGE_DESKTOP_RUNTIME_DIR") {
        if !value.trim().is_empty() {
            return PathBuf::from(value);
        }
    }

    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    for candidate in [
        cwd.clone(),
        cwd.join("desktop"),
        cwd.join(".."),
        cwd.join("../.."),
    ] {
        if candidate.join("runtime").exists() {
            return candidate;
        }
    }
    cwd
}

fn resolve_runtime_path(root: &Path, relative: &str) -> PathBuf {
    root.join(relative)
}

fn load_manifest(root: &Path) -> Result<RuntimeManifest, String> {
    let path = root
        .join("runtime")
        .join("manifests")
        .join(format!("{}.json", platform_id()));
    let body = fs::read_to_string(&path)
        .map_err(|err| format!("could not read runtime manifest {}: {err}", path.display()))?;
    serde_json::from_str(&body)
        .map_err(|err| format!("could not parse runtime manifest {}: {err}", path.display()))
}

fn runtime_env(data_dir: &Path, root: &Path, manifest: &RuntimeManifest) -> BTreeMap<String, String> {
    let postgres_port = "8822";
    let redis_port = "8823";
    let api_port = "8820";
    let web_dist = resolve_runtime_path(root, &manifest.web.dist);
    BTreeMap::from([
        ("APP_ENV".to_string(), "desktop".to_string()),
        ("POSTBRIDGE_APP_MODE".to_string(), "selfhost".to_string()),
        ("POSTBRIDGE_DESKTOP".to_string(), "1".to_string()),
        (
            "POSTBRIDGE_SELFHOST_TENANT_ID".to_string(),
            "00000000-0000-4000-8000-000000000001".to_string(),
        ),
        (
            "POSTBRIDGE_DESKTOP_DATA_DIR".to_string(),
            data_dir.display().to_string(),
        ),
        (
            "POSTBRIDGE_WEB_DIST_DIR".to_string(),
            web_dist.display().to_string(),
        ),
        (
            "DATABASE_URL".to_string(),
            format!("postgresql://postbridge:postbridge@127.0.0.1:{postgres_port}/postbridge"),
        ),
        (
            "REDIS_URL".to_string(),
            format!("redis://127.0.0.1:{redis_port}/0"),
        ),
        ("POSTBRIDGE_API_PORT".to_string(), api_port.to_string()),
        ("CORE_BASE_URL".to_string(), format!("http://127.0.0.1:{api_port}")),
        ("MEDIA_STORAGE_TYPE".to_string(), "local".to_string()),
        (
            "MEDIA_STORAGE_PATH".to_string(),
            data_dir.join("media").display().to_string(),
        ),
        (
            "MEDIA_BASE_URL".to_string(),
            format!("http://127.0.0.1:{api_port}/media"),
        ),
        (
            "CREDENTIALS_ENCRYPTION_KEY".to_string(),
            "desktop-dev-encryption-key-change-before-release".to_string(),
        ),
    ])
}

fn service_definitions(root: &Path, manifest: &RuntimeManifest) -> Vec<RuntimeService> {
    let data_dir = data_dir_path();
    let postgres_args = if manifest.postgres.kind.as_deref() == Some("pg0") {
        vec![
            "start".to_string(),
            "--name".to_string(),
            "postbridge".to_string(),
            "--port".to_string(),
            "8822".to_string(),
            "--data-dir".to_string(),
            data_dir.join("postgres").display().to_string(),
            "--username".to_string(),
            "postbridge".to_string(),
            "--password".to_string(),
            "postbridge".to_string(),
            "--database".to_string(),
            "postbridge".to_string(),
        ]
    } else {
        vec![
            "-D".to_string(),
            data_dir.join("postgres").display().to_string(),
            "-p".to_string(),
            "8822".to_string(),
        ]
    };
    let queue_args = if manifest.queue.kind.as_deref() == Some("garnet") {
        vec![
            "--bind".to_string(),
            "127.0.0.1".to_string(),
            "--port".to_string(),
            "8823".to_string(),
        ]
    } else {
        vec!["--port".to_string(), "8823".to_string()]
    };

    vec![
        RuntimeService {
            id: "postgres",
            label: "PostgreSQL with pgvector",
            bin: Some(resolve_runtime_path(root, &manifest.postgres.bin)),
            args: postgres_args,
            port: Some(8822),
            required: true,
        },
        RuntimeService {
            id: "queue",
            label: "Redis-compatible queue",
            bin: Some(resolve_runtime_path(root, &manifest.queue.bin)),
            args: queue_args,
            port: Some(8823),
            required: true,
        },
        RuntimeService {
            id: "migrate",
            label: "Database migrations",
            bin: Some(resolve_runtime_path(root, &manifest.core.migrate)),
            args: Vec::new(),
            port: None,
            required: true,
        },
        RuntimeService {
            id: "api",
            label: "Core API",
            bin: Some(resolve_runtime_path(root, &manifest.core.api)),
            args: vec!["--host".to_string(), "127.0.0.1".to_string(), "--port".to_string(), "8820".to_string()],
            port: Some(8820),
            required: true,
        },
        RuntimeService {
            id: "worker",
            label: "Worker and scheduler",
            bin: Some(resolve_runtime_path(root, &manifest.core.worker)),
            args: Vec::new(),
            port: None,
            required: true,
        },
        RuntimeService {
            id: "web",
            label: "Existing web UI bundle",
            bin: None,
            args: Vec::new(),
            port: Some(8821),
            required: false,
        },
    ]
}

fn spawn_log_reader(service_id: &'static str, stream_name: &'static str, reader: impl BufRead + Send + 'static) {
    thread::spawn(move || {
        for line in reader.lines() {
            match line {
                Ok(value) => push_log(&format!("{service_id} {stream_name}: {value}")),
                Err(err) => {
                    push_log(&format!("{service_id} {stream_name}: log stream error: {err}"));
                    break;
                }
            }
        }
    });
}

fn ensure_dirs(data_dir: &Path) -> Result<(), String> {
    for path in [
        data_dir.to_path_buf(),
        data_dir.join("postgres"),
        data_dir.join("media"),
        data_dir.join("logs"),
        data_dir.join("backups"),
    ] {
        fs::create_dir_all(&path)
            .map_err(|err| format!("could not create data directory {}: {err}", path.display()))?;
    }
    Ok(())
}

fn init_postgres_if_needed(root: &Path, manifest: &RuntimeManifest, data_dir: &Path, state: &mut RuntimeState) -> Result<(), String> {
    if manifest.postgres.kind.as_deref() == Some("pg0") {
        return Ok(());
    }

    let postgres_dir = data_dir.join("postgres");
    if postgres_dir.join("PG_VERSION").exists() {
        return Ok(());
    }

    let Some(initdb_relative) = manifest.postgres.initdb.as_deref() else {
        push_log_locked(state, "postgres data directory is empty but initdb is not configured");
        return Ok(());
    };
    let initdb = resolve_runtime_path(root, initdb_relative);
    if !initdb.exists() {
        push_log_locked(
            state,
            &format!("postgres data directory is empty but initdb is missing at {}", initdb.display()),
        );
        return Ok(());
    }

    push_log_locked(state, &format!("initializing postgres data directory at {}", postgres_dir.display()));
    let mut initdb_command = Command::new(&initdb);
    initdb_command
        .arg("-D")
        .arg(&postgres_dir)
        .arg("-U")
        .arg("postbridge")
        .arg("--locale=C");
    if !cfg!(target_os = "windows") {
        initdb_command.arg("--encoding=UTF8");
    }
    let output = initdb_command
        .output()
        .map_err(|err| format!("postgres initdb failed to start: {err}"))?;
    for line in String::from_utf8_lossy(&output.stdout).lines() {
        push_log_locked(state, &format!("initdb stdout: {line}"));
    }
    for line in String::from_utf8_lossy(&output.stderr).lines() {
        push_log_locked(state, &format!("initdb stderr: {line}"));
    }
    if !output.status.success() {
        push_log_locked(
            state,
            &format!("postgres initdb exited with status {}", output.status),
        );
        return Err(format!("postgres initdb exited with status {}", output.status));
    }
    Ok(())
}

fn wait_for_port(host: &str, port: u16, timeout: Duration) -> bool {
    let Ok(mut addresses) = (host, port).to_socket_addrs() else {
        return false;
    };
    let Some(address) = addresses.next() else {
        return false;
    };
    let started = SystemTime::now();
    loop {
        if TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_ok() {
            return true;
        }
        if started.elapsed().unwrap_or_default() >= timeout {
            return false;
        }
        thread::sleep(Duration::from_millis(250));
    }
}

fn http_health_ok(host: &str, port: u16, path: &str, timeout: Duration) -> bool {
    let Ok(mut addresses) = (host, port).to_socket_addrs() else {
        return false;
    };
    let Some(address) = addresses.next() else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&address, timeout) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));
    let request = format!("GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = [0_u8; 64];
    let Ok(read) = stream.read(&mut response) else {
        return false;
    };
    read > 0 && String::from_utf8_lossy(&response[..read]).starts_with("HTTP/1.1 200")
}

fn ensure_postgres_database(root: &Path, manifest: &RuntimeManifest, state: &mut RuntimeState) -> Result<(), String> {
    if !wait_for_port("127.0.0.1", 8822, Duration::from_secs(20)) {
        push_log_locked(state, "postgres did not open port 8822 before database bootstrap");
        return Ok(());
    }
    if manifest.postgres.kind.as_deref() == Some("pg0") {
        push_log_locked(state, "pg0 postgres is ready");
        return Ok(());
    }

    let Some(createdb_relative) = manifest.postgres.createdb.as_deref() else {
        push_log_locked(state, "createdb is not configured; skipping postbridge database bootstrap");
        return Ok(());
    };
    let createdb = resolve_runtime_path(root, createdb_relative);
    if !createdb.exists() {
        push_log_locked(state, &format!("createdb is missing at {}", createdb.display()));
        return Ok(());
    }

    let output = Command::new(&createdb)
        .arg("-h")
        .arg("127.0.0.1")
        .arg("-p")
        .arg("8822")
        .arg("-U")
        .arg("postbridge")
        .arg("postbridge")
        .output()
        .map_err(|err| format!("createdb failed to start: {err}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    for line in stdout.lines() {
        push_log_locked(state, &format!("createdb stdout: {line}"));
    }
    for line in stderr.lines() {
        push_log_locked(state, &format!("createdb stderr: {line}"));
    }
    if output.status.success() || stderr.contains("already exists") {
        push_log_locked(state, "postbridge database is ready");
        return Ok(());
    }
    Err(format!("createdb exited with status {}", output.status))
}

fn cleanup_exited_children(state: &mut RuntimeState) {
    let ids: Vec<String> = state.children.keys().cloned().collect();
    for id in ids {
        let mut remove_child = false;
        let mut log_message = None;
        if let Some(child) = state.children.get_mut(&id) {
            match child.try_wait() {
                Ok(Some(status)) => {
                    log_message = Some(format!("{id} exited with status {status}"));
                    remove_child = true;
                }
                Ok(None) => {}
                Err(err) => {
                    log_message = Some(format!("{id} status check failed: {err}"));
                    remove_child = true;
                }
            }
        }
        if let Some(message) = log_message {
            push_log_locked(state, &message);
        }
        if remove_child {
            state.children.remove(&id);
        }
    }
}

fn cleanup_listening_ports(state: &mut RuntimeState) {
    if !cfg!(target_os = "windows") {
        return;
    }
    let Ok(output) = Command::new("netstat").arg("-ano").output() else {
        push_log_locked(state, "port cleanup skipped: netstat failed to start");
        return;
    };
    let body = String::from_utf8_lossy(&output.stdout);
    let mut pids = Vec::new();
    for line in body.lines() {
        if !(line.contains(":8820") || line.contains(":8821") || line.contains(":8822") || line.contains(":8823")) {
            continue;
        }
        if !line.contains("LISTENING") {
            continue;
        }
        if let Some(pid) = line.split_whitespace().last() {
            if pid != "0" && !pids.iter().any(|existing| existing == pid) {
                pids.push(pid.to_string());
            }
        }
    }
    for pid in pids {
        match Command::new("powershell")
            .args([
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                &format!("Stop-Process -Id {pid} -Force -ErrorAction Stop"),
            ])
            .output()
        {
            Ok(output) => {
                if output.status.success() {
                    push_log_locked(state, &format!("stopped stale listener pid {pid}"));
                } else {
                    let stderr = String::from_utf8_lossy(&output.stderr);
                    push_log_locked(state, &format!("could not stop stale listener pid {pid}: {}", stderr.trim()));
                }
            }
            Err(err) => push_log_locked(state, &format!("stale listener cleanup failed for pid {pid}: {err}")),
        }
    }
}

fn service_status(service: &RuntimeService, state: &RuntimeState, root: &Path, manifest: &RuntimeManifest) -> ServiceStatus {
    if service.id == "api" {
        if http_health_ok("127.0.0.1", 8820, "/health", Duration::from_millis(350)) {
            return ServiceStatus {
                state: "running".to_string(),
                port: service.port,
                detail: Some(service.label.to_string()),
            };
        }
        if state.children.contains_key(service.id) || wait_for_port("127.0.0.1", 8820, Duration::from_millis(50)) {
            return ServiceStatus {
                state: "starting".to_string(),
                port: service.port,
                detail: Some(format!("{} is not healthy yet", service.label)),
            };
        }
    }

    if state.children.contains_key(service.id) {
        return ServiceStatus {
            state: "running".to_string(),
            port: service.port,
            detail: Some(service.label.to_string()),
        };
    }
    if let Some(port) = service.port {
        if wait_for_port("127.0.0.1", port, Duration::from_millis(50)) {
            return ServiceStatus {
                state: "running".to_string(),
                port: service.port,
                detail: Some(service.label.to_string()),
            };
        }
    }

    if service.id == "web" {
        let dist = resolve_runtime_path(root, &manifest.web.dist);
        let exists = dist.join("index.html").exists();
        return ServiceStatus {
            state: if exists { "ready" } else { "missing" }.to_string(),
            port: service.port,
            detail: Some(format!("{} at {}", service.label, dist.display())),
        };
    }

    let Some(bin) = &service.bin else {
        return ServiceStatus {
            state: "not_configured".to_string(),
            port: service.port,
            detail: Some(service.label.to_string()),
        };
    };

    ServiceStatus {
        state: if bin.exists() { "stopped" } else { "missing" }.to_string(),
        port: service.port,
        detail: Some(format!("{} at {}", service.label, bin.display())),
    }
}

fn build_status(state: &mut RuntimeState) -> Result<RuntimeStatus, String> {
    cleanup_exited_children(state);
    let root = runtime_root();
    let manifest = load_manifest(&root)?;
    let services = service_definitions(&root, &manifest);
    let mut service_statuses = BTreeMap::new();
    let mut runtime_ready = true;
    let mut missing_required = false;

    for service in &services {
        let status = service_status(service, state, &root, &manifest);
        if service.required && status.state == "missing" {
            missing_required = true;
        }
        if matches!(service.id, "postgres" | "queue" | "api" | "worker")
            && status.state != "running"
        {
            runtime_ready = false;
        }
        service_statuses.insert(service.id.to_string(), status);
    }

    let runtime_state = if missing_required {
        "missing_runtime"
    } else if runtime_ready {
        "running"
    } else if service_statuses.values().any(|status| status.state == "running" || status.state == "starting") {
        "starting"
    } else {
        "stopped"
    };

    Ok(RuntimeStatus {
        state: runtime_state.to_string(),
        data_dir: data_dir_path().display().to_string(),
        runtime_dir: root.display().to_string(),
        platform: manifest.platform,
        services: service_statuses,
    })
}

#[tauri::command]
fn runtime_status() -> Result<RuntimeStatus, String> {
    let mut guard = runtime_state()
        .lock()
        .map_err(|_| "runtime state lock poisoned".to_string())?;
    build_status(&mut guard)
}

#[tauri::command]
fn runtime_logs() -> Result<Vec<String>, String> {
    let guard = runtime_state()
        .lock()
        .map_err(|_| "runtime state lock poisoned".to_string())?;
    Ok(guard.logs.clone())
}

#[tauri::command]
fn runtime_start() -> Result<RuntimeStatus, String> {
    let root = runtime_root();
    let manifest = load_manifest(&root)?;
    let data_dir = data_dir_path();
    ensure_dirs(&data_dir)?;
    let env = runtime_env(&data_dir, &root, &manifest);

    let mut guard = runtime_state()
        .lock()
        .map_err(|_| "runtime state lock poisoned".to_string())?;
    cleanup_exited_children(&mut guard);
    if guard.children.is_empty()
        && !http_health_ok("127.0.0.1", 8820, "/health", Duration::from_millis(350))
        && service_definitions(&root, &manifest).iter().any(|service| {
            service.port.is_some_and(|port| wait_for_port("127.0.0.1", port, Duration::from_millis(50)))
        })
    {
        push_log_locked(&mut guard, "stale runtime ports detected before start; cleaning up listeners");
        cleanup_listening_ports(&mut guard);
        thread::sleep(Duration::from_millis(800));
    }
    push_log_locked(
        &mut guard,
        &format!("runtime start requested for {}", manifest.platform),
    );
    init_postgres_if_needed(&root, &manifest, &data_dir, &mut guard)?;

    for service in service_definitions(&root, &manifest) {
        if guard.children.contains_key(service.id) {
            push_log_locked(&mut guard, &format!("{} already running", service.id));
            continue;
        }
        if service.id == "web" {
            let web_dist = resolve_runtime_path(&root, &manifest.web.dist);
            if web_dist.join("index.html").exists() {
                push_log_locked(&mut guard, &format!("web bundle ready at {}", web_dist.display()));
            } else {
                push_log_locked(&mut guard, &format!("web bundle missing at {}", web_dist.display()));
            }
            continue;
        }

        let Some(bin) = service.bin.clone() else {
            push_log_locked(&mut guard, &format!("{} has no binary configured", service.id));
            continue;
        };
        if !bin.exists() {
            push_log_locked(&mut guard, &format!("{} binary missing at {}", service.id, bin.display()));
            continue;
        }

        if service.id == "migrate" {
            let mut command = Command::new(&bin);
            command.args(&service.args);
            command.envs(env.iter());
            match command.output() {
                Ok(output) => {
                    for line in String::from_utf8_lossy(&output.stdout).lines() {
                        push_log_locked(&mut guard, &format!("migrate stdout: {line}"));
                    }
                    for line in String::from_utf8_lossy(&output.stderr).lines() {
                        push_log_locked(&mut guard, &format!("migrate stderr: {line}"));
                    }
                    if output.status.success() {
                        push_log_locked(&mut guard, "migrate completed successfully");
                    } else {
                        push_log_locked(
                            &mut guard,
                            &format!("migrate exited with status {}", output.status),
                        );
                    }
                }
                Err(err) => {
                    push_log_locked(&mut guard, &format!("migrate failed to start: {err}"));
                }
            }
            continue;
        }

        let mut command = Command::new(&bin);
        command.args(&service.args);
        command.envs(env.iter());
        command.stdout(Stdio::piped());
        command.stderr(Stdio::piped());
        match command.spawn() {
            Ok(mut child) => {
                if let Some(stdout) = child.stdout.take() {
                    spawn_log_reader(service.id, "stdout", BufReader::new(stdout));
                }
                if let Some(stderr) = child.stderr.take() {
                    spawn_log_reader(service.id, "stderr", BufReader::new(stderr));
                }
                push_log_locked(&mut guard, &format!("{} started from {}", service.id, bin.display()));
                guard.children.insert(service.id.to_string(), child);
                if service.id == "postgres" {
                    ensure_postgres_database(&root, &manifest, &mut guard)?;
                }
            }
            Err(err) => {
                push_log_locked(&mut guard, &format!("{} failed to start: {err}", service.id));
            }
        }
    }

    build_status(&mut guard)
}

#[tauri::command]
fn runtime_stop() -> Result<RuntimeStatus, String> {
    let root = runtime_root();
    let manifest = load_manifest(&root).ok();
    let mut guard = runtime_state()
        .lock()
        .map_err(|_| "runtime state lock poisoned".to_string())?;
    cleanup_exited_children(&mut guard);
    push_log_locked(&mut guard, "runtime stop requested");

    let mut ids = vec![
        "worker".to_string(),
        "api".to_string(),
        "queue".to_string(),
        "postgres".to_string(),
    ];
    let extra_ids: Vec<String> = guard
        .children
        .keys()
        .filter(|id| !ids.contains(id))
        .cloned()
        .collect();
    ids.extend(extra_ids);
    for id in ids {
        if let Some(mut child) = guard.children.remove(&id) {
            match child.kill() {
                Ok(()) => {
                    let _ = child.wait();
                    push_log_locked(&mut guard, &format!("{id} stopped"));
                }
                Err(err) => {
                    push_log_locked(&mut guard, &format!("{id} stop failed: {err}"));
                }
            }
        }
    }
    cleanup_listening_ports(&mut guard);
    if let Some(manifest) = manifest {
        if manifest.postgres.kind.as_deref() == Some("pg0") {
            let pg0 = resolve_runtime_path(&root, &manifest.postgres.bin);
            if pg0.exists() {
                match Command::new(&pg0).args(["stop", "--name", "postbridge"]).output() {
                    Ok(output) => {
                        for line in String::from_utf8_lossy(&output.stdout).lines() {
                            push_log_locked(&mut guard, &format!("pg0 stop stdout: {line}"));
                        }
                        for line in String::from_utf8_lossy(&output.stderr).lines() {
                            push_log_locked(&mut guard, &format!("pg0 stop stderr: {line}"));
                        }
                    }
                    Err(err) => push_log_locked(&mut guard, &format!("pg0 stop failed: {err}")),
                }
            }
        }
    }

    build_status(&mut guard)
}

#[tauri::command]
fn runtime_open_postbridge() -> Result<(), String> {
    let url = "http://127.0.0.1:8820/web";
    let mut command = if cfg!(target_os = "windows") {
        let mut command = Command::new("cmd");
        command.args(["/C", "start", "", url]);
        command
    } else if cfg!(target_os = "macos") {
        let mut command = Command::new("open");
        command.arg(url);
        command
    } else {
        let mut command = Command::new("xdg-open");
        command.arg(url);
        command
    };
    command
        .spawn()
        .map_err(|err| format!("could not open Postbridge in browser: {err}"))?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            runtime_status,
            runtime_logs,
            runtime_start,
            runtime_stop,
            runtime_open_postbridge
        ])
        .run(tauri::generate_context!())
        .expect("error while running Postbridge Desktop");
}

fn main() {
    run();
}
