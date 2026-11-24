//! Decode historical data only. No local HTML, instructions, or native capabilities enter this view.
use chrono::{DateTime, FixedOffset};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, HashMap},
    fs::File,
    io::Read,
    path::Path,
};

const MAX_BYTES: u64 = 128 * 1024 * 1024;

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Report {
    format: String,
    profile: String,
    profile_id: String,
    #[serde(rename = "catalogSHA256")]
    catalog_sha256: String,
    imported_at: String,
    exported_at: String,
    #[serde(skip_deserializing)]
    first_observation: Option<String>,
    #[serde(skip_deserializing)]
    last_observation: Option<String>,
    #[serde(skip_deserializing)]
    totals: BTreeMap<String, usize>,
    rows: Vec<Row>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Row {
    id: String,
    name: String,
    binding: String,
    game: Option<String>,
    project: Option<u64>,
    installed: Option<String>,
    cached: Option<String>,
    seen: Option<String>,
    verdict: String,
    reason: String,
    installed_file: Option<u64>,
    target_file: Option<u64>,
    release: Option<String>,
    project_version: Option<String>,
    scope: Option<String>,
    receipts: Vec<[u64; 2]>,
    provider_url: String,
    source_url: String,
}

// Same ordered, compact UTF-8 JSON tuple as export_nexus_history.py. Intake
// timestamps and unrelated metadata may change across imports of the same files.
fn binding(item: &Value) -> String {
    let mut fields: Vec<_> = [
        "id",
        "name",
        "installedVersion",
        "latestKnownVersion",
        "sourceGame",
        "nexusId",
        "installedFiles",
        "installationArchive",
        "ignoredVersion",
        "installedFileEvidenceIncomplete",
        "updateEligible",
        "missingFromDisk",
        "notInProfile",
        "foreign",
        "repository",
    ]
    .into_iter()
    .map(|key| &item[key])
    .collect();
    fields.extend([&item["source"]["type"], &item["source"]["url"]]);
    format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&fields).expect("JSON values serialize"))
    )
}

fn date(value: &str) -> Result<DateTime<FixedOffset>, String> {
    DateTime::parse_from_rfc3339(value)
        .map_err(|_| "Invalid historical observation date.".to_string())
}

impl Row {
    fn validate(&self, item: &Value) -> Result<(), String> {
        let game = item["sourceGame"].as_str().filter(|s| *s != "unknown");
        if self.binding != binding(item)
            || item["name"] != self.name
            || item["installedVersion"].as_str() != self.installed.as_deref()
            || item["latestKnownVersion"].as_str() != self.cached.as_deref()
            || item["nexusId"].as_u64() != self.project
            || game != self.game.as_deref()
            || !item["installedFiles"]
                .as_array()
                .map_or(self.receipts.is_empty(), |pairs| {
                    pairs.len() == self.receipts.len()
                        && pairs.iter().zip(&self.receipts).all(|(p, r)| {
                            p[0].as_u64() == Some(r[0]) && p[1].as_u64() == Some(r[1])
                        })
                })
        {
            return Err(format!(
                "The installed evidence for {} has changed. Export a snapshot for the current import.",
                self.name
            ));
        }
        if !matches!(
            self.verdict.as_str(),
            "current"
                | "candidate"
                | "ignored"
                | "review"
                | "unknown"
                | "unverified"
                | "unavailable"
                | "newer-installed"
        ) || self.reason.trim().is_empty()
        {
            return Err("Invalid historical verdict or reason.".into());
        }
        if matches!(
            self.verdict.as_str(),
            "candidate" | "current" | "ignored" | "newer-installed"
        ) && (self.seen.is_none()
            || self.installed_file.unwrap_or(0) == 0
            || self.target_file.unwrap_or(0) == 0
            || self.provider_url.is_empty()
            || (self.verdict == "candidate" && self.installed_file == self.target_file))
        {
            return Err("Exact historical verdict lacks a dated file identity.".into());
        }
        if !self.provider_url.is_empty() {
            let game = self.game.as_deref().unwrap_or("");
            if game.is_empty()
                || !game
                    .bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
                || self.project.unwrap_or(0) == 0
            {
                return Err("Invalid historical Nexus project identity.".into());
            }
            let mut expected = format!(
                "https://www.nexusmods.com/{game}/mods/{}?tab=files",
                self.project.unwrap()
            );
            if let Some(file) = self.target_file {
                expected.push_str(&format!("&file_id={file}"));
            }
            if self.provider_url != expected {
                return Err("Historical Nexus link disagrees with its file identity.".into());
            }
        }
        if !self.source_url.is_empty()
            && !url::Url::parse(&self.source_url).is_ok_and(|u| {
                u.scheme() == "https"
                    && u.host_str().is_some()
                    && u.username().is_empty()
                    && u.password().is_none()
            })
        {
            return Err("Invalid recorded source page.".into());
        }
        Ok(())
    }
}

