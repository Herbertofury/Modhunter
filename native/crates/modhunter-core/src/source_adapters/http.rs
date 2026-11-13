//! Public API transport only. Derived endpoints, no credentials, and one narrow
//! GitLab latest-release redirect whose final body must prove project identity.
//! Body bounding follows Engineering-Atlas's public_http.rs; cached payloads are
//! reparsed and rebound to the requested project on every use.
use super::identity::{api_url_allowed, gitlab_latest_redirect};
use crate::CoreError;
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::io::Read;
use std::sync::{
    Mutex,
    atomic::{AtomicUsize, Ordering},
};
use std::time::{Duration, Instant};

pub const BODY_LIMIT: usize = 2 * 1024 * 1024;
const CACHE_LIMIT: usize = 4 * 1024 * 1024;
const ENTRY_LIMIT: usize = 128 * 1024;
const TTL: u64 = 900;

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SourceCache {
    #[serde(default)]
    pub entries: Vec<CacheEntry>,
}
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CacheEntry {
    pub url: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resolved_url: Option<String>,
    #[serde(default)]
    pub body: Option<Value>,
    #[serde(default)]
    pub etag: Option<String>,
    #[serde(default)]
    pub last_modified: Option<String>,
    #[serde(default)]
    pub checked_at: u64,
    #[serde(default)]
    pub expires_at: u64,
    #[serde(default)]
    pub retry_at: u64,
    #[serde(default)]
    pub last_status: u16,
}

#[derive(Debug, Clone, Default)]
pub struct Validators {
    pub etag: Option<String>,
    pub last_modified: Option<String>,
}
#[derive(Debug, Clone)]
pub struct HttpReply {
    pub status: u16,
    pub body: Vec<u8>,
    pub headers: BTreeMap<String, String>,
}
#[derive(Debug, Clone)]
pub struct FetchFailure {
    pub availability: String,
    pub reason: String,
    pub retry_at: u64,
    pub checked_at: u64,
}
impl FetchFailure {
    fn new(availability: &str, reason: &str, now: u64) -> Self {
        Self {
            availability: availability.into(),
            reason: reason.into(),
            retry_at: now + 60,
            checked_at: now,
        }
    }
}
/// Injected transports are for deterministic tests. The CLI always uses the
/// fixed-host HTTPS implementation below; requests cannot inject a transport.
pub trait SourceTransport: Sync {
    fn get(
        &self,
        url: &str,
        validators: &Validators,
        timeout: Duration,
    ) -> Result<HttpReply, String>;
}
pub struct PublicTransport {
    client: Client,
}
impl PublicTransport {
    pub fn new() -> Result<Self, CoreError> {
        Ok(Self {
            client: Client::builder()
                .user_agent("Modhunter/0.9 (+https://github.com/Herbertofury/Modhunter)")
                .redirect(reqwest::redirect::Policy::none())
                // HTTPS proxy negotiation can legitimately take several
                // seconds; retain the separate ten-second total deadline.
                .connect_timeout(Duration::from_secs(8))
                .timeout(Duration::from_secs(10))
                .pool_max_idle_per_host(4)
                .build()?,
        })
    }
}
impl SourceTransport for PublicTransport {
    fn get(
        &self,
        url: &str,
        validators: &Validators,
        timeout: Duration,
    ) -> Result<HttpReply, String> {
        if !api_url_allowed(url) {
            return Err("Unsupported API endpoint".into());
        }
        let mut request = self
            .client
            .get(url)
            .timeout(timeout)
            .header("Accept", "application/json");
        if url.starts_with("https://api.github.com/") {
            request = request.header("X-GitHub-Api-Version", "2026-03-10");
        }
        if let Some(etag) = &validators.etag {
            request = request.header("If-None-Match", etag);
        }
        if let Some(modified) = &validators.last_modified {
            request = request.header("If-Modified-Since", modified);
        }
        let response = request.send().map_err(|e| {
            if e.is_timeout() {
                "Provider request timed out"
            } else {
                "Provider connection failed"
            }
            .to_owned()
        })?;
        let status = response.status().as_u16();
        let mut headers = BTreeMap::new();
        for name in [
            "etag",
            "last-modified",
            "retry-after",
            "x-ratelimit-remaining",
            "x-ratelimit-reset",
            "content-type",
            "location",
        ] {
            if let Some(value) = response.headers().get(name).and_then(|v| v.to_str().ok())
                && clean_header(value)
            {
                headers.insert(name.into(), value.into());
            }
        }
        let mut body = Vec::new();
        if status == 200 {
            let content_type = headers
                .get("content-type")
                .map(String::as_str)
                .unwrap_or("")
                .split(';')
                .next()
                .unwrap_or("")
                .trim();
            if content_type != "application/json" && !content_type.ends_with("+json") {
                return Err("Provider returned a non-JSON page or verification challenge".into());
            }
            if response
                .content_length()
                .is_some_and(|n| n > BODY_LIMIT as u64)
            {
                return Err("Provider response exceeded 2 MiB".into());
            }
            response
                .take(BODY_LIMIT as u64 + 1)
                .read_to_end(&mut body)
                .map_err(|_| "Provider response was incomplete".to_owned())?;
            if body.len() > BODY_LIMIT {
                return Err("Provider response exceeded 2 MiB".into());
            }
        }
        Ok(HttpReply {
            status,
            body,
            headers,
        })
    }
}

