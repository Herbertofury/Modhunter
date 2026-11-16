use super::{
    SourceScanRequest,
    http::{HttpContext, Observation},
    identity::{ProjectIdentity, canonical_source_url, project_identity, segment},
};
use serde_json::{Value, json};
use std::collections::HashSet;
use url::Url;

fn text(value: &Value, key: &str, limit: usize) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .chars()
        .filter(|c| !c.is_control() || matches!(c, '\n' | '\t'))
        .take(limit)
        .collect()
}
fn required(value: &Value, key: &str, limit: usize) -> Result<String, String> {
    let s = value
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("Missing {key}"))?;
    if s.trim().is_empty() || s.len() > limit || s.chars().any(char::is_control) {
        return Err(format!("Invalid {key}"));
    }
    Ok(s.to_string())
}
fn published(value: &Value, key: &str, now: u64) -> Result<String, String> {
    let raw = required(value, key, 80)?;
    let parsed =
        chrono::DateTime::parse_from_rfc3339(&raw).map_err(|_| format!("Invalid {key}"))?;
    if parsed.timestamp() <= 0 || parsed.timestamp() as u64 > now + 60 {
        return Err("Release publication time is missing, invalid, or in the future".into());
    }
    Ok(raw)
}
fn project_url(raw: &str, host: &str, prefix: &str) -> Option<String> {
    let clean = canonical_source_url(raw)?;
    let parsed = Url::parse(&clean).ok()?;
    if parsed.host_str()? != host
        || parsed.query().is_some()
        || !parsed
            .path()
            .to_ascii_lowercase()
            .starts_with(&prefix.to_ascii_lowercase())
    {
        return None;
    }
    Some(clean)
}
fn photo(raw: &str, hosts: &[&str]) -> Option<String> {
    let clean = canonical_source_url(raw)?;
    let parsed = Url::parse(&clean).ok()?;
    hosts.contains(&parsed.host_str()?).then_some(clean)
}
fn person(
    value: &Value,
    name_field: &str,
    url_field: &str,
    allowed_host: &str,
    role: &str,
) -> Value {
    let name = text(value, name_field, 180);
    if name.is_empty() {
        return Value::Null;
    }
    let profile_url = project_url(&text(value, url_field, 1800), allowed_host, "/");
    let avatar = photo(
        &text(value, "avatar_url", 1800),
        &[
            allowed_host,
            "avatars.githubusercontent.com",
            "cdn.modrinth.com",
        ],
    );
    json!({"name":name,"url":profile_url,"avatarUrl":avatar,"role":role})
}
fn string_list(value: &Value, key: &str, max: usize) -> Result<Vec<String>, String> {
    let list = value
        .get(key)
        .and_then(Value::as_array)
        .ok_or_else(|| format!("Missing {key}"))?;
    if list.len() > max {
        return Err(format!("Excessive {key}"));
    }
    list.iter()
        .map(|v| {
            v.as_str()
                .filter(|s| !s.is_empty() && s.len() <= 120 && !s.chars().any(char::is_control))
                .map(str::to_owned)
                .ok_or_else(|| format!("Invalid {key}"))
        })
        .collect()
}
fn hash_value(raw: &str, length: usize) -> Option<String> {
    (raw.len() == length && raw.bytes().all(|c| c.is_ascii_hexdigit()))
        .then(|| raw.to_ascii_lowercase())
}
fn hashes(value: &Value) -> Value {
    let mut result = serde_json::Map::new();
    for (algorithm, length) in [("sha1", 40), ("sha256", 64), ("sha512", 128)] {
        if let Some(hash) = value
            .get(algorithm)
            .and_then(Value::as_str)
            .and_then(|s| hash_value(s, length))
        {
            result.insert(algorithm.into(), hash.into());
        }
    }
    Value::Object(result)
}
fn archive(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    [".zip", ".7z", ".rar"]
        .iter()
        .any(|suffix| lower.ends_with(suffix))
}
fn filename(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 240
        && !name
            .chars()
            .any(|c| c.is_control() || matches!(c, '/' | '\\'))
        && name != "."
        && name != ".."
}
fn base(id: &ProjectIdentity, observation: &Observation) -> Value {
    json!({"outcome":"release","provider":id.provider,"projectId":id.project_id,
        "source":format!("{}-release-api", id.provider),"sourceUrl":id.source_url,
        "availability":"available","checkedAt":observation.checked_at,"expiresAt":observation.expires_at,
        "freshness":observation.freshness,"releaseAssets":[],"dependencies":[],
        "compatibility":{"status":"not-declared","reason":"This release API does not establish game/runtime compatibility; review the selected file."},
        "reason":"Published release identity verified; installed-version comparison is performed by the updater."})
}
fn set_review(result: &mut Value, reason: &str, compatibility: &str) {
    result["outcome"] = "review".into();
    result["reason"] = reason.into();
    result["compatibility"]["status"] = compatibility.into();
    result["compatibility"]["reason"] = reason.into();
    if let Some(assets) = result["releaseAssets"].as_array_mut() {
        for asset in assets {
            asset["installable"] = false.into();
        }
    }
}

