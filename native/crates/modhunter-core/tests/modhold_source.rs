//! Protocol fixtures follow the public Modhold v1 response observed 2026-11-16.
//! No accounts, downloads or site credentials are involved.
use modhunter_core::source_adapters::identity::api_url_allowed;
use modhunter_core::source_adapters::{
    HttpReply, SourceScanRequest, SourceTransport, Validators, project_identity, provider_of,
    scan_sources_with,
};
use serde_json::{Value, json};
use std::collections::VecDeque;
use std::sync::Mutex;
use std::time::Duration;

const NOW: u64 = 1_800_000_000;
const PAGE: &str = "https://modholdmods.com/g/skyrim-se/mods/11/fixture-estate";
const API: &str = "https://modholdmods.com/api/v1/mods/11";

#[derive(Default)]
struct Mock {
    replies: Mutex<VecDeque<HttpReply>>,
    calls: Mutex<Vec<(String, Validators)>>,
}
impl Mock {
    fn push(&self, status: u16, body: Value, headers: &[(&str, &str)]) {
        self.replies.lock().unwrap().push_back(HttpReply {
            status,
            body: serde_json::to_vec(&body).unwrap(),
            headers: headers
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
        });
    }
}
impl SourceTransport for Mock {
    fn get(&self, url: &str, validators: &Validators, _: Duration) -> Result<HttpReply, String> {
        self.calls
            .lock()
            .unwrap()
            .push((url.into(), validators.clone()));
        assert_eq!(url, API);
        self.replies
            .lock()
            .unwrap()
            .pop_front()
            .ok_or_else(|| "Unexpected request".into())
    }
}
fn request() -> SourceScanRequest {
    serde_json::from_value(json!({
        "schemaVersion":1,"profileId":"fixture-profile","game":"skyrimspecialedition","gameRuntime":"1.6.1170",
        "items":[{"id":"row","installedVersion":"0.1.20","source":{"url":PAGE,"verified":true,"kind":"project","expectedAuthor":"SethSaidThat"}}]
    })).unwrap()
}
fn data() -> Value {
    json!({"id":11,"name":"Fixture estate","summary":"Protocol fixture","version":"0.1.21","game":"skyrim-se",
        "author":{"handle":"SethSaidThat","name":"SethSaidThat"},"access":"free",
        "updated":"2026-11-14 18:10:48","published":"2026-10-25 03:00:53","page":PAGE,
        "cover":"https://modholdmods.com/media/fixture/cover.webp",
        "files":[
            {"id":541,"kind":"main","label":"Main file","name":"Estate 0.1.21.zip","version":"0.1.21","size":47022769,"sha256":"a".repeat(64),"scanned":"2026-11-16 19:06:06"},
            {"id":586,"kind":"optional","label":"Add-on","name":"Addon 99.0.0.zip","version":"99.0.0","size":2657,"sha256":"b".repeat(64),"scanned":"2026-11-17 19:06:06"}],
        "changelog":[{"version":"0.1.21","body":"Fixture changes.","at":"2026-11-12 18:40:37"}]})
}

#[test]
fn modhold_identity_is_exact_game_and_numeric_mod_id() {
    let id = project_identity(PAGE).unwrap();
    assert_eq!(id.provider, "modhold");
    assert_eq!(id.project_id, "11");
    assert_eq!(id.community.as_deref(), Some("skyrim-se"));
    assert_eq!(id.api_url(), API);
    assert_eq!(
        project_identity("https://www.modholdmods.com/g/skyrim-se/mods/11/renamed?tab=files")
            .unwrap()
            .key(),
        id.key()
    );
    for bad in [
        "https://modholdmods.com.evil.test/g/skyrim-se/mods/11/fixture",
        "https://evil.modholdmods.com/g/skyrim-se/mods/11/fixture",
        "https://user:secret@modholdmods.com/g/skyrim-se/mods/11/fixture",
        "https://modholdmods.com/g/skyrim-se/mods/0/fixture",
        "https://modholdmods.com/g/skyrim-se/mods/011/fixture",
        "https://modholdmods.com/g/skyrim-se/mods/%31%31/fixture",
        "https://modholdmods.com/g/skyrim-se/mods/9007199254740992/fixture",
        "https://modholdmods.com/g/skyrim-se/mods/11/fixture/files",
        "https://modholdmods.com/g/skyrim-se/mods/11//fixture",
        "https://modholdmods.com/u/SethSaidThat",
        "https://modholdmods.com/g/skyrim-se/mods?author=SethSaidThat",
    ] {
        assert!(project_identity(bad).is_none(), "{bad}");
    }
    assert_eq!(
        provider_of("https://modholdmods.com/u/SethSaidThat"),
        "modhold"
    );
    assert!(api_url_allowed(API));
    for bad in [
        "https://modholdmods.com/api/v1/mods/11/download",
        "https://modholdmods.com/api/v1/files/541/download",
        "https://modholdmods.com/api/v1/mods/11?token=secret",
        "https://www.modholdmods.com/api/v1/mods/11",
    ] {
        assert!(!api_url_allowed(bad));
    }
}