fn decode(bytes: &[u8], catalog: &Value) -> Result<Report, String> {
    let mut report: Report = serde_json::from_slice(bytes).map_err(|_| {
        "Choose the JSON data from Modhunter's historical Nexus exporter.".to_string()
    })?;
    if report.format != "modhunter-nexus-history-v1"
        || catalog["offline"] != true
        || catalog["profileId"] != report.profile_id
        || catalog["profileName"] != report.profile
    {
        return Err("This historical report belongs to a different profile or format.".into());
    }
    date(&report.imported_at)?;
    date(&report.exported_at)?;
    if report.catalog_sha256.len() != 64
        || !report.catalog_sha256.bytes().all(|b| b.is_ascii_hexdigit())
    {
        return Err("Missing source catalog digest.".into());
    }
    let items = catalog["items"]
        .as_array()
        .ok_or("Import an offline MO2 profile first.")?;
    let mut by_id: HashMap<_, _> = items
        .iter()
        .filter_map(|item| Some((item["id"].as_str()?, item)))
        .collect();
    if items.len() != report.rows.len() || by_id.len() != items.len() {
        return Err(
            "The historical report and current profile have different row coverage.".into(),
        );
    }
    let mut earliest = None;
    let mut latest = None;
    for row in &report.rows {
        let item = by_id
            .remove(row.id.as_str())
            .ok_or("Unknown or duplicate historical row ID.")?;
        row.validate(item)?;
        if let Some(raw) = &row.seen {
            let seen = date(raw)?;
            earliest = Some(earliest.map_or(seen, |v: DateTime<FixedOffset>| v.min(seen)));
            latest = Some(latest.map_or(seen, |v: DateTime<FixedOffset>| v.max(seen)));
        }
        *report.totals.entry(row.verdict.clone()).or_default() += 1;
    }
    report.first_observation = earliest.map(|v| v.to_rfc3339());
    report.last_observation = latest.map(|v| v.to_rfc3339());
    Ok(report)
}

impl Report {
    // Only project dated evidence into its own namespace. The native update
    // authority, actions, and all provider and MO2 fields stay independent.
    pub fn apply(&self, catalog: &mut Value) {
        let rows: HashMap<_, _> = self.rows.iter().map(|row| (row.id.as_str(), row)).collect();
        for item in catalog["items"]
            .as_array_mut()
            .expect("validated item array")
        {
            let row = rows[item["id"].as_str().expect("validated item ID")];
            item["historicalNexus"] = serde_json::json!({
                "verdict": row.verdict, "reason": row.reason, "seen": row.seen,
                "installedFile": row.installed_file, "targetFile": row.target_file,
                "release": row.release, "projectVersion": row.project_version, "scope": row.scope,
                "providerUrl": row.provider_url, "sourceUrl": row.source_url
            });
        }
        catalog["historicalNexus"] = serde_json::json!({
            "totals": self.totals, "firstObservation": self.first_observation,
            "lastObservation": self.last_observation, "exportedAt": self.exported_at,
        });
    }

    pub fn encoded(&self) -> Result<String, String> {
        serde_json::to_string(self)
            .map(|s| {
                s.replace('<', "\\u003c")
                    .replace('\u{2028}', "\\u2028")
                    .replace('\u{2029}', "\\u2029")
            })
            .map_err(|e| e.to_string())
    }
}