pub fn github(id: &ProjectIdentity, observation: &Observation, now: u64) -> Result<Value, String> {
    let data = &observation.value;
    let release_id = data
        .get("id")
        .and_then(Value::as_u64)
        .filter(|n| *n > 0)
        .ok_or("Missing release ID")?;
    let tag = required(data, "tag_name", 120)?;
    if data.get("draft").and_then(Value::as_bool) != Some(false)
        || data.get("prerelease").and_then(Value::as_bool) != Some(false)
    {
        return Err("Latest response does not establish a published stable release".into());
    }
    let date = published(data, "published_at", now)?;
    let release_url = required(data, "html_url", 1800)?;
    let prefix = format!("/{}/releases/", id.project_id);
    if project_url(&release_url, "github.com", &prefix).is_none() {
        return Err("Release identity does not match the selected GitHub repository".into());
    }
    let page = Url::parse(&release_url).map_err(|_| "Invalid GitHub release page")?;
    let page_suffix = page
        .path()
        .get(prefix.len()..)
        .unwrap_or_default()
        .trim_end_matches('/');
    if page_suffix != format!("tag/{}", encode_segment(&tag)) && page_suffix != encode_segment(&tag)
    {
        return Err("GitHub release page/tag identity is inconsistent".into());
    }
    let expected_api = format!(
        "https://api.github.com/repos/{}/releases/{release_id}",
        id.project_id
    );
    if !required(data, "url", 1800)?.eq_ignore_ascii_case(&expected_api) {
        return Err("GitHub release API identity is inconsistent".into());
    }
    let items = data
        .get("assets")
        .and_then(Value::as_array)
        .ok_or("Missing release assets")?;
    if items.len() > 300 {
        return Err("Too many release assets for one bounded review".into());
    }
    let mut assets = Vec::new();
    let mut seen = HashSet::new();
    for item in items {
        let Some(asset_id) = item.get("id").and_then(Value::as_u64).filter(|n| *n > 0) else {
            continue;
        };
        if !seen.insert(asset_id) {
            return Err("Duplicate release asset identity".into());
        }
        let name = text(item, "name", 241);
        if !filename(&name)
            || !archive(&name)
            || item.get("state").and_then(Value::as_str) != Some("uploaded")
        {
            continue;
        }
        let Some(size) = item.get("size").and_then(Value::as_u64).filter(|n| *n > 0) else {
            continue;
        };
        let raw = text(item, "browser_download_url", 1800);
        let Some(url) = project_url(
            &raw,
            "github.com",
            &format!("/{}/releases/download/", id.project_id),
        ) else {
            continue;
        };
        let parsed = Url::parse(&url).expect("checked URL");
        let expected = format!(
            "/{}/releases/download/{}/{}",
            id.project_id,
            encode_segment(&tag),
            encode_segment(&name)
        );
        if parsed
            .path()
            .get(prefix.len()..)
            .is_none_or(|suffix| suffix != &expected[prefix.len()..])
        {
            continue;
        }
        let digest = text(item, "digest", 200);
        let mut digest_json = json!({});
        if let Some(raw_hash) = digest.strip_prefix("sha256:")
            && let Some(hash) = hash_value(raw_hash, 64)
        {
            digest_json["sha256"] = hash.into();
        }
        assets.push(json!({"assetKey":format!("github:{asset_id}"),"assetId":asset_id,"name":name,"url":url,"size":size,
            "provider":"github","projectId":id.project_id,"releaseId":release_id.to_string(),"hashes":digest_json,
            "installable":true,"uploader":person(&item["uploader"],"login","html_url","github.com","asset uploader")}));
    }
    let mut result = base(id, observation);
    result["latestVersion"] = tag.into();
    result["releaseId"] = release_id.to_string().into();
    result["releaseUrl"] = release_url.into();
    result["publishedAt"] = date.into();
    result["changelog"] = text(data, "body", 6000).into();
    result["summary"] = text(data, "name", 300).into();
    result["publisher"] = person(
        &data["author"],
        "login",
        "html_url",
        "github.com",
        "release publisher",
    );
    result["releaseAssets"] = assets.into();
    result["channel"] = "stable".into();
    Ok(result)
}
fn encode_segment(value: &str) -> String {
    // application/x-www-form-urlencoded encodes spaces as '+', not a URL path.
    url::form_urlencoded::byte_serialize(value.as_bytes())
        .collect::<String>()
        .replace('+', "%20")
}
fn looks_prerelease(tag: &str) -> bool {
    let lower = tag.to_ascii_lowercase();
    let pieces: Vec<_> = lower.split(['-', '_', '.', ' ']).collect();
    pieces.iter().any(|p| {
        matches!(
            *p,
            "alpha" | "beta" | "preview" | "nightly" | "dev" | "snapshot" | "pre"
        ) || p
            .strip_prefix("rc")
            .is_some_and(|n| n.is_empty() || n.bytes().all(|b| b.is_ascii_digit()))
    }) || lower.split_once('-').is_some_and(|(core, _)| {
        core.trim_start_matches('v').split('.').count() >= 2
            && core
                .trim_start_matches('v')
                .bytes()
                .all(|b| b.is_ascii_digit() || b == b'.')
    })
}

