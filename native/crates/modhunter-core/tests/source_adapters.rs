use modhunter_core::source_adapters::{
    HttpReply, SourceScanRequest, SourceTransport, Validators, canonical_source_url,
    project_identity, scan_sources_with,
};
use serde_json::{Value, json};
use std::collections::{BTreeMap, VecDeque};
use std::sync::Mutex;
use std::time::Duration;

const NOW: u64 = 1_800_000_000;
const GH: &str = "https://api.github.com/repos/owner/mod/releases/latest";
struct Mock {
    replies: Mutex<BTreeMap<String, VecDeque<Result<HttpReply, String>>>>,
    calls: Mutex<Vec<(String, Validators)>>,
}
impl Mock {
    fn new() -> Self {
        Self {
            replies: Mutex::new(BTreeMap::new()),
            calls: Mutex::new(Vec::new()),
        }
    }
    fn reply(&self, url: &str, status: u16, value: Value, headers: &[(&str, &str)]) {
        self.replies
            .lock()
            .unwrap()
            .entry(url.into())
            .or_default()
            .push_back(Ok(HttpReply {
                status,
                body: serde_json::to_vec(&value).unwrap(),
                headers: headers
                    .iter()
                    .map(|(k, v)| (k.to_string(), v.to_string()))
                    .collect(),
            }));
    }
    fn raw(&self, url: &str, body: Vec<u8>) {
        self.replies
            .lock()
            .unwrap()
            .entry(url.into())
            .or_default()
            .push_back(Ok(HttpReply {
                status: 200,
                body,
                headers: BTreeMap::new(),
            }));
    }
    fn count(&self) -> usize {
        self.calls.lock().unwrap().len()
    }
}
impl SourceTransport for Mock {
    fn get(
        &self,
        url: &str,
        validators: &Validators,
        _timeout: Duration,
    ) -> Result<HttpReply, String> {
        self.calls
            .lock()
            .unwrap()
            .push((url.into(), validators.clone()));
        self.replies
            .lock()
            .unwrap()
            .get_mut(url)
            .and_then(VecDeque::pop_front)
            .unwrap_or_else(|| Err(format!("Unexpected endpoint {url}")))
    }
}
fn request(source: &str) -> SourceScanRequest {
    serde_json::from_value(json!({"schemaVersion":1,"profileId":"fixture-profile","game":"skyrimspecialedition","gameRuntime":"1.6.1170",
        "items":[{"id":"row","installedVersion":"1.0","source":{"url":source,"verified":true,"kind":"project"}}]})).unwrap()
}
fn gh() -> Value {
    json!({"id":42,"tag_name":"v2.0","name":"Version 2","url":"https://api.github.com/repos/owner/mod/releases/42",
        "html_url":"https://github.com/owner/mod/releases/tag/v2.0","draft":false,"prerelease":false,
        "published_at":"2026-11-10T09:00:00Z","body":"Fixes and migration notes.",
        "author":{"login":"Publisher","html_url":"https://github.com/Publisher","avatar_url":"https://avatars.githubusercontent.com/u/42?v=4"},
        "assets":[{"id":50,"name":"Mod.zip","state":"uploaded","size":4200,"digest":format!("sha256:{}","a".repeat(64)),
            "browser_download_url":"https://github.com/owner/mod/releases/download/v2.0/Mod.zip",
            "uploader":{"login":"Uploader","html_url":"https://github.com/Uploader"}}]})
}
fn gitlab() -> Value {
    json!({"tag_name":"v2.0","name":"Stable","description":"Updated plugin","released_at":"2026-11-10T09:00:00Z",
        "_links":{"self":"https://gitlab.com/group/subgroup/mod/-/releases/v2.0"},
        "author":{"name":"Release publisher","web_url":"https://gitlab.com/publisher"},
        "assets":{"links":[{"id":5,"name":"Mod.zip","url":"https://gitlab.com/group/subgroup/mod/uploads/abcdef/Mod.zip",
            "direct_asset_url":"https://gitlab.com/group/subgroup/mod/-/releases/v2.0/downloads/Mod.zip"}]}})
}
fn modrinth_project() -> Value {
    json!({"id":"AABBCCDD","slug":"example","team":"11223344","title":"Example","description":"Fixture mod","status":"approved","project_type":"mod",
        "icon_url":"https://cdn.modrinth.com/data/AABBCCDD/icon.png","gallery":[{"url":"https://cdn.modrinth.com/data/AABBCCDD/images/screen.png","title":"Screenshot"}]})
}
fn modrinth_version() -> Value {
    json!({"id":"VVVVVVVV","project_id":"AABBCCDD","author_id":"AAAAAAAA","name":"Example 2","version_number":"2.0","version_type":"release","status":"listed",
        "date_published":"2026-11-10T09:00:00Z","game_versions":["1.21.1"],"loaders":["fabric"],"changelog":"Changes.",
        "dependencies":[{"project_id":"DEPNAME1","version_id":null,"dependency_type":"required","file_name":null}],
        "files":[{"filename":"Example.zip","url":"https://cdn.modrinth.com/data/AABBCCDD/versions/VVVVVVVV/Example.zip",
            "size":4500,"primary":true,"hashes":{"sha512":"b".repeat(128)}}]})
}
fn version_url(req: &SourceScanRequest) -> String {
    let mut u = url::Url::parse("https://api.modrinth.com/v2/project/AABBCCDD/version").unwrap();
    u.query_pairs_mut().append_pair("include_changelog", "true");
    if req.game == "minecraft" && !req.loader.is_empty() {
        u.query_pairs_mut()
            .append_pair("loaders", &json!([req.loader]).to_string())
            .append_pair("game_versions", &json!([req.game_runtime]).to_string());
    }
    u.into()
}
fn thunderstore() -> Value {
    json!({"namespace":"Team","name":"Example","full_name":"Team-Example","owner":"Team","is_deprecated":false,
        "community_listings":[{"community":"valheim","review_status":"approved"}],
        "latest":{"namespace":"Team","name":"Example","full_name":"Team-Example-2.0.0","version_number":"2.0.0","is_active":true,
            "description":"Fixture","icon":"https://gcdn.thunderstore.io/live/repository/icons/example.png","date_created":"2026-11-10T09:00:00Z",
            "dependencies":["Dependency-Library-1.2.3"],"download_url":"https://thunderstore.io/package/download/Team/Example/2.0.0/"}})
}