fn clean_header(value: &str) -> bool {
    value.len() <= 1024 && !value.chars().any(char::is_control)
}
#[derive(Debug, Clone)]
pub struct Observation {
    pub value: Value,
    pub checked_at: u64,
    pub expires_at: u64,
    pub freshness: &'static str,
}
#[derive(Default)]
pub struct HttpStats {
    pub requests: AtomicUsize,
    pub cache_hits: AtomicUsize,
    pub revalidated: AtomicUsize,
    pub rate_limited: AtomicUsize,
}
#[derive(Default)]
struct CacheStore {
    entries: BTreeMap<String, CacheEntry>,
    sizes: BTreeMap<String, usize>,
    bytes: usize,
}
impl CacheStore {
    fn get(&self, url: &str) -> Option<&CacheEntry> {
        self.entries.get(url)
    }
    fn values(&self) -> impl Iterator<Item = &CacheEntry> {
        self.entries.values()
    }
    fn remove(&mut self, url: &str) {
        self.entries.remove(url);
        if let Some(size) = self.sizes.remove(url) {
            self.bytes = self.bytes.saturating_sub(size);
        }
    }
    fn insert(&mut self, url: String, entry: CacheEntry) {
        self.remove(&url);
        let size = serde_json::to_vec(&entry).map_or(ENTRY_LIMIT + 1, |v| v.len());
        if size > ENTRY_LIMIT {
            return;
        }
        self.bytes += size;
        self.sizes.insert(url.clone(), size);
        self.entries.insert(url, entry);
        while self.bytes > CACHE_LIMIT || self.entries.len() > 256 {
            let oldest = self
                .entries
                .values()
                .min_by_key(|e| e.checked_at.max(e.retry_at))
                .map(|e| e.url.clone());
            if let Some(oldest) = oldest {
                self.remove(&oldest);
            } else {
                break;
            }
        }
    }
}
pub(crate) struct HttpContext<'a> {
    transport: &'a dyn SourceTransport,
    cache: Mutex<CacheStore>,
    host_cooldowns: Mutex<BTreeMap<String, (u64, u16)>>,
    now: u64,
    deadline: Instant,
    force: bool,
    pub stats: HttpStats,
}
impl<'a> HttpContext<'a> {
    pub fn new(
        transport: &'a dyn SourceTransport,
        cache: &SourceCache,
        now: u64,
        budget: Duration,
        force: bool,
    ) -> Self {
        let mut entries = CacheStore::default();
        let mut total = 0;
        let mut host_cooldowns = BTreeMap::new();
        for item in cache.entries.iter().take(256) {
            if !api_url_allowed(&item.url)
                || item.checked_at > now + 1
                || item.retry_at > now + 3600
                || item.resolved_url.as_ref().is_some_and(|resolved| {
                    gitlab_latest_redirect(&item.url, resolved).is_none_or(|(url, tag)| {
                        url != *resolved
                            || item.body.as_ref().is_some_and(|body| {
                                body.get("tag_name").and_then(Value::as_str) != Some(&tag)
                            })
                    })
                })
            {
                continue;
            }
            let size = serde_json::to_vec(item).map_or(ENTRY_LIMIT + 1, |v| v.len());
            if size > ENTRY_LIMIT || total + size > CACHE_LIMIT {
                continue;
            }
            if item.etag.as_deref().is_some_and(|v| !clean_header(v))
                || item
                    .last_modified
                    .as_deref()
                    .is_some_and(|v| !clean_header(v))
            {
                continue;
            }
            let mut item = item.clone();
            item.expires_at = item.expires_at.min(item.checked_at.saturating_add(TTL));
            if matches!(item.last_status, 429 | 503) && item.retry_at > now {
                let host = reqwest::Url::parse(&item.url)
                    .ok()
                    .and_then(|u| u.host_str().map(str::to_owned))
                    .unwrap_or_default();
                host_cooldowns
                    .entry(host)
                    .and_modify(|n: &mut (u64, u16)| {
                        if item.retry_at > n.0 {
                            *n = (item.retry_at, item.last_status);
                        }
                    })
                    .or_insert((item.retry_at, item.last_status));
            }
            total += size;
            entries.insert(item.url.clone(), item);
        }
        Self {
            transport,
            cache: Mutex::new(entries),
            host_cooldowns: Mutex::new(host_cooldowns),
            now,
            deadline: Instant::now() + budget,
            force,
            stats: HttpStats::default(),
        }
    }
    pub fn fetch(&self, url: &str) -> Result<Observation, FetchFailure> {
        if !api_url_allowed(url) {
            return Err(FetchFailure::new(
                "unsupported",
                "Unsupported provider API endpoint",
                self.now,
            ));
        }
        let host = reqwest::Url::parse(url)
            .expect("validated URL")
            .host_str()
            .unwrap_or_default()
            .to_owned();
        if let Some((retry, status)) = self
            .host_cooldowns
            .lock()
            .expect("cooldown mutex")
            .get(&host)
            .copied()
            && retry > self.now
        {
            if status == 429 {
                self.stats.rate_limited.fetch_add(1, Ordering::Relaxed);
            }
            return Err(FetchFailure {
                availability: status_name(status).into(),
                reason: "Provider cooldown is active; forced checks also respect it".into(),
                retry_at: retry,
                checked_at: self.now,
            });
        }
        let previous = self.cache.lock().expect("cache mutex").get(url).cloned();
        if let Some(entry) = &previous {
            if entry.retry_at > self.now {
                return Err(FetchFailure {
                    availability: status_name(entry.last_status).into(),
                    reason: "Provider retry cooldown is active".into(),
                    retry_at: entry.retry_at,
                    checked_at: entry.checked_at,
                });
            }
            if !self.force
                && entry.expires_at > self.now
                && let Some(value) = &entry.body
            {
                self.stats.cache_hits.fetch_add(1, Ordering::Relaxed);
                return Ok(Observation {
                    value: value.clone(),
                    checked_at: entry.checked_at,
                    expires_at: entry.expires_at,
                    freshness: "cached",
                });
            }
        }
        let remaining = self.deadline.saturating_duration_since(Instant::now());
        if remaining < Duration::from_millis(20) {
            return Err(FetchFailure::new(
                "budget-exhausted",
                "This scan reached its time budget; unchecked projects remain unknown",
                self.now,
            ));
        }
        let validators = Validators {
            etag: previous.as_ref().and_then(|e| e.etag.clone()),
            last_modified: previous.as_ref().and_then(|e| e.last_modified.clone()),
        };
        let empty_validators = Validators::default();
        let first_validators = if previous.as_ref().is_some_and(|e| e.resolved_url.is_some()) {
            &empty_validators
        } else {
            &validators
        };
        self.stats.requests.fetch_add(1, Ordering::Relaxed);
        let mut reply = match self.transport.get(
            url,
            first_validators,
            remaining.min(Duration::from_secs(10)),
        ) {
            Ok(reply) => reply,
            Err(reason) => {
                self.remember_failure(url, previous, 0, self.now + 60);
                return Err(FetchFailure::new("unavailable", &reason, self.now));
            }
        };
        let mut resolved_url = None;
        let mut redirect_tag = None;
        if matches!(reply.status, 302 | 307 | 308)
            && let Some((target, tag)) = reply
                .headers
                .get("location")
                .and_then(|location| gitlab_latest_redirect(url, location))
        {
            let remaining = self.deadline.saturating_duration_since(Instant::now());
            if remaining < Duration::from_millis(20) {
                self.remember_failure(url, previous, 0, self.now + 60);
                return Err(FetchFailure::new(
                    "budget-exhausted",
                    "This scan reached its time budget before the GitLab release could be checked",
                    self.now,
                ));
            }
            // Validators belong to a specific resolved release endpoint. A
            // new latest tag cannot borrow the previous release's ETag/304.
            let target_validators = if previous.as_ref().and_then(|e| e.resolved_url.as_deref())
                == Some(target.as_str())
            {
                &validators
            } else {
                &empty_validators
            };
            self.stats.requests.fetch_add(1, Ordering::Relaxed);
            reply = match self.transport.get(
                &target,
                target_validators,
                remaining.min(Duration::from_secs(10)),
            ) {
                Ok(reply) => reply,
                Err(reason) => {
                    self.remember_failure(url, previous, 0, self.now + 60);
                    return Err(FetchFailure::new("unavailable", &reason, self.now));
                }
            };
            resolved_url = Some(target);
            redirect_tag = Some(tag);
        }
        if reply.status == 304 {
            if let Some(mut entry) = previous
                .as_ref()
                .filter(|e| e.body.is_some() && e.resolved_url == resolved_url)
                .cloned()
            {
                entry.checked_at = self.now;
                entry.expires_at = self.now + TTL;
                entry.retry_at = 0;
                entry.last_status = 200;
                let value = entry.body.as_ref().expect("checked body").clone();
                self.cache
                    .lock()
                    .expect("cache mutex")
                    .insert(url.into(), entry);
                self.stats.revalidated.fetch_add(1, Ordering::Relaxed);
                return Ok(Observation {
                    value,
                    checked_at: self.now,
                    expires_at: self.now + TTL,
                    freshness: "revalidated",
                });
            }
            self.remember_failure(url, previous, 0, self.now + 60);
            return Err(FetchFailure::new(
                "invalid-response",
                "Provider returned 304 without a cached observation for the same release endpoint",
                self.now,
            ));
        }
        if reply.status != 200 {
            let mut retry = self.now + 60;
            if reply.status == 429
                || (reply.status == 403
                    && reply
                        .headers
                        .get("x-ratelimit-remaining")
                        .is_some_and(|n| n == "0"))
            {
                retry = retry_after(&reply.headers, self.now);
                self.hold_host(host, retry, 429);
                self.stats.rate_limited.fetch_add(1, Ordering::Relaxed);
                self.remember_failure(url, previous, 429, retry);
                return Err(FetchFailure { availability: "rate-limited".into(), reason: "Provider rate limit reached; cached observations are not a fresh update verdict".into(), retry_at: retry, checked_at: self.now });
            }
            if reply.status == 503 {
                // GameSync-style service backoff, persisted across scans. A
                // provider outage is not a rate-limit or a current-version
                // result, and force-refresh must not create a retry storm.
                retry = retry_after(&reply.headers, self.now);
                self.hold_host(host, retry, 503);
            }
            self.remember_failure(url, previous, reply.status, retry);
            return Err(FetchFailure {
                availability: status_name(reply.status).into(),
                reason: format!(
                    "Provider returned HTTP {}; no current-version claim was made",
                    reply.status
                ),
                retry_at: retry,
                checked_at: self.now,
            });
        }
        if reply.body.len() > BODY_LIMIT {
            self.remember_failure(url, previous, 0, self.now + 60);
            return Err(FetchFailure::new(
                "invalid-response",
                "Provider response exceeded 2 MiB",
                self.now,
            ));
        }
        let value = match parse_json(&reply.body) {
            Ok(value) => value,
            Err(_) => {
                self.remember_failure(url, previous, 0, self.now + 60);
                return Err(FetchFailure::new(
                    "invalid-response",
                    "Invalid or ambiguous provider JSON",
                    self.now,
                ));
            }
        };
        if redirect_tag
            .as_ref()
            .is_some_and(|tag| value.get("tag_name").and_then(Value::as_str) != Some(tag.as_str()))
        {
            self.remember_failure(url, previous, 0, self.now + 60);
            return Err(FetchFailure::new(
                "invalid-response",
                "GitLab release body differs from its resolved release tag",
                self.now,
            ));
        }
        let entry = CacheEntry {
            url: url.into(),
            resolved_url,
            body: Some(cache_document(url, &value)),
            etag: reply
                .headers
                .get("etag")
                .filter(|v| clean_header(v))
                .cloned(),
            last_modified: reply
                .headers
                .get("last-modified")
                .filter(|v| clean_header(v))
                .cloned(),
            checked_at: self.now,
            expires_at: self.now + TTL,
            retry_at: 0,
            last_status: 200,
        };
        if serde_json::to_vec(&entry).is_ok_and(|v| v.len() <= ENTRY_LIMIT) {
            self.cache
                .lock()
                .expect("cache mutex")
                .insert(url.into(), entry);
        } else {
            self.cache.lock().expect("cache mutex").remove(url);
        }
        Ok(Observation {
            value,
            checked_at: self.now,
            expires_at: self.now + TTL,
            freshness: "fresh",
        })
    }
    fn hold_host(&self, host: String, until: u64, status: u16) {
        self.host_cooldowns
            .lock()
            .expect("cooldown mutex")
            .entry(host)
            .and_modify(|current| {
                if until > current.0 {
                    *current = (until, status);
                }
            })
            .or_insert((until, status));
    }

