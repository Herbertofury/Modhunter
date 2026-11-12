//! Exact-identity, bounded public release adapters. No downloads or credentials.
//!
//! Primary contracts: GitHub releases REST API, GitLab project release API,
//! Modrinth v2 project/version API and thunderstore-io/Thunderstore's official
//! PackageSerializerExperimental. Version comparison remains in the MO2 updater.
pub mod http;
pub mod identity;
pub mod parsers;

pub use http::{CacheEntry, HttpReply, SourceCache, SourceTransport, Validators};
pub use identity::{ProjectIdentity, canonical_source_url, project_identity, provider_of};

use crate::CoreError;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashSet};
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

fn schema() -> u32 {
    1
}
fn unknown() -> String {
    "unknown".into()
}
fn budget() -> u64 {
    45_000
}
fn workers() -> usize {
    4
}
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SourceScanRequest {
    #[serde(default = "schema")]
    pub schema_version: u32,
    pub profile_id: String,
    #[serde(default = "unknown")]
    pub game: String,
    #[serde(default = "unknown")]
    pub game_runtime: String,
    #[serde(default)]
    pub loader: String,
    pub items: Vec<SourceItem>,
    #[serde(default)]
    pub force: bool,
    #[serde(default = "budget")]
    pub budget_ms: u64,
    #[serde(default = "workers")]
    pub workers: usize,
    #[serde(default)]
    pub cache: SourceCache,
}
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SourceItem {
    pub id: String,
    #[serde(default)]
    pub installed_version: String,
    #[serde(default)]
    pub source: SourceLink,
}
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SourceLink {
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub verified: bool,
    #[serde(default)]
    pub kind: String,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SourceScanResult {
    pub schema_version: u32,
    pub profile_id: String,
    pub item_records: BTreeMap<String, Value>,
    pub cache: SourceCache,
    pub stats: Value,
    pub errors: Vec<String>,
    pub checked_at: u64,
}

pub fn scan_sources(request: &SourceScanRequest) -> Result<SourceScanResult, CoreError> {
    let transport = http::PublicTransport::new()?;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| CoreError::Invalid("System clock predates the Unix epoch".into()))?
        .as_secs();
    scan_sources_with(request, &transport, now)
}