#[test]
fn modhold_main_file_and_author_are_bound_without_download_claims() {
    let mock = Mock::default();
    mock.push(200, data(), &[]);
    let result = scan_sources_with(&request(), &mock, NOW).unwrap();
    let row = &result.item_records["row"];
    assert_eq!(row["outcome"], "release");
    assert_eq!(row["latestVersion"], "0.1.21");
    assert_eq!(row["releaseId"], "541");
    assert_eq!(row["releaseIdentityKind"], "main-file");
    assert_eq!(
        row["author"]["url"],
        "https://modholdmods.com/u/SethSaidThat"
    );
    assert_eq!(row["publisherId"], "SethSaidThat");
    assert_eq!(row["publishedAt"], "2026-11-12 18:40:37");
    assert_eq!(row["updatedAt"], "2026-11-14 18:10:48");
    assert_eq!(row["releaseAssets"].as_array().unwrap().len(), 1);
    assert_eq!(row["releaseAssets"][0]["assetId"], 541);
    assert_eq!(row["releaseAssets"][0]["hashes"]["sha256"], "a".repeat(64));
    assert_eq!(row["releaseAssets"][0]["installable"], false);
    assert_eq!(row["manualAssetCount"], 2);
    assert_eq!(result.stats["queried"], 1);
}

#[test]
fn modhold_deduplicates_project_reads_but_author_pins_are_per_row() {
    let mock = Mock::default();
    mock.push(200, data(), &[]);
    let mut req = request();
    let mut wrong = req.items[0].clone();
    wrong.id = "wrong-author".into();
    wrong.source.expected_author = Some("AnotherAuthor".into());
    req.items.push(wrong);
    let result = scan_sources_with(&req, &mock, NOW).unwrap();
    assert_eq!(result.stats["sourceProjects"], 1);
    assert_eq!(mock.calls.lock().unwrap().len(), 1);
    assert_eq!(result.item_records["row"]["outcome"], "release");
    assert_eq!(
        result.item_records["wrong-author"]["outcome"],
        "unavailable"
    );
    assert_eq!(
        result.item_records["wrong-author"]["availability"],
        "author-identity-unverified"
    );
    assert!(
        result.item_records["wrong-author"]["releaseAssets"]
            .as_array()
            .unwrap()
            .is_empty()
    );
}

#[test]
fn modhold_rejects_same_title_foreign_ids_games_authors_and_ambiguous_files() {
    let mut mutations = Vec::new();
    let mut foreign_id = data();
    foreign_id["id"] = 12.into();
    mutations.push(foreign_id);
    let mut foreign_game = data();
    foreign_game["game"] = "fallout-4".into();
    mutations.push(foreign_game);
    let mut foreign_page = data();
    foreign_page["page"] = "https://modholdmods.com/g/skyrim-se/mods/12/fixture-estate".into();
    mutations.push(foreign_page);
    let mut foreign_host = data();
    foreign_host["page"] =
        "https://modholdmods.com.evil.test/g/skyrim-se/mods/11/fixture-estate".into();
    mutations.push(foreign_host);
    let mut bad_author = data();
    bad_author["author"]["handle"] = "../../someone".into();
    mutations.push(bad_author);
    let mut duplicates = data();
    duplicates["files"][1]["id"] = 541.into();
    mutations.push(duplicates);
    let mut future = data();
    future["updated"] = "2099-01-01 00:00:00".into();
    mutations.push(future);
    for value in mutations {
        let mock = Mock::default();
        mock.push(200, value, &[]);
        let result = scan_sources_with(&request(), &mock, NOW).unwrap();
        assert_eq!(result.item_records["row"]["outcome"], "unavailable");
    }
    for value in [
        {
            let mut v = data();
            v["version"] = "99.0.0".into();
            v
        },
        {
            let mut v = data();
            v["files"][0]["kind"] = "optional".into();
            v
        },
        {
            let mut v = data();
            v["files"][1]["kind"] = "main".into();
            v
        },
    ] {
        let mock = Mock::default();
        mock.push(200, value, &[]);
        let result = scan_sources_with(&request(), &mock, NOW).unwrap();
        assert_eq!(result.item_records["row"]["outcome"], "review");
        assert_eq!(
            result.item_records["row"]["compatibility"]["status"],
            "main-file-review"
        );
    }
}

