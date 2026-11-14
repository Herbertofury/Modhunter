//! Authenticated native-only content operations. Tokens stay in this connection
//! object; no redirect, proxy, mutation retry, or browser-supplied endpoint.
use super::{BridgeClient, CoreError, validate_bridge_endpoint};
use reqwest::blocking::Response;
use serde_json::Value;
use std::io::Read;

const RESULT_LIMIT: u64 = 32 * 1024 * 1024;
fn read_reply(response: Response, limit: u64) -> Result<Value, CoreError> {
    if response
        .content_length()
        .is_some_and(|length| length > limit)
    {
        return Err(CoreError::Invalid(
            "The native response exceeds its complete-result limit.".into(),
        ));
    }
    let status = response.status();
    let mut bytes = Vec::new();
    response
        .take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| crate::io_error("Read native MO2 response", error))?;
    if bytes.len() as u64 > limit {
        return Err(CoreError::Invalid(
            "The native response exceeds its complete-result limit.".into(),
        ));
    }
    let value: Value = serde_json::from_slice(&bytes)?;
    if status.is_server_error() {
        return Err(CoreError::Invalid(
            "The native service failed; operation acceptance must be reconciled before retrying."
                .into(),
        ));
    }
    if !status.is_success() || value.get("ok").and_then(Value::as_bool) == Some(false) {
        let message = value
            .get("error")
            .or_else(|| value.get("message"))
            .and_then(Value::as_str)
            .unwrap_or("The native operation was not accepted.");
        // Only a definite structured rejection authorizes another explicit try.
        // Transport/JSON failures leave mutation acceptance unknown.
        return Err(CoreError::NativeRejected(
            message.chars().take(1000).collect(),
        ));
    }
    if !value.is_object() {
        return Err(CoreError::Invalid(
            "The native response is not an object.".into(),
        ));
    }
    Ok(value)
}

impl BridgeClient {
    /// Route names are owned by the desktop. Parameters never become URL paths.
    pub fn native_content(
        &self,
        endpoint: &str,
        route: &str,
        value: &Value,
    ) -> Result<Value, CoreError> {
        let method = match route {
            "snapshot" | "notification-facts" | "install-queue" | "content-context"
            | "content-jobs" | "content-backups" | "duplicates" => "GET",
            "content-install"
            | "content-cancel"
            | "content-recovery"
            | "content-batch-restore"
            | "install-queue-control"
            | "duplicate-recovery" => "POST",
            _ => return Err(CoreError::Invalid("Unknown native content route.".into())),
        };
        let endpoint = validate_bridge_endpoint(endpoint)?;
        let bootstrap = read_reply(
            self.client
                .get(format!("{endpoint}api/native-session"))
                .header("X-Modhunter-Native", "content-v1")
                .header("Accept", "application/json")
                .send()?,
            16 * 1024,
        )?;
        if bootstrap.get("protocol").and_then(Value::as_u64) != Some(1) {
            return Err(CoreError::Invalid(
                "The MO2 plugin does not support native content operations.".into(),
            ));
        }
        let token = bootstrap
            .get("token")
            .and_then(Value::as_str)
            .filter(|token| {
                (16..=256).contains(&token.len())
                    && token
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"-_".contains(&b))
            })
            .ok_or_else(|| {
                CoreError::Invalid("The MO2 plugin did not provide a valid native session.".into())
            })?;
        let url = format!("{endpoint}api/{route}");
        let request = if method == "GET" {
            let mut query = Vec::new();
            if let Some(object) = value.as_object() {
                for (key, item) in object {
                    let text = item.as_str().ok_or_else(|| {
                        CoreError::Invalid("Native query values must be text.".into())
                    })?;
                    if key.len() > 64 || text.len() > 1024 {
                        return Err(CoreError::Invalid("Native query is too large.".into()));
                    }
                    query.push((key.as_str(), text));
                }
            } else if !value.is_null() {
                return Err(CoreError::Invalid("Native query must be an object.".into()));
            }
            self.client.get(url).query(&query)
        } else {
            if serde_json::to_vec(value)?.len() > 1024 * 1024 {
                return Err(CoreError::Invalid(
                    "Native content request exceeds 1 MiB.".into(),
                ));
            }
            self.client.post(url).json(value)
        };
        read_reply(
            request
                .header("X-Modhunter-Native", "content-v1")
                .header("X-Arsenal-Token", token)
                .header("Origin", endpoint.trim_end_matches('/'))
                .header("Accept", "application/json")
                .send()?,
            RESULT_LIMIT,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::io::Write;
    use std::net::TcpListener;

    #[test]
    fn native_bridge_authenticates_and_does_not_expose_tokens_in_results() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}/", listener.local_addr().unwrap());
        let server = std::thread::spawn(move || {
            for step in 0..2 {
                let (mut stream, _) = listener.accept().unwrap();
                let mut bytes = [0; 8192];
                let count = stream.read(&mut bytes).unwrap();
                let request = String::from_utf8_lossy(&bytes[..count]).to_lowercase();
                assert!(request.contains("x-modhunter-native: content-v1"));
                let body = if step == 0 {
                    assert!(request.starts_with("get /api/native-session "));
                    assert!(!request.contains("\r\norigin:"));
                    json!({"ok":true,"protocol":1,"token":"fixture_native_secret_123456"})
                } else {
                    assert!(request.starts_with("post /api/content-cancel "));
                    assert!(request.contains("x-arsenal-token: fixture_native_secret_123456"));
                    assert!(request.contains("\r\norigin: http://127.0.0.1:"));
                    json!({"ok":true,"state":"cancelling"})
                }
                .to_string();
                write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    body.len(),
                    body
                )
                .unwrap();
            }
        });
        let result = BridgeClient::new()
            .unwrap()
            .native_content(&endpoint, "content-cancel", &json!({"jobId":"job-1"}))
            .unwrap();
        assert_eq!(result["state"], "cancelling");
        assert!(!result.to_string().contains("fixture_native_secret"));
        server.join().unwrap();
    }

    #[test]
    fn native_routes_and_endpoints_are_not_renderer_paths() {
        let client = BridgeClient::new().unwrap();
        for route in [
            "../health",
            "content-install?x=1",
            "native-session",
            "https://evil.example/",
        ] {
            assert!(
                client
                    .native_content("http://127.0.0.1:12345/", route, &json!({}))
                    .is_err()
            );
        }
        assert!(
            client
                .native_content("https://example.com/", "content-install", &json!({}))
                .is_err()
        );
    }
}
