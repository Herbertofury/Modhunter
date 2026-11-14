//! Reusable, deadline-bound connection probe. Never follows redirects or
//! environment proxy configuration, and never replays a mutation.
use crate::{CoreError, io_error};
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use std::io::Read;
use std::time::{Duration, Instant};
use url::Url;

const HEALTH_LIMIT: u64 = 16_384;

#[path = "native_bridge.rs"]
mod native;

pub fn validate_bridge_endpoint(raw: &str) -> Result<String, CoreError> {
    let raw = raw.trim();
    if raw.chars().any(char::is_control) {
        return Err(CoreError::Invalid(
            "MO2 address contains control characters".into(),
        ));
    }
    let url = Url::parse(raw).map_err(|_| {
        CoreError::Invalid("Enter the MO2 plugin address: http://127.0.0.1:PORT/".into())
    })?;
    let host = url.host_str().unwrap_or("");
    let authority = raw
        .strip_prefix("http://")
        .and_then(|s| s.split('/').next())
        .unwrap_or("");
    // Reject non-canonical numeric forms, including 127.1 and 2130706433.
    let raw_host = authority.rsplit_once(':').map(|(h, _)| h).unwrap_or("");
    if url.scheme() != "http"
        || !matches!(host, "127.0.0.1" | "localhost")
        || !matches!(raw_host, "127.0.0.1" | "localhost")
        || !url.username().is_empty()
        || url.password().is_some()
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
        || url.port().is_none_or(|p| p < 1024)
    {
        return Err(CoreError::Invalid("MO2 connection requires an explicit local http://127.0.0.1:PORT/ address with no path, credentials, or query".into()));
    }
    Ok(format!("http://127.0.0.1:{}/", url.port().unwrap()))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BridgeHealth {
    pub endpoint: String,
    pub version: String,
    pub profile_id: String,
    pub generation: u64,
    pub latency_ms: f64,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct HealthResponse {
    ok: bool,
    app: String,
    protocol: u32,
    live: bool,
    version: String,
    profile_id: String,
    generation: u64,
}

#[derive(Clone)]
pub struct BridgeClient {
    client: Client,
}

impl BridgeClient {
    pub fn new() -> Result<Self, CoreError> {
        Self::with_timeout(Duration::from_secs(4))
    }

    pub fn with_timeout(timeout: Duration) -> Result<Self, CoreError> {
        let client = Client::builder()
            .connect_timeout(timeout.min(Duration::from_secs(2)))
            .timeout(timeout)
            .redirect(reqwest::redirect::Policy::none())
            .no_proxy()
            .user_agent(concat!("Modhunter/", env!("CARGO_PKG_VERSION")))
            .pool_max_idle_per_host(2)
            .build()?;
        Ok(Self { client })
    }

    pub fn probe(&self, endpoint: &str) -> Result<BridgeHealth, CoreError> {
        let endpoint = validate_bridge_endpoint(endpoint)?;
        let start = Instant::now();
        let response = self
            .client
            .get(format!("{endpoint}api/health"))
            .header("Accept", "application/json")
            .send()?;
        if response.status().as_u16() == 404 {
            return Err(CoreError::Invalid("This MO2 plugin is older than the Rust desktop protocol. Install the included Modhunter 0.9 plugin, restart MO2, and open Update Arsenal.".into()));
        }
        if !response.status().is_success() {
            return Err(CoreError::Invalid(format!(
                "MO2 health check returned HTTP {}",
                response.status()
            )));
        }
        if response.content_length().is_some_and(|n| n > HEALTH_LIMIT) {
            return Err(CoreError::Invalid(
                "MO2 health response exceeded 16 KiB".into(),
            ));
        }
        let mut bytes = Vec::with_capacity(1024);
        response
            .take(HEALTH_LIMIT + 1)
            .read_to_end(&mut bytes)
            .map_err(|e| io_error("Read MO2 health response", e))?;
        if bytes.len() as u64 > HEALTH_LIMIT {
            return Err(CoreError::Invalid(
                "MO2 health response exceeded 16 KiB".into(),
            ));
        }
        let info: HealthResponse = serde_json::from_slice(&bytes)?;
        if !info.ok || !info.live || info.app != "Modhunter" || info.protocol != 1 {
            return Err(CoreError::Invalid(
                "The local address is not a compatible live Modhunter MO2 plugin".into(),
            ));
        }
        Ok(BridgeHealth {
            endpoint,
            version: info.version,
            profile_id: info.profile_id,
            generation: info.generation,
            latency_ms: start.elapsed().as_secs_f64() * 1000.0,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::net::TcpListener;
    use std::thread;

    #[test]
    fn endpoint_is_strictly_local() {
        assert_eq!(
            validate_bridge_endpoint("http://localhost:51337/").unwrap(),
            "http://127.0.0.1:51337/"
        );
        for bad in [
            "https://127.0.0.1:51337/",
            "http://127.1:51337/",
            "http://2130706433:51337/",
            "http://127.0.0.1.evil:51337/",
            "http://user@127.0.0.1:51337/",
            "http://127.0.0.1:51337/?x",
            "http://127.0.0.1:51337/#a",
            "http://127.0.0.1:51337/foo",
            "http://127.0.0.1:80/",
            "http://[::1]:51337/",
            "file:///tmp/x",
        ] {
            assert!(validate_bridge_endpoint(bad).is_err(), "accepted {bad}");
        }
    }

    fn mock(status: &str, body: String) -> (String, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}/", listener.local_addr().unwrap());
        let status = status.to_owned();
        let worker = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut b = [0u8; 2048];
            let n = stream.read(&mut b).unwrap();
            let request = String::from_utf8_lossy(&b[..n]);
            assert!(request.starts_with("GET /api/health HTTP/1.1"));
            let _ = write!(
                stream,
                "HTTP/1.1 {status}\r\nContent-Length: {}\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
        });
        (url, worker)
    }

    #[test]
    fn probe_validates_identity_and_bounds() {
        let body = r#"{"ok":true,"app":"Modhunter","protocol":1,"live":true,"version":"0.9.0","profileId":"hero","generation":3}"#.to_owned();
        let (url, worker) = mock("200 OK", body);
        let health = BridgeClient::new().unwrap().probe(&url).unwrap();
        assert_eq!(health.profile_id, "hero");
        assert_eq!(health.generation, 3);
        worker.join().unwrap();
        let (url, worker) = mock("200 OK", "x".repeat(20_000));
        assert!(
            BridgeClient::new()
                .unwrap()
                .probe(&url)
                .unwrap_err()
                .to_string()
                .contains("16 KiB")
        );
        worker.join().unwrap();
        let (url, worker) = mock("302 Found", "".into());
        assert!(BridgeClient::new().unwrap().probe(&url).is_err());
        worker.join().unwrap();
    }
}