pub fn gitlab(id: &ProjectIdentity, observation: &Observation, now: u64) -> Result<Value, String> {
    let data = &observation.value;
    let tag = required(data, "tag_name", 120)?;
    if looks_prerelease(&tag) || data.get("upcoming_release").and_then(Value::as_bool) == Some(true)
    {
        return Err("Latest GitLab tag is a prerelease or upcoming release; stable version is not established".into());
    }
    let date = published(data, "released_at", now)?;
    let release_url = required(&data["_links"], "self", 1800)?;
    let expected = format!("/{}/-/releases/{}", id.project_id, encode_segment(&tag));
    let Some(clean) = project_url(&release_url, "gitlab.com", &expected) else {
        return Err("GitLab release does not match the selected nested project".into());
    };
    let parsed = Url::parse(&clean).expect("checked URL");
    if parsed.path().trim_end_matches('/') != expected {
        return Err("GitLab release tag identity is inconsistent".into());
    }
    let links = data["assets"]["links"]
        .as_array()
        .ok_or("Missing GitLab release asset links")?;
    if links.len() > 300 {
        return Err("Too many GitLab assets for one bounded review".into());
    }
    let mut assets = Vec::new();
    let mut seen = HashSet::new();
    for link in links {
        let Some(link_id) = link.get("id").and_then(Value::as_u64).filter(|id| *id > 0) else {
            continue;
        };
        if !seen.insert(link_id) {
            return Err("Duplicate GitLab asset identity".into());
        }
        let name = text(link, "name", 241);
        if !filename(&name) || !archive(&name) {
            continue;
        }
        let raw = link.get("url").and_then(Value::as_str).unwrap_or_default();
        let Some(url) = canonical_source_url(raw) else {
            continue;
        };
        let own_path = |raw: &str| -> bool {
            canonical_source_url(raw)
                .and_then(|u| Url::parse(&u).ok())
                .is_some_and(|parsed| {
                    parsed.host_str() == Some("gitlab.com")
                        && parsed.query().is_none()
                        && (parsed.path().starts_with(&format!("{expected}/downloads/"))
                            || parsed
                                .path()
                                .starts_with(&format!("/{}/uploads/", id.project_id))
                            || parsed
                                .path()
                                .starts_with(&format!("/{}/-/uploads/", id.project_id)))
                })
        };
        // GitLab's direct_asset_url redirects to links.url. Validate that
        // underlying target too; an official wrapper is not target evidence.
        if !own_path(&url)
            || link
                .get("direct_asset_url")
                .and_then(Value::as_str)
                .is_some_and(|u| !own_path(u))
        {
            continue;
        }
        assets.push(json!({"assetKey":format!("gitlab:{link_id}"),"assetId":link_id,"name":name,"url":url,"originUrl":url,"size":Value::Null,
            "provider":"gitlab","projectId":id.project_id,"releaseId":tag,"hashes":{},"installable":true}));
    }
    let mut result = base(id, observation);
    result["latestVersion"] = tag.clone().into();
    result["releaseId"] = tag.into();
    result["releaseUrl"] = release_url.into();
    result["publishedAt"] = date.into();
    result["changelog"] = text(data, "description", 6000).into();
    result["summary"] = text(data, "name", 300).into();
    result["publisher"] = person(
        &data["author"],
        "name",
        "web_url",
        "gitlab.com",
        "release publisher",
    );
    result["manualAssetCount"] = (links.len().saturating_sub(assets.len())).into();
    result["releaseAssets"] = assets.into();
    result["channel"] = "stable".into();
    Ok(result)
}

fn canonical_id(value: &str) -> bool {
    value.len() == 8 && value.bytes().all(|b| b.is_ascii_alphanumeric())
}
fn game_key(game: &str) -> String {
    game.to_ascii_lowercase()
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .collect()
}

