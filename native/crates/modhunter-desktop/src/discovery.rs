//! Session files are hints, never authority. Every result passes the Rust
//! loopback health probe before it can become an active connection.
#![cfg_attr(not(windows), allow(dead_code))]
use modhunter_core::{BridgeClient, validate_bridge_endpoint};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeSet, HashSet},
    fs,
    io::Read,
    path::Path,
    sync::{
        Mutex,
        atomic::{AtomicUsize, Ordering},
    },
};

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Descriptor {
    app: String,
    protocol: u32,
    endpoint: String,
    profile_id: String,
    profile_name: String,
    pid: u32,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Session {
    pub endpoint: String,
    pub profile_id: String,
    pub profile_name: String,
    pub version: String,
    pub generation: u64,
    pub latency_ms: f64,
}

fn linked(meta: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        meta.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        meta.file_type().is_symlink()
    }
}

pub fn discover(directory: &Path, client: &BridgeClient) -> std::io::Result<Vec<Session>> {
    let entries = match fs::read_dir(directory) {
        Ok(entries) => entries,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(e),
    };
    // Crashed MO2 processes can leave hundreds of old hints. Directory order
    // is not chronological: choose the newest files across the directory,
    // retaining only 256 metadata entries and probing at most 32 endpoints.
    let mut candidates = BTreeSet::new();
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|s| s.to_str()) != Some("json") {
            continue;
        }
        let Ok(meta) = fs::symlink_metadata(&path) else {
            continue;
        };
        if linked(&meta) || !meta.is_file() || meta.len() > 4096 {
            continue;
        }
        candidates.insert((meta.modified().unwrap_or(std::time::UNIX_EPOCH), path));
        if candidates.len() > 256 {
            candidates.pop_first();
        }
    }
    let mut hints = Vec::new();
    let mut seen = HashSet::new();
    for (_, path) in candidates.into_iter().rev() {
        let Ok(file) = fs::File::open(&path) else {
            continue;
        };
        let mut bytes = Vec::with_capacity(512);
        if file.take(4097).read_to_end(&mut bytes).is_err() || bytes.len() > 4096 {
            continue;
        }
        let Ok(mut hint) = serde_json::from_slice::<Descriptor>(&bytes) else {
            continue;
        };
        if hint.app != "Modhunter" || hint.protocol != 1 || hint.pid == 0 {
            continue;
        }
        let Ok(endpoint) = validate_bridge_endpoint(&hint.endpoint) else {
            continue;
        };
        hint.endpoint = endpoint;
        if seen.insert(hint.endpoint.clone()) {
            hints.push(hint);
        }
        if hints.len() == 32 {
            break;
        }
    }
    let next = AtomicUsize::new(0);
    let results = Mutex::new(Vec::new());
    std::thread::scope(|scope| {
        for _ in 0..hints.len().min(8) {
            let hints = &hints;
            let next = &next;
            let results = &results;
            scope.spawn(move || {
                loop {
                    let i = next.fetch_add(1, Ordering::Relaxed);
                    let Some(hint) = hints.get(i) else {
                        break;
                    };
                    let Ok(health) = client.probe(&hint.endpoint) else {
                        continue;
                    };
                    let profile_name = if health.profile_id == hint.profile_id {
                        hint.profile_name.clone()
                    } else {
                        "MO2 profile changed".into()
                    };
                    results
                        .lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .push(Session {
                            endpoint: health.endpoint,
                            profile_id: health.profile_id,
                            profile_name,
                            version: health.version,
                            generation: health.generation,
                            latency_ms: health.latency_ms,
                        });
                }
            });
        }
    });
    let mut results = results.into_inner().unwrap_or_else(|e| e.into_inner());
    results.sort_by(|a, b| {
        a.profile_name
            .cmp(&b.profile_name)
            .then(a.endpoint.cmp(&b.endpoint))
    });
    Ok(results)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::Write, net::TcpListener, time::Duration};
    #[test]
    fn session_is_probed_and_duplicate_hints_do_not_duplicate_connections() {
        let temp = tempfile::tempdir().unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let endpoint = format!("http://{}/", listener.local_addr().unwrap());
        let descriptor = serde_json::json!({"app":"Modhunter","protocol":1,"endpoint":endpoint,"profileId":"qa","profileName":"Native QA","pid":123});
        for i in 0..400 {
            let path = temp.path().join(format!("stale-{i:03}.json"));
            let mut old = descriptor.clone();
            old["endpoint"] = serde_json::json!(format!("http://127.0.0.1:{}/", 11000 + i));
            fs::write(&path, old.to_string()).unwrap();
            fs::OpenOptions::new()
                .write(true)
                .open(&path)
                .unwrap()
                .set_times(
                    fs::FileTimes::new()
                        .set_modified(std::time::UNIX_EPOCH + Duration::from_secs(100)),
                )
                .unwrap();
        }
        // Create live descriptors after more than 256 stale ones. A bounded
        // read_dir().take(256) silently omitted them on real filesystems.
        for name in ["zz-first.json", "zz-second.json"] {
            fs::write(temp.path().join(name), descriptor.to_string()).unwrap();
        }
        let worker = std::thread::spawn(move || {
            let deadline = std::time::Instant::now() + Duration::from_secs(5);
            let mut stream = loop {
                match listener.accept() {
                    Ok((stream, _)) => break stream,
                    Err(e)
                        if e.kind() == std::io::ErrorKind::WouldBlock
                            && std::time::Instant::now() < deadline =>
                    {
                        std::thread::sleep(Duration::from_millis(5))
                    }
                    Err(e) => panic!("Live session was starved by stale discovery hints: {e}"),
                }
            };
            // Winsock can inherit the listener's nonblocking mode. The
            // listener polls a bounded deadline, but this accepted fixture
            // connection must wait for the client's complete HTTP headers.
            stream.set_nonblocking(false).unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            stream
                .set_write_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let mut request = Vec::new();
            let mut buffer = [0_u8; 1024];
            while !request.windows(4).any(|part| part == b"\r\n\r\n") {
                let n = stream.read(&mut buffer).unwrap();
                assert!(n > 0, "Health client closed before sending HTTP headers");
                request.extend_from_slice(&buffer[..n]);
                assert!(
                    request.len() <= 8192,
                    "Health request headers exceeded fixture limit"
                );
            }
            assert!(String::from_utf8_lossy(&request).starts_with("GET /api/health"));
            let body = r#"{"ok":true,"app":"Modhunter","protocol":1,"live":true,"version":"0.9.0","profileId":"qa","generation":1}"#;
            write!(stream,"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",body.len()).unwrap();
        });
        let sessions = discover(
            temp.path(),
            &BridgeClient::with_timeout(Duration::from_secs(1)).unwrap(),
        )
        .unwrap();
        worker.join().unwrap();
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions[0].profile_name, "Native QA");
        assert_eq!(sessions[0].endpoint, endpoint);
        assert_eq!(sessions[0].generation, 1);
    }
    #[test]
    fn stale_wrong_protocol_remote_and_oversized_descriptors_are_not_connections() {
        let temp = tempfile::tempdir().unwrap();
        let client = BridgeClient::with_timeout(Duration::from_millis(40)).unwrap();
        for (index, endpoint, protocol) in [
            (0, "https://example.com/", 1),
            (1, "http://127.0.0.1:9/", 1),
            (2, "http://127.0.0.1:55555/", 2),
        ] {
            fs::write(temp.path().join(format!("{index}.json")),serde_json::json!({"app":"Modhunter","protocol":protocol,"endpoint":endpoint,"profileId":"qa","profileName":"QA","pid":123}).to_string()).unwrap();
        }
        fs::write(temp.path().join("large.json"), "x".repeat(4100)).unwrap();
        assert!(discover(temp.path(), &client).unwrap().is_empty());
        assert!(
            discover(&temp.path().join("missing"), &client)
                .unwrap()
                .is_empty()
        );
    }
}