    fn remember_failure(
        &self,
        url: &str,
        previous: Option<CacheEntry>,
        status: u16,
        retry_at: u64,
    ) {
        let mut entry = previous.unwrap_or_else(|| CacheEntry {
            url: url.into(),
            checked_at: self.now,
            ..Default::default()
        });
        entry.expires_at = entry.expires_at.min(self.now);
        entry.checked_at = self.now;
        entry.retry_at = retry_at;
        entry.last_status = status;
        self.cache
            .lock()
            .expect("cache mutex")
            .insert(url.into(), entry);
    }
    pub fn export_cache(&self) -> SourceCache {
        let mut entries: Vec<_> = self
            .cache
            .lock()
            .expect("cache mutex")
            .values()
            .cloned()
            .collect();
        entries.sort_by_key(|e| std::cmp::Reverse(e.checked_at.max(e.retry_at)));
        let mut total = 0;
        entries.retain(|e| {
            let size = serde_json::to_vec(e).map_or(CACHE_LIMIT + 1, |v| v.len());
            let keep = total + size <= CACHE_LIMIT;
            if keep {
                total += size;
            }
            keep
        });
        entries.truncate(256);
        SourceCache { entries }
    }
}
fn status_name(status: u16) -> &'static str {
    match status {
        401 => "authentication-required",
        403 => "access-denied-or-challenged",
        404 => "not-found-or-private",
        410 => "gone",
        301 | 302 | 303 | 307 | 308 => "source-moved",
        429 => "rate-limited",
        _ => "unavailable",
    }
}
fn retry_after(headers: &BTreeMap<String, String>, now: u64) -> u64 {
    let seconds = headers
        .get("retry-after")
        .and_then(|v| v.parse::<u64>().ok())
        .or_else(|| {
            headers
                .get("retry-after")
                .and_then(|v| chrono::DateTime::parse_from_rfc2822(v).ok())
                .map(|d| (d.timestamp().max(0) as u64).saturating_sub(now))
        })
        .or_else(|| {
            headers
                .get("x-ratelimit-reset")
                .and_then(|v| v.parse::<u64>().ok())
                .map(|t| t.saturating_sub(now))
        })
        .unwrap_or(60)
        .clamp(1, 3600);
    now + seconds
}