/// The injected transport and clock make protocol boundaries repeatable without
/// contacting a public service. CLI requests cannot select either dependency.
pub fn scan_sources_with(
    request: &SourceScanRequest,
    transport: &dyn SourceTransport,
    now: u64,
) -> Result<SourceScanResult, CoreError> {
    let started = Instant::now();
    if request.schema_version != 1
        || request.profile_id.is_empty()
        || request.profile_id.len() > 1000
        || request.items.len() > 25_000
        || !(1..=8).contains(&request.workers)
        || !(100..=120_000).contains(&request.budget_ms)
        || request.loader.len() > 120
        || request.game_runtime.len() > 120
    {
        return Err(CoreError::Invalid(
            "Invalid or excessive source scan request".into(),
        ));
    }
    let mut ids = HashSet::new();
    let mut groups: BTreeMap<String, (ProjectIdentity, Vec<&SourceItem>)> = BTreeMap::new();
    let mut output = BTreeMap::new();
    let mut manual = 0;
    for item in &request.items {
        if item.id.is_empty() || item.id.len() > 1000 || !ids.insert(&item.id) {
            return Err(CoreError::Invalid(
                "Empty, duplicate or excessive source row identity".into(),
            ));
        }
        if item.source.verified
            && item.source.kind != "advisory"
            && let Some(project) = project_identity(&item.source.url)
        {
            groups
                .entry(project.key())
                .or_insert_with(|| (project, Vec::new()))
                .1
                .push(item);
        } else {
            manual += 1;
            let provider = provider_of(&item.source.url);
            let capability = manual_capability(provider);
            output.insert(item.id.clone(),json!({"outcome":"manual","provider":provider,"source":provider,
                "sourceUrl":canonical_source_url(&item.source.url),"availability":capability.0,"freshness":"unverified",
                "checkedAt":Value::Null,"reason":capability.1,"releaseAssets":[]}));
        }
    }
    if groups.len() > 2048 {
        return Err(CoreError::Invalid(
            "Source scan exceeds 2,048 distinct public projects; select a smaller batch".into(),
        ));
    }
    let http = http::HttpContext::new(
        transport,
        &request.cache,
        now,
        Duration::from_millis(request.budget_ms),
        request.force,
    );
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(request.workers)
        .build()
        .map_err(|e| CoreError::Invalid(format!("Could not create source workers: {e}")))?;
    let work: Vec<_> = groups.values().collect();
    let results: Vec<_> = pool.install(|| {
        work.par_iter()
            .map(|(id, items)| (items, parsers::fetch_project(id, &http, request, now)))
            .collect()
    });
    let mut errors = Vec::new();
    let mut output_bytes = serde_json::to_vec(&output)?.len();
    for (items, result) in results {
        if result["outcome"] == "unavailable" && errors.len() < 100 {
            errors.push(format!(
                "{} {}: {}",
                result["provider"].as_str().unwrap_or("source"),
                result["projectId"].as_str().unwrap_or(""),
                result["reason"].as_str().unwrap_or("Unavailable")
            ));
        }
        for item in items {
            let mut row = result.clone();
            row["sourceUrl"] = canonical_source_url(&item.source.url).into();
            let mut size = serde_json::to_vec(&row)?.len();
            if output_bytes.saturating_add(size) > 20 * 1024 * 1024 {
                for field in [
                    "changelog",
                    "media",
                    "releaseAssets",
                    "dependencies",
                    "publisher",
                    "author",
                ] {
                    row.as_object_mut().expect("record object").remove(field);
                }
                row["releaseAssets"] = json!([]);
                row["detailsTruncated"] = true.into();
                row["detailReason"]="Detailed metadata exceeded this scan's response budget; inspect the original release page.".into();
                size = serde_json::to_vec(&row)?.len();
            }
            output_bytes = output_bytes.saturating_add(size);
            output.insert(item.id.clone(), row);
        }
    }
    Ok(SourceScanResult {
        schema_version: 1,
        profile_id: request.profile_id.clone(),
        item_records: output,
        cache: http.export_cache(),
        stats: json!({"sourceProjects":groups.len(),"manualSources":manual,"queried":http.stats.requests.load(Ordering::Relaxed),
            "cacheHits":http.stats.cache_hits.load(Ordering::Relaxed),"revalidated":http.stats.revalidated.load(Ordering::Relaxed),
            "rateLimited":http.stats.rate_limited.load(Ordering::Relaxed),"items":request.items.len(),"workers":request.workers,
            "elapsedMs":started.elapsed().as_secs_f64()*1000.0,"budgetMs":request.budget_ms}),
        errors,
        checked_at: now,
    })
}
fn manual_capability(provider: &str) -> (&'static str, &'static str) {
    match provider {
        "nexus" => (
            "native-account",
            "Use Modhunter's native MO2 Nexus metadata/file audit for this project.",
        ),
        "curseforge" => (
            "api-key-required",
            "CurseForge API access requires an approved API key; use the original project until configured support is available.",
        ),
        "patreon" | "subscribestar" | "boosty" | "afdian" | "ko-fi" => (
            "account-or-page-required",
            "Open the original creator page in your own signed-in browser; membership visibility is not a verified mod version.",
        ),
        "loverslab" | "f95" | "aml" => (
            "account-or-page-required",
            "Open the original project/download page; forum access or timestamps alone do not establish a newer compatible file.",
        ),
        "bethesda" | "steam-workshop" => (
            "official-client",
            "Use the provider's official client for entitlement and installation; this source is retained for review.",
        ),
        "unknown" => (
            "unlinked",
            "Link the exact original project before checking its release identity.",
        ),
        _ => (
            "manual-review",
            "This source is retained, but a public release API has not established a current version. Review its original project page.",
        ),
    }
}