#[test]
fn exact_source_identity_retains_nested_namespaces_and_rejects_credential_hosts() {
    let id = project_identity("https://gitlab.com/group/subgroup/mod/-/releases/v2.0?token=secret")
        .unwrap();
    assert_eq!(id.project_id, "group/subgroup/mod");
    assert_eq!(
        id.api_url(),
        "https://gitlab.com/api/v4/projects/group%2Fsubgroup%2Fmod/releases/permalink/latest"
    );
    assert_eq!(
        project_identity("https://gitlab.com/group/subgroup/mod")
            .unwrap()
            .project_id,
        "group/subgroup/mod"
    );
    for bad in [
        "https://github.com.evil.test/owner/mod",
        "https://user:secret@github.com/owner/mod",
        "http://github.com/owner/mod",
        "https://127.0.0.1/owner/mod",
        "https://github.com/owner/%2Fmod",
    ] {
        assert!(project_identity(bad).is_none(), "{bad}");
    }
    assert_eq!(
        canonical_source_url(
            "https://www.loverslab.com/index.php?/files/file/123/&token=secret&utm_source=test"
        )
        .unwrap(),
        "https://www.loverslab.com/index.php?/files/file/123/"
    );
}
#[test]
fn github_binds_release_assets_publisher_and_hashes_with_project_deduplication() {
    let mock = Mock::new();
    mock.reply(GH, 200, gh(), &[]);
    let mut req = request("https://github.com/Owner/Mod");
    let mut duplicate = req.items[0].clone();
    duplicate.id = "copy".into();
    duplicate.source.url = "https://github.com/owner/mod/releases".into();
    req.items.push(duplicate);
    let result = scan_sources_with(&req, &mock, NOW).unwrap();
    assert_eq!(mock.count(), 1);
    assert_eq!(result.stats["sourceProjects"], 1);
    let row = &result.item_records["row"];
    assert_eq!(row["outcome"], "release");
    assert_eq!(row["publisher"]["name"], "Publisher");
    assert!(row.get("author").is_none());
    assert_eq!(row["releaseAssets"][0]["uploader"]["name"], "Uploader");
    assert_eq!(row["releaseAssets"][0]["hashes"]["sha256"], "a".repeat(64));
    assert_eq!(
        result.item_records["copy"]["sourceUrl"],
        "https://github.com/owner/mod/releases"
    );
}
#[test]
fn github_rejects_drafts_future_or_foreign_release_identity() {
    for field in [
        "draft",
        "prerelease",
        "future",
        "foreign",
        "missingStable",
        "duplicate",
    ] {
        let mut data = gh();
        match field {
            "draft" => data["draft"] = true.into(),
            "prerelease" => data["prerelease"] = true.into(),
            "future" => data["published_at"] = "2200-01-01T00:00:00Z".into(),
            "foreign" => {
                data["url"] = "https://api.github.com/repos/owner/other/releases/42".into()
            }
            "missingStable" => {
                data.as_object_mut().unwrap().remove("prerelease");
            }
            _ => {
                let copy = data["assets"][0].clone();
                data["assets"].as_array_mut().unwrap().push(copy);
            }
        }
        let mock = Mock::new();
        mock.reply(GH, 200, data, &[]);
        let result =
            scan_sources_with(&request("https://github.com/owner/mod"), &mock, NOW).unwrap();
        assert_eq!(
            result.item_records["row"]["outcome"], "unavailable",
            "{field}"
        );
        assert!(result.item_records["row"].get("latestVersion").is_none());
    }
}
#[test]
fn github_excludes_foreign_tags_and_assets_even_on_correct_repository_host() {
    let mut data = gh();
    data["assets"][0]["browser_download_url"] =
        "https://github.com/owner/mod/releases/download/v1.0/Mod.zip".into();
    let mock = Mock::new();
    mock.reply(GH, 200, data, &[]);
    let result = scan_sources_with(&request("https://github.com/owner/mod"), &mock, NOW).unwrap();
    assert_eq!(result.item_records["row"]["outcome"], "release");
    assert!(
        result.item_records["row"]["releaseAssets"]
            .as_array()
            .unwrap()
            .is_empty()
    );
}
#[test]
fn conditional_cache_revalidates_without_refetching_fresh_projects() {
    let mock = Mock::new();
    mock.reply(GH, 200, gh(), &[("etag", "\"one\"")]);
    let mut req = request("https://github.com/owner/mod");
    let first = scan_sources_with(&req, &mock, NOW).unwrap();
    req.cache = first.cache;
    let warm = scan_sources_with(&req, &mock, NOW + 30).unwrap();
    assert_eq!(mock.count(), 1);
    assert_eq!(warm.item_records["row"]["freshness"], "cached");
    assert_eq!(warm.item_records["row"]["checkedAt"], NOW);
    req.force = true;
    mock.reply(GH, 304, Value::Null, &[]);
    let checked = scan_sources_with(&req, &mock, NOW + 31).unwrap();
    assert_eq!(checked.item_records["row"]["freshness"], "revalidated");
    assert_eq!(checked.item_records["row"]["checkedAt"], NOW + 31);
    assert_eq!(
        mock.calls.lock().unwrap()[1].1.etag.as_deref(),
        Some("\"one\"")
    );
}
#[test]
fn rate_limits_survive_process_cache_and_forced_checks() {
    let mock = Mock::new();
    mock.reply(GH, 429, Value::Null, &[("retry-after", "180")]);
    let mut req = request("https://github.com/owner/mod");
    let first = scan_sources_with(&req, &mock, NOW).unwrap();
    assert_eq!(first.item_records["row"]["availability"], "rate-limited");
    req.cache = first.cache;
    req.force = true;
    let mut other = req.items[0].clone();
    other.id = "another".into();
    other.source.url = "https://github.com/owner/another".into();
    req.items.push(other);
    let second = scan_sources_with(&req, &mock, NOW + 1).unwrap();
    assert_eq!(mock.count(), 1);
    assert_eq!(
        second.item_records["another"]["availability"],
        "rate-limited"
    );
    assert_eq!(second.item_records["row"]["retryAt"], NOW + 180);
}
#[test]
fn failed_forced_revalidation_never_returns_cached_current_evidence() {
    let mock = Mock::new();
    mock.reply(GH, 200, gh(), &[]);
    let mut req = request("https://github.com/owner/mod");
    req.cache = scan_sources_with(&req, &mock, NOW).unwrap().cache;
    mock.reply(GH, 404, Value::Null, &[]);
    req.force = true;
    let result = scan_sources_with(&req, &mock, NOW + 30).unwrap();
    assert_eq!(result.item_records["row"]["outcome"], "unavailable");
    assert_eq!(
        result.item_records["row"]["availability"],
        "not-found-or-private"
    );
    assert!(result.item_records["row"].get("latestVersion").is_none());
}
#[test]
fn rejects_oversized_ambiguous_json_and_cacheless_304() {
    for body in [
        b"{\"id\":1,\"id\":2}".to_vec(),
        vec![b' '; 2 * 1024 * 1024 + 1],
    ] {
        let mock = Mock::new();
        mock.raw(GH, body);
        let result =
            scan_sources_with(&request("https://github.com/owner/mod"), &mock, NOW).unwrap();
        assert_eq!(result.item_records["row"]["outcome"], "unavailable");
    }
    let mock = Mock::new();
    mock.reply(GH, 304, Value::Null, &[]);
    let result = scan_sources_with(&request("https://github.com/owner/mod"), &mock, NOW).unwrap();
    assert_eq!(
        result.item_records["row"]["availability"],
        "invalid-response"
    );
}
#[test]
fn cache_cannot_hide_future_timestamps_or_foreign_project_payloads() {
    let mock = Mock::new();
    mock.reply(GH, 200, gh(), &[]);
    let mut req = request("https://github.com/owner/mod");
    req.cache = scan_sources_with(&req, &mock, NOW).unwrap().cache;
    req.cache.entries[0].body.as_mut().unwrap()["url"] =
        "https://api.github.com/repos/foreign/mod/releases/42".into();
    let poisoned = scan_sources_with(&req, &mock, NOW + 1).unwrap();
    assert_eq!(poisoned.item_records["row"]["outcome"], "unavailable");
    req.cache.entries[0].checked_at = NOW + 500;
    req.cache.entries[0].expires_at = NOW + 900;
    mock.reply(GH, 200, gh(), &[]);
    assert_eq!(
        scan_sources_with(&req, &mock, NOW + 2)
            .unwrap()
            .item_records["row"]["freshness"],
        "fresh"
    );
}
#[test]
fn gitlab_checks_nested_project_and_underlying_redirect_asset() {
    let req = request("https://gitlab.com/group/subgroup/mod/-/releases");
    let url = project_identity(&req.items[0].source.url)
        .unwrap()
        .api_url();
    let mock = Mock::new();
    mock.reply(&url, 200, gitlab(), &[]);
    let result = scan_sources_with(&req, &mock, NOW).unwrap();
    assert_eq!(
        result.item_records["row"]["releaseAssets"][0]["assetKey"],
        "gitlab:5"
    );
    assert_eq!(
        result.item_records["row"]["projectId"],
        "group/subgroup/mod"
    );
    let mut evil = gitlab();
    evil["assets"]["links"][0]["url"] = "https://external.example/payload.zip".into();
    mock.reply(&url, 200, evil, &[]);
    let result = scan_sources_with(&req, &mock, NOW).unwrap();
    assert!(
        result.item_records["row"]["releaseAssets"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert_eq!(result.item_records["row"]["manualAssetCount"], 1);
}
#[test]
fn gitlab_future_prerelease_and_wrong_tag_are_not_update_evidence() {
    let req = request("https://gitlab.com/group/subgroup/mod");
    let url = project_identity(&req.items[0].source.url)
        .unwrap()
        .api_url();
    for change in ["prerelease", "future", "tag"] {
        let mut v = gitlab();
        match change {
            "prerelease" => v["tag_name"] = "v3.0-rc1".into(),
            "future" => v["released_at"] = "2200-01-01T00:00:00Z".into(),
            _ => {
                v["_links"]["self"] = "https://gitlab.com/group/subgroup/mod/-/releases/v1.0".into()
            }
        }
        let mock = Mock::new();
        mock.reply(&url, 200, v, &[]);
        assert_eq!(
            scan_sources_with(&req, &mock, NOW).unwrap().item_records["row"]["outcome"],
            "unavailable"
        );
    }
}

#[test]
fn gitlab_latest_redirect_revalidates_only_the_same_resolved_release() {
    for (location, target) in [
        (
            "/api/v4/projects/42/releases/v2.0",
            "https://gitlab.com/api/v4/projects/42/releases/v2.0",
        ),
        (
            "https://gitlab.com/api/v4/projects/group%2Fsubgroup%2Fmod/releases/v2.0",
            "https://gitlab.com/api/v4/projects/group%2Fsubgroup%2Fmod/releases/v2.0",
        ),
    ] {
        let mut req = request("https://gitlab.com/group/subgroup/mod/-/releases");
        let url = project_identity(&req.items[0].source.url)
            .unwrap()
            .api_url();
        let mock = Mock::new();
        mock.reply(&url, 302, Value::Null, &[("location", location)]);
        mock.reply(target, 200, gitlab(), &[("etag", "\"release-two\"")]);
        let first = scan_sources_with(&req, &mock, NOW).unwrap();
        assert_eq!(first.item_records["row"]["outcome"], "release");
        assert_eq!(first.cache.entries[0].url, url);
        assert_eq!(first.cache.entries[0].resolved_url.as_deref(), Some(target));
        req.cache = first.cache;
        let warm = scan_sources_with(&req, &mock, NOW + 1).unwrap();
        assert_eq!(warm.item_records["row"]["freshness"], "cached");
        assert_eq!(mock.count(), 2);
        req.force = true;
        mock.reply(&url, 302, Value::Null, &[("location", location)]);
        mock.reply(target, 304, Value::Null, &[]);
        let revalidated = scan_sources_with(&req, &mock, NOW + 2).unwrap();
        assert_eq!(revalidated.item_records["row"]["freshness"], "revalidated");
        let calls = mock.calls.lock().unwrap();
        assert_eq!(calls.len(), 4);
        assert!(calls[2].1.etag.is_none());
        assert_eq!(calls[3].1.etag.as_deref(), Some("\"release-two\""));
    }
}

#[test]
fn gitlab_latest_redirect_cannot_escape_the_original_api_project() {
    for target in [
        "https://external.example/api/v4/projects/42/releases/v2.0",
        "http://gitlab.com/api/v4/projects/42/releases/v2.0",
        "https://person:secret@gitlab.com/api/v4/projects/42/releases/v2.0",
        "https://gitlab.com:8080/api/v4/projects/42/releases/v2.0",
        "/api/v4/projects/other%2Fmod/releases/v2.0",
        "/api/v4/projects/42/releases/v2.0?private_token=secret",
        "/api/v4/projects/42/releases/v2.0#fragment",
        "/api/v4/projects/0/releases/v2.0",
        "/api/v4/projects/42/releases/permalink/latest",
        "/api/v4/projects/42/jobs/1/artifacts",
    ] {
        let req = request("https://gitlab.com/group/subgroup/mod");
        let url = project_identity(&req.items[0].source.url)
            .unwrap()
            .api_url();
        let mock = Mock::new();
        mock.reply(&url, 302, Value::Null, &[("location", target)]);
        let result = scan_sources_with(&req, &mock, NOW).unwrap();
        assert_eq!(
            result.item_records["row"]["outcome"], "unavailable",
            "{target}"
        );
        assert_eq!(result.item_records["row"]["availability"], "source-moved");
        assert_eq!(mock.count(), 1);
    }
}

#[test]
fn gitlab_redirect_requires_final_project_tag_proof_and_never_follows_twice() {
    let target = "https://gitlab.com/api/v4/projects/42/releases/v2.0";
    for change in ["project", "tag", "second-redirect"] {
        let req = request("https://gitlab.com/group/subgroup/mod");
        let url = project_identity(&req.items[0].source.url)
            .unwrap()
            .api_url();
        let mock = Mock::new();
        mock.reply(&url, 302, Value::Null, &[("location", target)]);
        let mut data = gitlab();
        match change {
            "project" => {
                data["_links"]["self"] = "https://gitlab.com/foreign/mod/-/releases/v2.0".into();
                mock.reply(target, 200, data, &[]);
            }
            "tag" => {
                data["tag_name"] = "v3.0".into();
                data["_links"]["self"] =
                    "https://gitlab.com/group/subgroup/mod/-/releases/v3.0".into();
                mock.reply(target, 200, data, &[]);
            }
            _ => mock.reply(target, 302, Value::Null, &[("location", &url)]),
        }
        let result = scan_sources_with(&req, &mock, NOW).unwrap();
        assert_eq!(
            result.item_records["row"]["outcome"], "unavailable",
            "{change}"
        );
        assert_eq!(mock.count(), 2);
    }
}

#[test]
fn gitlab_new_latest_tag_cannot_reuse_old_release_304_or_cache() {
    let old_target = "https://gitlab.com/api/v4/projects/42/releases/v2.0";
    let new_target = "https://gitlab.com/api/v4/projects/42/releases/v3.0";
    let mut req = request("https://gitlab.com/group/subgroup/mod");
    let url = project_identity(&req.items[0].source.url)
        .unwrap()
        .api_url();
    let mock = Mock::new();
    mock.reply(&url, 302, Value::Null, &[("location", old_target)]);
    mock.reply(old_target, 200, gitlab(), &[("etag", "\"release-two\"")]);
    req.cache = scan_sources_with(&req, &mock, NOW).unwrap().cache;
    req.force = true;
    mock.reply(&url, 302, Value::Null, &[("location", new_target)]);
    mock.reply(new_target, 304, Value::Null, &[]);
    let changed = scan_sources_with(&req, &mock, NOW + 1).unwrap();
    assert_eq!(changed.item_records["row"]["outcome"], "unavailable");
    assert_eq!(
        changed.item_records["row"]["availability"],
        "invalid-response"
    );
    assert!(mock.calls.lock().unwrap()[3].1.etag.is_none());
    req.cache = changed.cache;
    req.force = false;
    let retry = scan_sources_with(&req, &mock, NOW + 2).unwrap();
    assert_eq!(retry.item_records["row"]["outcome"], "unavailable");
    assert_eq!(mock.count(), 4);
}
#[test]
fn modrinth_exact_game_loader_version_and_dependencies_are_bound() {
    let mut req = request("https://modrinth.com/mod/example");
    req.game = "minecraft".into();
    req.game_runtime = "1.21.1".into();
    req.loader = "fabric".into();
    let mock = Mock::new();
    mock.reply(
        "https://api.modrinth.com/v2/project/example",
        200,
        modrinth_project(),
        &[],
    );
    let mut beta = modrinth_version();
    beta["id"] = "BBBBBBBB".into();
    beta["version_type"] = "beta".into();
    beta["version_number"] = "3.0-beta".into();
    mock.reply(
        &version_url(&req),
        200,
        json!([beta, modrinth_version()]),
        &[],
    );
    let result = scan_sources_with(&req, &mock, NOW).unwrap();
    let row = &result.item_records["row"];
    assert_eq!(row["outcome"], "release");
    assert_eq!(row["latestVersion"], "2.0");
    assert_eq!(row["projectId"], "AABBCCDD");
    assert_eq!(row["projectSlug"], "example");
    assert_eq!(row["dependencies"][0]["kind"], "required");
    assert_eq!(row["releaseAssets"][0]["hashes"]["sha512"], "b".repeat(128));
    assert_eq!(row["media"][0]["type"], "image");
    assert!(row.get("author").is_none());
}
#[test]
fn modrinth_cross_game_or_missing_loader_is_review_and_jar_is_metadata_only() {
    for minecraft in [false, true] {
        let mut req = request("https://modrinth.com/mod/example");
        if minecraft {
            req.game = "minecraft".into();
            req.game_runtime = "1.21.1".into();
        }
        let mut version = modrinth_version();
        version["files"][0]["filename"] = "Example.jar".into();
        version["files"][0]["url"] =
            "https://cdn.modrinth.com/data/AABBCCDD/versions/VVVVVVVV/Example.jar".into();
        let mock = Mock::new();
        mock.reply(
            "https://api.modrinth.com/v2/project/example",
            200,
            modrinth_project(),
            &[],
        );
        mock.reply(&version_url(&req), 200, json!([version]), &[]);
        let result = scan_sources_with(&req, &mock, NOW).unwrap();
        assert_eq!(result.item_records["row"]["outcome"], "review");
        assert_eq!(
            result.item_records["row"]["releaseAssets"][0]["installable"],
            false
        );
    }
}
#[test]
fn modrinth_rejects_foreign_duplicate_versions_bad_hashes_and_wrong_file_join() {
    let req = request("https://modrinth.com/mod/example");
    for change in ["foreign", "duplicate", "hash", "url", "primary"] {
        let mut v = modrinth_version();
        match change {
            "foreign" => v["project_id"] = "ZZZZZZZZ".into(),
            "hash" => v["files"][0]["hashes"] = json!({"sha1":"bad"}),
            "url" => {
                v["files"][0]["url"] =
                    "https://cdn.modrinth.com/data/OTHER123/versions/VVVVVVVV/Example.zip".into()
            }
            "primary" => {
                let mut file = v["files"][0].clone();
                file["filename"] = "Other.zip".into();
                file["url"] =
                    "https://cdn.modrinth.com/data/AABBCCDD/versions/VVVVVVVV/Other.zip".into();
                v["files"].as_array_mut().unwrap().push(file);
            }
            _ => {}
        }
        let versions = if change == "duplicate" {
            json!([v.clone(), v])
        } else {
            json!([v])
        };
        let mock = Mock::new();
        mock.reply(
            "https://api.modrinth.com/v2/project/example",
            200,
            modrinth_project(),
            &[],
        );
        mock.reply(&version_url(&req), 200, versions, &[]);
        assert_eq!(
            scan_sources_with(&req, &mock, NOW).unwrap().item_records["row"]["outcome"],
            "unavailable",
            "{change}"
        );
    }
}
#[test]
fn modrinth_legacy_version_number_cdn_path_remains_exactly_bound() {
    let mut req = request("https://modrinth.com/mod/example");
    req.game = "minecraft".into();
    req.game_runtime = "1.21.1".into();
    req.loader = "fabric".into();
    let mut v = modrinth_version();
    v["files"][0]["url"] = "https://cdn.modrinth.com/data/AABBCCDD/versions/2.0/Example.zip".into();
    let mock = Mock::new();
    mock.reply(
        "https://api.modrinth.com/v2/project/example",
        200,
        modrinth_project(),
        &[],
    );
    mock.reply(&version_url(&req), 200, json!([v]), &[]);
    assert_eq!(
        scan_sources_with(&req, &mock, NOW).unwrap().item_records["row"]["outcome"],
        "release"
    );
}
#[test]
fn thunderstore_requires_exact_community_and_preserves_deprecation() {
    let mut req = request("https://thunderstore.io/c/valheim/p/Team/Example/");
    req.game = "valheim".into();
    let url = project_identity(&req.items[0].source.url)
        .unwrap()
        .api_url();
    let mock = Mock::new();
    mock.reply(&url, 200, thunderstore(), &[]);
    let result = scan_sources_with(&req, &mock, NOW).unwrap();
    assert_eq!(result.item_records["row"]["outcome"], "release");
    assert_eq!(
        result.item_records["row"]["dependencies"][0]["projectId"],
        "Dependency/Library"
    );
    req.game = "skyrimspecialedition".into();
    mock.reply(&url, 200, thunderstore(), &[]);
    assert_eq!(
        scan_sources_with(&req, &mock, NOW).unwrap().item_records["row"]["outcome"],
        "review"
    );
    req.game = "valheim".into();
    let mut deprecated = thunderstore();
    deprecated["is_deprecated"] = true.into();
    mock.reply(&url, 200, deprecated, &[]);
    let result = scan_sources_with(&req, &mock, NOW).unwrap();
    assert_eq!(result.item_records["row"]["projectStatus"], "deprecated");
    assert_eq!(result.item_records["row"]["outcome"], "review");
}
#[test]
fn thunderstore_rejects_foreign_package_and_download_and_missing_listing() {
    let mut req = request("https://thunderstore.io/c/valheim/p/Team/Example/");
    req.game = "valheim".into();
    let url = project_identity(&req.items[0].source.url)
        .unwrap()
        .api_url();
    for change in ["identity", "download", "listing"] {
        let mut data = thunderstore();
        match change {
            "identity" => data["latest"]["namespace"] = "Other".into(),
            "download" => {
                data["latest"]["download_url"] =
                    "https://thunderstore.io/package/download/Team/Other/2.0.0/".into()
            }
            _ => data["community_listings"] = json!([]),
        }
        let mock = Mock::new();
        mock.reply(&url, 200, data, &[]);
        let result = scan_sources_with(&req, &mock, NOW).unwrap();
        assert_ne!(result.item_records["row"]["outcome"], "release", "{change}");
    }
}

#[test]
fn thunderstore_preserves_unreviewed_moderation_without_claiming_approval() {
    let mut req = request("https://thunderstore.io/c/valheim/p/Team/Example/");
    req.game = "valheim".into();
    let url = project_identity(&req.items[0].source.url)
        .unwrap()
        .api_url();
    for (status, expected) in [
        ("approved", "release"),
        ("unreviewed", "release"),
        ("rejected", "review"),
        ("novel-status", "review"),
        ("duplicate", "unavailable"),
    ] {
        let mut data = thunderstore();
        if status == "duplicate" {
            data["community_listings"] = json!([
                {"community":"valheim","review_status":"approved"},
                {"community":"valheim","review_status":"rejected"}
            ]);
        } else {
            data["community_listings"][0]["review_status"] = status.into();
        }
        let mock = Mock::new();
        mock.reply(&url, 200, data, &[]);
        let result = scan_sources_with(&req, &mock, NOW).unwrap();
        let row = &result.item_records["row"];
        assert_eq!(row["outcome"], expected, "{status}");
        if matches!(status, "approved" | "unreviewed" | "rejected") {
            assert_eq!(row["moderationStatus"], status);
        }
        if expected == "review" {
            assert_eq!(row["releaseAssets"][0]["installable"], false);
        }
        if status == "unreviewed" {
            req.cache = result.cache;
            let cached = scan_sources_with(&req, &mock, NOW + 1).unwrap();
            assert_eq!(cached.item_records["row"]["moderationStatus"], "unreviewed");
            req.cache.entries.clear();
        }
    }
}
#[test]
fn manual_sources_preserve_gates_without_network_or_fake_check_times() {
    for (url, availability) in [
        (
            "https://www.patreon.com/posts/example",
            "account-or-page-required",
        ),
        (
            "https://www.curseforge.com/minecraft/mc-mods/example",
            "api-key-required",
        ),
        (
            "https://steamcommunity.com/sharedfiles/filedetails/?id=123",
            "official-client",
        ),
    ] {
        let mock = Mock::new();
        let result = scan_sources_with(&request(url), &mock, NOW).unwrap();
        assert_eq!(mock.count(), 0);
        assert_eq!(result.item_records["row"]["availability"], availability);
        assert!(result.item_records["row"]["checkedAt"].is_null());
    }
}
#[test]
fn duplicate_row_ids_and_excessive_work_are_rejected_before_requests() {
    let mut req = request("https://github.com/owner/mod");
    req.items.push(req.items[0].clone());
    let mock = Mock::new();
    assert!(scan_sources_with(&req, &mock, NOW).is_err());
    assert_eq!(mock.count(), 0);
    req.items.pop();
    req.workers = 64;
    assert!(scan_sources_with(&req, &mock, NOW).is_err());
    assert_eq!(mock.count(), 0);
}

#[test]
fn public_cache_keeps_consumed_fields_and_drops_unrelated_account_metadata() {
    let mut data = gh();
    data["author"]["email"] = "not-needed@example.test".into();
    data["author"]["events_url"] = "https://api.github.com/users/publisher/events".into();
    data["unrelated_private_field"] = "not retained".into();
    let mock = Mock::new();
    mock.reply(GH, 200, data, &[]);
    let mut req = request("https://github.com/owner/mod");
    let result = scan_sources_with(&req, &mock, NOW).unwrap();
    let cache_text = serde_json::to_string(&result.cache).unwrap();
    assert!(!cache_text.contains("not-needed"));
    assert!(!cache_text.contains("events_url"));
    assert!(!cache_text.contains("unrelated_private_field"));
    req.cache = result.cache;
    let warm = scan_sources_with(&req, &mock, NOW + 1).unwrap();
    assert_eq!(warm.item_records["row"]["outcome"], "release");
    assert_eq!(warm.item_records["row"]["publisher"]["name"], "Publisher");
    assert_eq!(mock.count(), 1);
}

#[test]
fn malformed_revalidation_expires_previous_fresh_cache_until_retry() {
    let mock = Mock::new();
    mock.reply(GH, 200, gh(), &[]);
    let mut req = request("https://github.com/owner/mod");
    req.cache = scan_sources_with(&req, &mock, NOW).unwrap().cache;
    req.force = true;
    mock.raw(GH, b"{\"id\":1,\"id\":2}".to_vec());
    let failure = scan_sources_with(&req, &mock, NOW + 1).unwrap();
    assert_eq!(failure.item_records["row"]["outcome"], "unavailable");
    req.cache = failure.cache;
    req.force = false;
    let retry = scan_sources_with(&req, &mock, NOW + 2).unwrap();
    assert_eq!(retry.item_records["row"]["outcome"], "unavailable");
    assert_eq!(mock.count(), 2);
}
