//! Public release discovery and bounded owned-data accounting. No browser
//! credentials, mod paths or installer actions are involved in these helpers.
use crate::acquisition::reject_link;
use serde_json::{Value, json};
use std::{fs, io::Read, path::Path, time::Duration};
type Result<T> = std::result::Result<T, String>;
fn version(raw: &str) -> Result<[u64; 3]> {
    let parts = raw
        .strip_prefix('v')
        .unwrap_or(raw)
        .split('.')
        .map(|n| {
            if n.is_empty() || n.len() > 9 || !n.bytes().all(|b| b.is_ascii_digit()) {
                return Err("Release version is not stable numeric semver.".to_string());
            }
            n.parse::<u64>().map_err(|e| e.to_string())
        })
        .collect::<Result<Vec<_>>>()?;
    parts
        .try_into()
        .map_err(|_| "Release version must contain three components.".into())
}
pub fn release_facts(value: &Value, current: &str) -> Result<Value> {
    if value["draft"] != false || value["prerelease"] != false {
        return Err("The release endpoint did not return a stable published release.".into());
    }
    let tag = value["tag_name"]
        .as_str()
        .ok_or("Release has no version.")?;
    let available = version(tag)?;
    let installed = version(current)?;
    let expected = format!("https://github.com/Herbertofury/Modhunter/releases/tag/{tag}");
    if value["html_url"] != expected {
        return Err("Release link is not the canonical Modhunter release.".into());
    }
    let assets = value["assets"]
        .as_array()
        .ok_or("Release has no asset list.")?;
    if assets.len() > 100 {
        return Err("Release asset list exceeds its complete-result bound.".into());
    }
    let mut files = Vec::new();
    for asset in assets {
        let name = asset["name"].as_str().ok_or("Release asset has no name.")?;
        let raw = asset["browser_download_url"]
            .as_str()
            .ok_or("Release asset has no URL.")?;
        let url = url::Url::parse(raw).map_err(|e| e.to_string())?;
        if name.len() > 200
            || !crate::acquisition::valid_component(name)
            || url.scheme() != "https"
            || url.host_str() != Some("github.com")
            || url.port().is_some()
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
            || !url
                .path()
                .starts_with(&format!("/Herbertofury/Modhunter/releases/download/{tag}/"))
        {
            return Err("Release asset identity is not canonical.".into());
        }
        files.push(json!({"name":name,"bytes":asset["size"],"url":raw}));
    }
    if !files.iter().any(|f| {
        f["name"]
            .as_str()
            .is_some_and(|n| n.contains("SHA256") || n.to_ascii_lowercase().contains("checksum"))
    }) {
        return Err(
            "Published release has no checksum asset. Review it on GitHub before downloading."
                .into(),
        );
    }
    Ok(
        json!({"currentVersion":current,"availableVersion":tag.trim_start_matches('v'),"newer":available>installed,"url":expected,
        "publishedAt":value["published_at"],"releaseId":value["id"],"assets":files,"verifiedIdentity":true}),
    )
}
pub fn check_release(current: &str) -> Result<Value> {
    let response = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(20))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|e| e.to_string())?
        .get("https://api.github.com/repos/Herbertofury/Modhunter/releases/latest")
        .header("Accept", "application/vnd.github+json")
        .header("User-Agent", format!("Modhunter/{current}"))
        .send()
        .map_err(|e| e.to_string())?;
    if !response.status().is_success() {
        return Err(format!(
            "Release check failed (HTTP {}). Last verified release is retained.",
            response.status()
        ));
    }
    if response.content_length().is_some_and(|n| n > 1024 * 1024) {
        return Err("Release response is too large.".into());
    }
    let mut bytes = Vec::new();
    response
        .take(1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > 1024 * 1024 {
        return Err("Release response is too large.".into());
    }
    release_facts(
        &serde_json::from_slice(&bytes).map_err(|e| e.to_string())?,
        current,
    )
}
/// Count the complete owned tree or fail. A partial walk is never labeled total.
pub fn usage(path: &Path) -> Result<Value> {
    if !path.exists() {
        return Ok(json!({"files":0,"bytes":0,"directories":0,"present":false}));
    }
    let mut stack = vec![path.to_path_buf()];
    let (mut files, mut directories, mut bytes) = (0u64, 0u64, 0u64);
    while let Some(path) = stack.pop() {
        reject_link(&path)?;
        let meta = fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
        if meta.is_dir() {
            directories += 1;
            for child in fs::read_dir(path).map_err(|e| e.to_string())? {
                stack.push(child.map_err(|e| e.to_string())?.path());
            }
        } else if meta.is_file() {
            files += 1;
            bytes = bytes
                .checked_add(meta.len())
                .ok_or("Storage count overflow.")?;
        } else {
            return Err("Owned storage contains a non-regular file.".into());
        }
        if files + directories > 1_000_000 {
            return Err(
                "Storage inventory exceeds one million paths; no partial total is reported.".into(),
            );
        }
    }
    Ok(json!({"files":files,"directories":directories,"bytes":bytes,"present":true}))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn release() -> Value {
        json!({"id":123,"draft":false,"prerelease":false,"tag_name":"v0.11.0","html_url":"https://github.com/Herbertofury/Modhunter/releases/tag/v0.11.0","assets":[{"name":"SHA256SUMS.txt","size":512,"browser_download_url":"https://github.com/Herbertofury/Modhunter/releases/download/v0.11.0/SHA256SUMS.txt"}]})
    }
    #[test]
    fn versions_are_numeric_and_failures_are_not_current() {
        let facts = release_facts(&release(), "0.9.9").unwrap();
        assert_eq!(facts["newer"], true);
        assert_eq!(release_facts(&release(), "0.12.0").unwrap()["newer"], false);
        for tag in ["0.11", "v0.11.0-beta", "0.11.0/evil", "0.11.0.1"] {
            let mut r = release();
            r["tag_name"] = json!(tag);
            assert!(release_facts(&r, "0.10.2").is_err());
        }
    }
    #[test]
    fn release_identity_and_checksum_are_required() {
        for field in ["draft", "prerelease"] {
            let mut r = release();
            r[field] = json!(true);
            assert!(release_facts(&r, "0.10.2").is_err());
        }
        let mut r = release();
        r["html_url"] = json!("https://evil.example/");
        assert!(release_facts(&r, "0.10.2").is_err());
        let mut r = release();
        r["assets"] = json!([]);
        assert!(release_facts(&r, "0.10.2").is_err());
        let mut r = release();
        r["assets"][0]["browser_download_url"] =
            json!("https://github.com/other/repo/releases/download/v0.11.0/SHA256SUMS.txt");
        assert!(release_facts(&r, "0.10.2").is_err());
    }
    #[test]
    fn usage_counts_complete_files() {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir(dir.path().join("nested")).unwrap();
        fs::write(dir.path().join("a"), b"1234").unwrap();
        fs::write(dir.path().join("nested/b"), b"123456").unwrap();
        assert_eq!(
            usage(dir.path()).unwrap(),
            json!({"files":2,"directories":2,"bytes":10,"present":true})
        );
    }
    #[cfg(unix)]
    #[test]
    fn usage_rejects_links_without_a_partial_success() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("a"), b"1234").unwrap();
        std::os::unix::fs::symlink("a", dir.path().join("linked")).unwrap();
        assert!(usage(dir.path()).is_err());
    }
}