// Persist only the public fields consumed by these adapters. A full provider
// account/release payload can contain unrelated profile and API metadata.
fn cache_document(url: &str, value: &Value) -> Value {
    fn fields(value: &Value, keys: &[&str]) -> Value {
        if !value.is_object() {
            return value.clone();
        }
        let mut result = serde_json::Map::new();
        for key in keys {
            if let Some(v) = value.get(*key) {
                result.insert((*key).into(), v.clone());
            }
        }
        for key in ["body", "changelog", "description"] {
            if let Some(s) = result.get(key).and_then(Value::as_str) {
                result.insert(key.into(), Value::String(s.chars().take(6000).collect()));
            }
        }
        Value::Object(result)
    }
    fn list(value: &Value, keys: &[&str]) -> Value {
        value
            .as_array()
            .map(|items| Value::Array(items.iter().map(|v| fields(v, keys)).collect()))
            .unwrap_or_else(|| value.clone())
    }
    fn person(value: &Value) -> Value {
        fields(
            value,
            &["name", "login", "html_url", "web_url", "avatar_url"],
        )
    }
    fn version(value: &Value) -> Value {
        let mut result = fields(
            value,
            &[
                "id",
                "project_id",
                "author_id",
                "version_number",
                "version_type",
                "status",
                "date_published",
                "game_versions",
                "loaders",
                "changelog",
                "dependencies",
                "files",
            ],
        );
        if let Some(files) = value.get("files") {
            result["files"] = list(files, &["filename", "url", "size", "primary", "hashes"]);
        }
        if let Some(deps) = value.get("dependencies") {
            result["dependencies"] = list(
                deps,
                &["project_id", "version_id", "dependency_type", "file_name"],
            );
        }
        result
    }
    if url.starts_with("https://api.github.com/") {
        let mut result = fields(
            value,
            &[
                "id",
                "tag_name",
                "name",
                "url",
                "html_url",
                "draft",
                "prerelease",
                "published_at",
                "body",
                "author",
                "assets",
            ],
        );
        if let Some(author) = value.get("author") {
            result["author"] = person(author);
        }
        if let Some(assets) = value.get("assets").and_then(Value::as_array) {
            result["assets"] = assets
                .iter()
                .map(|v| {
                    let mut a = fields(
                        v,
                        &[
                            "id",
                            "name",
                            "state",
                            "size",
                            "digest",
                            "browser_download_url",
                            "uploader",
                        ],
                    );
                    if let Some(uploader) = v.get("uploader") {
                        a["uploader"] = person(uploader);
                    }
                    a
                })
                .collect();
        }
        result
    } else if url.starts_with("https://gitlab.com/") {
        let mut result = fields(
            value,
            &[
                "tag_name",
                "name",
                "description",
                "released_at",
                "upcoming_release",
                "author",
                "_links",
                "assets",
            ],
        );
        if let Some(author) = value.get("author") {
            result["author"] = person(author);
        }
        if let Some(links) = value.get("_links") {
            result["_links"] = fields(links, &["self"]);
        }
        if let Some(assets) = value.get("assets") {
            result["assets"] = fields(assets, &["links"]);
            if let Some(links) = assets.get("links") {
                result["assets"]["links"] = list(links, &["id", "name", "url", "direct_asset_url"]);
            }
        }
        result
    } else if url.starts_with("https://api.modrinth.com/") {
        if let Some(versions) = value.as_array() {
            return versions.iter().map(version).collect();
        }
        if value.get("version_number").is_some() {
            return version(value);
        }
        let mut result = fields(
            value,
            &[
                "id",
                "slug",
                "team",
                "title",
                "description",
                "status",
                "project_type",
                "icon_url",
                "gallery",
            ],
        );
        if let Some(gallery) = value.get("gallery") {
            result["gallery"] = list(gallery, &["url", "title"]);
        }
        result
    } else if url.starts_with("https://thunderstore.io/") {
        let mut result = fields(
            value,
            &[
                "namespace",
                "name",
                "full_name",
                "owner",
                "is_deprecated",
                "community_listings",
                "latest",
            ],
        );
        if let Some(listings) = value.get("community_listings") {
            result["community_listings"] = list(listings, &["community", "review_status"]);
        }
        if let Some(latest) = value.get("latest") {
            result["latest"] = fields(
                latest,
                &[
                    "namespace",
                    "name",
                    "full_name",
                    "version_number",
                    "is_active",
                    "description",
                    "icon",
                    "date_created",
                    "dependencies",
                    "download_url",
                ],
            );
        }
        result
    } else {
        Value::Null
    }
}