pub fn modrinth(
    id: &ProjectIdentity,
    project: &Observation,
    versions: &Observation,
    request: &SourceScanRequest,
    now: u64,
) -> Result<Value, String> {
    let data = &project.value;
    let project_id = required(data, "id", 8)?;
    if !canonical_id(&project_id)
        || (id.project_id != project_id && text(data, "slug", 120) != id.project_id)
    {
        return Err("Modrinth project ID/slug does not match the selected source".into());
    }
    let status = required(data, "status", 30)?;
    if !matches!(status.as_str(), "approved" | "archived" | "unlisted") {
        return Err("Modrinth project is not a publicly published project".into());
    }
    let project_type = required(data, "project_type", 30)?;
    let requested_type = id.project_type.as_deref().unwrap_or("");
    if project_type != requested_type
        && !matches!(
            (project_type.as_str(), requested_type),
            ("mod", "plugin" | "datapack")
        )
    {
        return Err("Modrinth source type differs from the resolved project".into());
    }
    let list = versions
        .value
        .as_array()
        .ok_or("Missing Modrinth version list")?;
    if list.len() > 1000 {
        return Err("Version list exceeds the bounded review limit".into());
    }
    let mut seen = HashSet::new();
    let mut stable = Vec::new();
    for version in list {
        let version_id = required(version, "id", 8)?;
        if !canonical_id(&version_id)
            || !seen.insert(version_id.clone())
            || required(version, "project_id", 8)? != project_id
        {
            return Err("Duplicate or foreign Modrinth version identity".into());
        }
        if version.get("version_type").and_then(Value::as_str) != Some("release") {
            continue;
        }
        if version
            .get("status")
            .and_then(Value::as_str)
            .is_some_and(|s| !matches!(s, "listed" | "archived"))
        {
            continue;
        }
        let Ok(date) = published(version, "date_published", now) else {
            continue;
        };
        let loaders = string_list(version, "loaders", 100)?;
        let games = string_list(version, "game_versions", 400)?;
        let date_key = chrono::DateTime::parse_from_rfc3339(&date)
            .expect("already checked timestamp")
            .timestamp();
        stable.push((version, date_key, loaders, games));
    }
    stable.sort_by_key(|v| std::cmp::Reverse(v.1));
    let known_context = game_key(&request.game) == "minecraft"
        && !request.loader.is_empty()
        && !matches!(request.game_runtime.as_str(), "" | "unknown");
    let selected = if known_context {
        stable.iter().find(|(_, _, loaders, games)| {
            loaders.contains(&request.loader) && games.contains(&request.game_runtime)
        })
    } else {
        stable.first()
    };
    let Some((version, _, loaders, games)) = selected else {
        return Err(if known_context {
            "No published stable version matches the selected Minecraft version and loader"
        } else {
            "No published stable Modrinth version was established"
        }
        .into());
    };
    let version_id = required(version, "id", 8)?;
    let version_number = required(version, "version_number", 120)?;
    let files = version
        .get("files")
        .and_then(Value::as_array)
        .ok_or("Missing Modrinth files")?;
    if files.len() > 100 {
        return Err("Too many files for one Modrinth version".into());
    }
    let mut primary = 0;
    let mut names = HashSet::new();
    let mut assets = Vec::new();
    for file in files {
        let name = required(file, "filename", 240)?;
        if !filename(&name) || !names.insert(name.clone()) {
            return Err("Ambiguous Modrinth filename".into());
        }
        let is_primary = file
            .get("primary")
            .and_then(Value::as_bool)
            .ok_or("Missing Modrinth primary file flag")?;
        primary += usize::from(is_primary);
        let raw = required(file, "url", 1800)?;
        let Some(url) = project_url(
            &raw,
            "cdn.modrinth.com",
            &format!("/data/{project_id}/versions/"),
        ) else {
            return Err("Modrinth file does not belong to the resolved project and version".into());
        };
        let path = Url::parse(&url).expect("checked URL").path().to_string();
        let canonical_path = format!(
            "/data/{project_id}/versions/{version_id}/{}",
            encode_segment(&name)
        );
        let legacy_path = format!(
            "/data/{project_id}/versions/{}/{}",
            encode_segment(&version_number),
            encode_segment(&name)
        );
        if path != canonical_path && path != legacy_path {
            return Err("Modrinth file path/filename mismatch".into());
        }
        let size = file
            .get("size")
            .and_then(Value::as_u64)
            .filter(|n| *n > 0)
            .ok_or("Invalid Modrinth file size")?;
        let file_hashes = hashes(&file["hashes"]);
        if file_hashes.as_object().is_none_or(|h| h.is_empty()) {
            return Err("Modrinth file lacks a valid published digest".into());
        }
        assets.push(json!({"assetKey":format!("modrinth:{version_id}:{name}"),"name":name,"url":url,"size":size,
            "provider":"modrinth","projectId":project_id,"releaseId":version_id,"releaseVersion":version_number,"hashes":file_hashes,
            "primary":is_primary,"installable":archive(&name)}));
    }
    if primary > 1 {
        return Err("Multiple primary Modrinth files".into());
    }
    let dependencies = version
        .get("dependencies")
        .and_then(Value::as_array)
        .ok_or("Missing Modrinth dependency list")?;
    if dependencies.len() > 300 {
        return Err("Too many Modrinth dependencies".into());
    }
    let mut dep_records = Vec::new();
    for dependency in dependencies {
        let kind = required(dependency, "dependency_type", 20)?;
        if !matches!(
            kind.as_str(),
            "required" | "optional" | "incompatible" | "embedded"
        ) {
            return Err("Unknown dependency classification".into());
        }
        let dependency_id = dependency.get("project_id").and_then(Value::as_str);
        let version_id = dependency.get("version_id").and_then(Value::as_str);
        if dependency_id.is_some_and(|id| !canonical_id(id))
            || version_id.is_some_and(|id| !canonical_id(id))
        {
            return Err("Invalid dependency identity".into());
        }
        dep_records.push(json!({"projectId":dependency_id,"versionId":version_id,"kind":kind,
            "name":text(dependency,"file_name",240),"url":dependency_id.map(|id| format!("https://modrinth.com/project/{id}"))}));
    }
    let mut result = base(id, versions);
    // Both observations are required: project identity and selected version.
    result["checkedAt"] = project.checked_at.min(versions.checked_at).into();
    result["expiresAt"] = project.expires_at.min(versions.expires_at).into();
    result["projectId"] = project_id.clone().into();
    result["projectSlug"] = text(data, "slug", 120).into();
    result["latestVersion"] = version_number.into();
    result["releaseId"] = version_id.clone().into();
    result["releaseUrl"] =
        format!("https://modrinth.com/{requested_type}/{project_id}/version/{version_id}").into();
    result["publishedAt"] = published(version, "date_published", now)?.into();
    result["summary"] = text(data, "description", 800).into();
    result["changelog"] = text(version, "changelog", 6000).into();
    result["title"] = text(data, "title", 240).into();
    result["iconUrl"] = photo(&text(data, "icon_url", 1800), &["cdn.modrinth.com"]).into();
    result["publisherId"] = text(version, "author_id", 8).into();
    result["teamId"] = text(data, "team", 8).into();
    result["releaseAssets"] = assets.into();
    result["dependencies"] = dep_records.into();
    result["compatibility"] = json!({"status":"matched","game":"minecraft","gameVersions":games,"loaders":loaders,
        "reason":"The declared game version and loader match this profile; complete dependency and save compatibility remain unassessed."});
    result["channel"] = "stable".into();
    let media = data
        .get("gallery")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .take(6)
                .filter_map(|item| {
                    photo(&text(item, "url", 1800), &["cdn.modrinth.com"]).map(
                        |url| json!({"type":"image","url":url,"caption":text(item,"title",240)}),
                    )
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    result["media"] = media.into();
    if game_key(&request.game) != "minecraft" {
        set_review(
            &mut result,
            "This Modrinth project targets Minecraft; it does not establish an update for the selected game's MO2 profile.",
            "game-mismatch",
        );
    } else if !known_context {
        set_review(
            &mut result,
            "Choose the exact Minecraft runtime and mod loader before treating a release as a compatible update.",
            "context-required",
        );
    }
    if status == "archived" {
        result["projectStatus"] = "archived".into();
    }
    Ok(result)
}

pub fn thunderstore(
    id: &ProjectIdentity,
    observation: &Observation,
    request: &SourceScanRequest,
    now: u64,
) -> Result<Value, String> {
    let data = &observation.value;
    let (namespace, name) = id
        .project_id
        .split_once('/')
        .ok_or("Invalid Thunderstore project")?;
    if required(data, "namespace", 120)? != namespace
        || required(data, "name", 120)? != name
        || required(data, "full_name", 250)? != format!("{namespace}-{name}")
    {
        return Err(
            "Thunderstore package identity differs from the requested namespace/project".into(),
        );
    }
    let latest = &data["latest"];
    let version = required(latest, "version_number", 120)?;
    if !segment(&version)
        || looks_prerelease(&version)
        || latest.get("is_active").and_then(Value::as_bool) != Some(true)
        || required(latest, "namespace", 120)? != namespace
        || required(latest, "name", 120)? != name
        || required(latest, "full_name", 400)? != format!("{namespace}-{name}-{version}")
    {
        return Err(
            "Thunderstore latest version is inactive, non-stable or belongs to another package"
                .into(),
        );
    }
    let date = published(latest, "date_created", now)?;
    let raw_download = required(latest, "download_url", 1800)?;
    let expected =
        format!("https://thunderstore.io/package/download/{namespace}/{name}/{version}/");
    if canonical_source_url(&raw_download).as_deref() != Some(expected.as_str()) {
        return Err(
            "Thunderstore download is not bound to the exact namespace/project/version".into(),
        );
    }
    let listings = data
        .get("community_listings")
        .and_then(Value::as_array)
        .ok_or("Missing Thunderstore community bindings")?;
    if listings.len() > 300 {
        return Err("Too many Thunderstore community listings for one bounded review".into());
    }
    let matching_listings: Vec<_> = listings
        .iter()
        .filter(|listing| {
            id.community.as_deref().is_some_and(|community| {
                listing.get("community").and_then(Value::as_str) == Some(community)
            })
        })
        .collect();
    if matching_listings.len() > 1 {
        return Err("Duplicate Thunderstore community identity".into());
    }
    let moderation = matching_listings
        .first()
        .and_then(|listing| listing.get("review_status"))
        .and_then(Value::as_str)
        .filter(|status| matches!(*status, "approved" | "unreviewed" | "rejected"))
        .unwrap_or("unknown");
    let community_match = matches!(moderation, "approved" | "unreviewed");
    let profile_match = id.community.as_ref().is_some_and(|community| {
        game_key(&request.game) != "unknown" && game_key(&request.game) == game_key(community)
    });
    let mut dependencies = Vec::new();
    for dep in string_list(latest, "dependencies", 300)? {
        let parts: Vec<_> = dep.split('-').collect();
        if parts.len() != 3 || !parts.iter().all(|s| segment(s)) {
            return Err("Ambiguous Thunderstore dependency identity".into());
        }
        dependencies.push(json!({"projectId":format!("{}/{}",parts[0],parts[1]),"versionId":parts[2],"kind":"required",
            "name":parts[1],"url":format!("https://thunderstore.io/package/{}/{}/{}",parts[0],parts[1],parts[2])}));
    }
    let mut result = base(id, observation);
    result["latestVersion"] = version.clone().into();
    result["releaseId"] = version.clone().into();
    result["releaseUrl"] = id
        .community
        .as_ref()
        .map_or_else(
            || format!("https://thunderstore.io/package/{namespace}/{name}/{version}/"),
            |c| format!("https://thunderstore.io/c/{c}/p/{namespace}/{name}/v/{version}/"),
        )
        .into();
    result["publishedAt"] = date.into();
    result["summary"] = text(latest, "description", 800).into();
    result["title"] = name.into();
    result["publisher"] = json!({"name":namespace,"url":format!("https://thunderstore.io/package/{namespace}/"),"role":"publishing namespace"});
    result["iconUrl"] = photo(
        &text(latest, "icon", 1800),
        &[
            "gcdn.thunderstore.io",
            "cdn.thunderstore.io",
            "thunderstore.io",
        ],
    )
    .into();
    result["dependencies"] = dependencies.into();
    result["moderationStatus"] = moderation.into();
    result["releaseAssets"] = json!([{"assetKey":format!("thunderstore:{}/{version}",id.project_id),"name":format!("{namespace}-{name}-{version}.zip"),
        "url":expected,"size":Value::Null,"provider":"thunderstore","projectId":id.project_id,"releaseId":version,"hashes":{},"installable":true}]);
    result["compatibility"] = json!({"status":"community-matched","community":id.community,"reason":"Selected game matches the package's declared community; runtime/loader compatibility still requires review."});
    result["channel"] = "stable".into();
    if !community_match {
        set_review(
            &mut result,
            "The selected source has no exact public Thunderstore community listing, or its moderation status is rejected or unknown.",
            "community-required",
        );
    } else if !profile_match {
        set_review(
            &mut result,
            "Thunderstore community does not match the selected game profile.",
            "game-mismatch",
        );
    }
    if data.get("is_deprecated").and_then(Value::as_bool) == Some(true) {
        result["projectStatus"] = "deprecated".into();
        set_review(
            &mut result,
            "This Thunderstore package is marked deprecated; inspect its replacement and migration notes.",
            "deprecated",
        );
    }
    Ok(result)
}

/// Modhold's observed v1 response uses a numeric mod ID, an exact game/page
/// join, author.handle and explicit main/optional file kinds. No download URLs
/// are returned by this public endpoint. File scan times are not release dates.
pub fn modhold(
    id: &ProjectIdentity,
    observation: &Observation,
    request: &SourceScanRequest,
    now: u64,
) -> Result<Value, String> {
    let data = &observation.value;
    if data
        .get("id")
        .and_then(Value::as_u64)
        .map(|n| n.to_string())
        .as_deref()
        != Some(&id.project_id)
    {
        return Err("Modhold response does not match the selected mod ID".into());
    }
    let game = required(data, "game", 80)?;
    if id.community.as_deref() != Some(&game) {
        return Err("Modhold response does not match the selected game's mod".into());
    }
    let page = required(data, "page", 1800)?;
    let page_identity = project_identity(&page).ok_or("Invalid Modhold canonical page")?;
    let parsed_page = Url::parse(&page).map_err(|_| "Invalid Modhold canonical page")?;
    if page_identity.provider != "modhold"
        || page_identity.project_id != id.project_id
        || page_identity.community != id.community
        || parsed_page.host_str() != Some("modholdmods.com")
        || parsed_page.query().is_some()
        || parsed_page.fragment().is_some()
    {
        return Err("Modhold canonical page identifies a different mod or game".into());
    }
    let title = required(data, "name", 300)?;
    let version = required(data, "version", 120)?;
    let handle = required(&data["author"], "handle", 120)?;
    if !segment(&handle) {
        return Err("Invalid Modhold author handle".into());
    }
    let author_name = required(&data["author"], "name", 180)?;
    // v1 emits a timestamp without an offset. Preserve that representation;
    // a one-day bound detects clearly future data without inventing its zone.
    let site_date = |value: &Value, key: &str| -> Result<String, String> {
        let raw = required(value, key, 40)?;
        let date = chrono::NaiveDateTime::parse_from_str(&raw, "%Y-%m-%d %H:%M:%S")
            .map_err(|_| format!("Invalid Modhold {key}"))?;
        if date.and_utc().timestamp() <= 0
            || date.and_utc().timestamp() as u64 > now.saturating_add(86_400)
        {
            return Err("Modhold publication date is invalid or clearly in the future".into());
        }
        Ok(raw)
    };
    let updated = site_date(data, "updated")?;
    let published = site_date(data, "published")?;
    let files = data
        .get("files")
        .and_then(Value::as_array)
        .ok_or("Missing Modhold files")?;
    if files.len() > 300 {
        return Err("Too many Modhold files for one bounded review".into());
    }
    let mut seen = HashSet::new();
    let mut main = Vec::new();
    let mut main_versions = HashSet::new();
    for file in files {
        let file_id = file
            .get("id")
            .and_then(Value::as_u64)
            .filter(|n| *n > 0)
            .ok_or("Invalid Modhold file identity")?;
        if !seen.insert(file_id) {
            return Err("Duplicate Modhold file identity".into());
        }
        if text(file, "kind", 30) == "main" {
            let file_version = required(file, "version", 120)?;
            main_versions.insert(file_version.clone());
            if file_version == version {
                main.push((file_id, file));
            }
        }
    }
    let mut result = base(id, observation);
    result["title"] = title.clone().into();
    result["releaseName"] = title.into();
    result["latestVersion"] = version.clone().into();
    result["releaseUrl"] = page.clone().into();
    result["summary"] = text(data, "summary", 4000).into();
    result["publisherId"] = handle.clone().into();
    result["author"] = json!({"name":author_name,"handle":handle,
        "url":format!("https://modholdmods.com/u/{handle}"),"role":"mod author"});
    result["iconUrl"] = photo(&text(data, "cover", 1800), &["modholdmods.com"]).into();
    result["updatedAt"] = updated.into();
    result["projectPublishedAt"] = published.into();
    result["providerTimestampZone"] = "not-declared".into();
    result["access"] = text(data, "access", 80).into();
    result["manualAssetCount"] = files.len().into();
    result["channel"] = "not-declared".into();
    result["reason"] = "The exact Modhold mod and main-file version are verified. Downloads require the provider's sign-in and are opened for manual review.".into();
    if main.len() == 1 && main_versions.len() == 1 {
        let (file_id, file) = main[0];
        let name = required(file, "name", 240)?;
        if !filename(&name) {
            return Err("Invalid Modhold main-file name".into());
        }
        result["releaseId"] = file_id.to_string().into();
        result["releaseIdentityKind"] = "main-file".into();
        result["releaseAssets"] = json!([{
            "assetKey":format!("modhold:{file_id}"),"assetId":file_id,
            "provider":"modhold","projectId":id.project_id,"releaseId":file_id.to_string(),
            "releaseVersion":version,"name":name,"fileKind":"main",
            "url":format!("{page}?tab=files"),"size":file.get("size").and_then(Value::as_u64),
            "hashes":hashes(file),"installable":false,"downloadMode":"provider-sign-in"
        }]);
        if looks_prerelease(&version) || looks_prerelease(&name) {
            result["channel"] = "prerelease".into();
            set_review(
                &mut result,
                "The Modhold main file is marked alpha/beta or prerelease; review its channel and compatibility before updating.",
                "channel-review",
            );
        }
    } else {
        set_review(
            &mut result,
            "The page version does not identify exactly one current main file; optional files never establish the mod's update version.",
            "main-file-review",
        );
    }
    if let Some(changelog) = data.get("changelog").and_then(Value::as_array) {
        if changelog.len() > 300 {
            return Err("Too many Modhold changelog entries for one bounded review".into());
        }
        if let Some(entry) = changelog
            .iter()
            .find(|entry| entry.get("version").and_then(Value::as_str) == Some(&version))
        {
            result["changelog"] = text(entry, "body", 6000).into();
            result["publishedAt"] = site_date(entry, "at")?.into();
        }
    }
    let requested_game = game_key(&request.game);
    let game_matches = match game.as_str() {
        "skyrim-se" => matches!(requested_game.as_str(), "skyrimse" | "skyrimspecialedition"),
        "skyrim" => matches!(requested_game.as_str(), "skyrim" | "skyrimle"),
        "fallout-new-vegas" => matches!(
            requested_game.as_str(),
            "falloutnv" | "falloutnewvegas" | "newvegas"
        ),
        _ => requested_game == game_key(&game),
    };
    result["compatibility"]["game"] = game.into();
    if !game_matches {
        set_review(
            &mut result,
            "The Modhold mod's game does not match this profile; cross-game version comparison is disabled.",
            "game-mismatch",
        );
    }
    Ok(result)
}

pub(crate) fn fetch_project(
    id: &ProjectIdentity,
    http: &HttpContext<'_>,
    request: &SourceScanRequest,
    now: u64,
) -> Value {
    let observation = match http.fetch(&id.api_url()) {
        Ok(value) => value,
        Err(error) => {
            return json!({"outcome":"unavailable","provider":id.provider,"projectId":id.project_id,"source":format!("{}-release-api",id.provider),
            "sourceUrl":id.source_url,"availability":error.availability,"freshness":"unverified","checkedAt":error.checked_at,"retryAt":error.retry_at,"reason":error.reason,"releaseAssets":[]});
        }
    };
    let parsed = match id.provider.as_str() {
        "github" => github(id, &observation, now),
        "gitlab" => gitlab(id, &observation, now),
        "thunderstore" => thunderstore(id, &observation, request, now),
        "modhold" => modhold(id, &observation, request, now),
        "modrinth" => {
            let canonical = observation
                .value
                .get("id")
                .and_then(Value::as_str)
                .filter(|s| {
                    canonical_id(s)
                        && (id.project_id == *s
                            || text(&observation.value, "slug", 120) == id.project_id)
                });
            if let Some(project_id) = canonical {
                let mut url = Url::parse(&format!(
                    "https://api.modrinth.com/v2/project/{project_id}/version"
                ))
                .expect("known API URL");
                url.query_pairs_mut()
                    .append_pair("include_changelog", "true");
                if game_key(&request.game) == "minecraft"
                    && !request.loader.is_empty()
                    && !matches!(request.game_runtime.as_str(), "" | "unknown")
                {
                    url.query_pairs_mut()
                        .append_pair("loaders", &json!([request.loader]).to_string())
                        .append_pair("game_versions", &json!([request.game_runtime]).to_string());
                }
                match http.fetch(url.as_str()) {
                    Ok(versions) => modrinth(id, &observation, &versions, request, now),
                    Err(error) => {
                        return json!({"outcome":"unavailable","provider":id.provider,"projectId":id.project_id,"source":"modrinth-release-api",
                        "sourceUrl":id.source_url,"availability":error.availability,"freshness":"unverified","checkedAt":error.checked_at,"retryAt":error.retry_at,"reason":error.reason,"releaseAssets":[]});
                    }
                }
            } else {
                Err("Invalid canonical Modrinth project identity".into())
            }
        }
        _ => Err("Unsupported source adapter".into()),
    };
    parsed.unwrap_or_else(|reason|json!({"outcome":"unavailable","provider":id.provider,"projectId":id.project_id,"source":format!("{}-release-api",id.provider),
        "sourceUrl":id.source_url,"availability":"invalid-or-incompatible-metadata","freshness":observation.freshness,
        "checkedAt":observation.checked_at,"expiresAt":observation.expires_at,"reason":reason,"releaseAssets":[]}))
}