#[test]
fn modhold_game_and_prerelease_review_remain_visible() {
    let mock = Mock::default();
    mock.push(200, data(), &[]);
    let mut req = request();
    req.game = "fallout4".into();
    let result = scan_sources_with(&req, &mock, NOW).unwrap();
    assert_eq!(result.item_records["row"]["outcome"], "review");
    assert_eq!(
        result.item_records["row"]["compatibility"]["status"],
        "game-mismatch"
    );
    let mut beta = data();
    beta["files"][0]["name"] = "Estate 0.1.21 Beta.zip".into();
    mock.push(200, beta, &[]);
    let result = scan_sources_with(&request(), &mock, NOW).unwrap();
    assert_eq!(result.item_records["row"]["outcome"], "review");
    assert_eq!(result.item_records["row"]["latestVersion"], "0.1.21");
    assert_eq!(result.item_records["row"]["channel"], "prerelease");
}

#[test]
fn modhold_cache_preserves_last_good_on_failure_and_respects_cooldowns() {
    let mock = Mock::default();
    mock.push(200, data(), &[("etag", "modhold-fixture")]);
    let mut req = request();
    let first = scan_sources_with(&req, &mock, NOW).unwrap();
    assert_eq!(
        first.cache.entries[0].body.as_ref().unwrap()["files"][0].get("scanned"),
        None
    );
    req.cache = first.cache;
    let cached = scan_sources_with(&req, &mock, NOW + 30).unwrap();
    assert_eq!(cached.stats["cacheHits"], 1);
    assert_eq!(mock.calls.lock().unwrap().len(), 1);
    mock.push(429, Value::Null, &[("retry-after", "120")]);
    req.force = true;
    let failed = scan_sources_with(&req, &mock, NOW + 40).unwrap();
    assert_eq!(failed.item_records["row"]["availability"], "rate-limited");
    assert_eq!(failed.item_records["row"]["outcome"], "unavailable");
    assert_eq!(
        failed.cache.entries[0].body.as_ref().unwrap()["version"],
        "0.1.21"
    );
    assert!(failed.cache.entries[0].expires_at <= NOW + 40);
    req.cache = failed.cache;
    let cooldown = scan_sources_with(&req, &mock, NOW + 50).unwrap();
    assert_eq!(cooldown.stats["queried"], 0);
    assert_eq!(cooldown.item_records["row"]["outcome"], "unavailable");
    mock.push(304, Value::Null, &[]);
    let recovered = scan_sources_with(&req, &mock, NOW + 161).unwrap();
    assert_eq!(recovered.stats["revalidated"], 1);
    assert_eq!(recovered.item_records["row"]["latestVersion"], "0.1.21");
}

#[test]
fn modhold_http_failures_are_not_ban_or_removal_proof() {
    for (status, availability) in [
        (403, "access-denied-or-challenged"),
        (404, "not-found-or-private"),
        (503, "unavailable"),
    ] {
        let mock = Mock::default();
        mock.push(status, Value::Null, &[]);
        let result = scan_sources_with(&request(), &mock, NOW).unwrap();
        let row = &result.item_records["row"];
        assert_eq!(row["availability"], availability);
        assert_eq!(row["outcome"], "unavailable");
        assert!(row.get("latestVersion").is_none());
        assert!(row.get("authorStatus").is_none());
    }
}