pub fn read(path: &Path, catalog: &Value) -> Result<Report, String> {
    if !path.is_absolute()
        || !path
            .extension()
            .is_some_and(|e| e.eq_ignore_ascii_case("json"))
    {
        return Err("Choose an absolute path to a historical evidence JSON file.".into());
    }
    let file = File::open(path).map_err(|e| format!("Could not open historical evidence: {e}"))?;
    let meta = file.metadata().map_err(|e| e.to_string())?;
    if !meta.is_file() || meta.len() > MAX_BYTES {
        return Err("Historical evidence must be a regular JSON file of at most 128 MiB.".into());
    }
    let mut bytes = Vec::new();
    file.take(MAX_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_BYTES {
        return Err("Historical evidence exceeds 128 MiB.".into());
    }
    decode(&bytes, catalog)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn fixture() -> (Value, Value) {
        let item = json!({"id":"id1","name":"Snow ☃","installedVersion":"2.6","sourceGame":"skyrimspecialedition",
            "nexusId":50307,"installedFiles":[[50307,252976]],"installationArchive":"Wolf.zip","updateEligible":true,
            "source":{"type":"nexus","url":"https://www.nexusmods.com/skyrimspecialedition/mods/50307"}});
        let digest = binding(&item);
        assert_eq!(
            digest,
            "11bfff5eb3e13ad98ca27afbc5fd290ff7d89025fd3e042b8c3950238377ab74"
        );
        let catalog =
            json!({"profileId":"fixture","profileName":"QA","offline":true,"items":[item]});
        let row = json!({"id":"id1","binding":digest,"name":"Snow ☃","game":"skyrimspecialedition","project":50307,
            "installed":"2.6","seen":"2026-11-24T18:00:00Z","verdict":"current","reason":"<script>test</script>",
            "installedFile":252976,"targetFile":252976,"receipts":[[50307,252976]],
            "providerUrl":"https://www.nexusmods.com/skyrimspecialedition/mods/50307?tab=files&file_id=252976","sourceUrl":""});
        let report = json!({"format":"modhunter-nexus-history-v1","profile":"QA","profileId":"fixture",
            "catalogSHA256":"a".repeat(64),"importedAt":"2026-11-24T19:00:00Z","exportedAt":"2026-11-24T20:00:00Z",
            "totals":{"candidate":200},"rows":[row]});
        (catalog, report)
    }

    #[test]
    fn file_reader_requires_a_local_json_report_within_the_size_limit() {
        let (catalog, report) = fixture();
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("history.json");
        let bytes = serde_json::to_vec(&report).unwrap();
        std::fs::write(&path, &bytes).unwrap();
        assert_eq!(
            read(&path, &catalog).unwrap().encoded().unwrap(),
            decode(&bytes, &catalog).unwrap().encoded().unwrap()
        );
        assert!(read(Path::new("history.json"), &catalog).is_err());
        let other = directory.path().join("history.html");
        std::fs::rename(&path, &other).unwrap();
        assert!(read(&other, &catalog).is_err());
        File::create(&path).unwrap().set_len(MAX_BYTES + 1).unwrap();
        assert!(read(&path, &catalog).unwrap_err().contains("128 MiB"));
    }

    #[test]
    fn native_decodes_data_and_recounts_without_interpreting_markup() {
        let (catalog, report) = fixture();
        let validated = decode(&serde_json::to_vec(&report).unwrap(), &catalog).unwrap();
        let text = validated.encoded().unwrap();
        let mut decorated = catalog.clone();
        validated.apply(&mut decorated);
        assert_eq!(
            decorated["items"][0]["historicalNexus"]["targetFile"],
            252976
        );
        assert_eq!(
            decorated["historicalNexus"]["totals"],
            serde_json::json!({"current":1})
        );
        decorated.as_object_mut().unwrap().remove("historicalNexus");
        decorated["items"][0]
            .as_object_mut()
            .unwrap()
            .remove("historicalNexus");
        assert_eq!(decorated, catalog);
        assert!(!text.contains("<script>"));
        let result: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(result["totals"], json!({"current":1}));
        assert_eq!(result["rows"][0]["reason"], "<script>test</script>");
        assert_eq!(result["lastObservation"], "2026-11-24T18:00:00+00:00");
        assert!(decode(b"<script>alert(1)</script>", &catalog).is_err());
    }

    #[test]
    fn changed_install_or_fabricated_coverage_is_rejected() {
        let (catalog, report) = fixture();
        for (key, changed) in [
            ("installedVersion", json!("3")),
            ("ignoredVersion", json!("4")),
            ("installedFiles", json!([[50307, 99]])),
            ("installationArchive", json!("different.7z")),
            ("installedFileEvidenceIncomplete", json!(true)),
        ] {
            let mut changed_catalog = catalog.clone();
            changed_catalog["items"][0][key] = changed;
            assert!(
                decode(&serde_json::to_vec(&report).unwrap(), &changed_catalog).is_err(),
                "{key}"
            );
        }
        for (pointer, changed) in [
            ("/profileId", json!("another")),
            ("/rows", json!([])),
            ("/rows/0/verdict", json!("candidate")),
            ("/rows/0/providerUrl", json!("https://example.org/")),
            ("/rows/0/seen", json!("not a date")),
            ("/rows/0/receipts", json!([[50307, 999]])),
        ] {
            let mut changed_report = report.clone();
            *changed_report.pointer_mut(pointer).unwrap() = changed;
            assert!(
                decode(&serde_json::to_vec(&changed_report).unwrap(), &catalog).is_err(),
                "{pointer}"
            );
        }
    }
}