/// Reject duplicate keys throughout the response, including identity-bearing
/// nested objects. Derived from Atlas's strict version identity contract.
pub fn parse_json(bytes: &[u8]) -> Result<Value, serde_json::Error> {
    struct Strict(Value);
    impl<'de> Deserialize<'de> for Strict {
        fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
            struct V;
            impl<'de> serde::de::Visitor<'de> for V {
                type Value = Strict;
                fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                    f.write_str("unambiguous JSON")
                }
                fn visit_bool<E: serde::de::Error>(self, v: bool) -> Result<Strict, E> {
                    Ok(Strict(Value::Bool(v)))
                }
                fn visit_i64<E: serde::de::Error>(self, v: i64) -> Result<Strict, E> {
                    Ok(Strict(v.into()))
                }
                fn visit_u64<E: serde::de::Error>(self, v: u64) -> Result<Strict, E> {
                    Ok(Strict(v.into()))
                }
                fn visit_f64<E: serde::de::Error>(self, v: f64) -> Result<Strict, E> {
                    serde_json::Number::from_f64(v)
                        .map(|n| Strict(Value::Number(n)))
                        .ok_or_else(|| E::custom("non-finite number"))
                }
                fn visit_str<E: serde::de::Error>(self, v: &str) -> Result<Strict, E> {
                    Ok(Strict(Value::String(v.into())))
                }
                fn visit_string<E: serde::de::Error>(self, v: String) -> Result<Strict, E> {
                    Ok(Strict(Value::String(v)))
                }
                fn visit_none<E: serde::de::Error>(self) -> Result<Strict, E> {
                    Ok(Strict(Value::Null))
                }
                fn visit_unit<E: serde::de::Error>(self) -> Result<Strict, E> {
                    Ok(Strict(Value::Null))
                }
                fn visit_seq<A: serde::de::SeqAccess<'de>>(
                    self,
                    mut a: A,
                ) -> Result<Strict, A::Error> {
                    let mut result = Vec::new();
                    while let Some(v) = a.next_element::<Strict>()? {
                        result.push(v.0);
                    }
                    Ok(Strict(Value::Array(result)))
                }
                fn visit_map<A: serde::de::MapAccess<'de>>(
                    self,
                    mut a: A,
                ) -> Result<Strict, A::Error> {
                    let mut result = serde_json::Map::new();
                    while let Some(k) = a.next_key::<String>()? {
                        if result.contains_key(&k) {
                            return Err(serde::de::Error::custom("duplicate JSON field"));
                        }
                        result.insert(k, a.next_value::<Strict>()?.0);
                    }
                    Ok(Strict(Value::Object(result)))
                }
            }
            d.deserialize_any(V)
        }
    }
    serde_json::from_slice::<Strict>(bytes).map(|s| s.0)
}
