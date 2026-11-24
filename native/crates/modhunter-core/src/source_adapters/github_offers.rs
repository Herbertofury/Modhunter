//! Anonymous metadata for one exact public release. Asset bytes are never requested.
use super::http::{PublicTransport, SourceTransport, Validators};
use super::identity::{decode_path_component, segment};
use super::parsers::{archive, filename};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant};
use url::Url;

#[derive(Debug, thiserror::Error)]
pub enum Failure {
    #[error("{0}")]
    Unavailable(String),
    #[error("{0}")]
    Incomplete(String),
}
fn incomplete(reason: &str) -> Failure {
    Failure::Incomplete(reason.into())
}
fn tag_ok(tag: &str) -> bool {
    !tag.is_empty()
        && tag.len() <= 512
        && !tag.chars().any(|c| c.is_control() || c == '\\')
        && tag.split('/').all(|p| !matches!(p, "" | "." | ".."))
}
fn encode(s: &str) -> String {
    url::form_urlencoded::byte_serialize(s.as_bytes())
        .collect::<String>()
        .replace('+', "%20")
}
#[derive(Debug)]
pub struct ReleasePage {
    project: String,
    tag: Option<String>,
}
impl ReleasePage {
    pub fn recognize(url: &Url) -> Option<Self> {
        if url.origin().ascii_serialization() != "https://github.com"
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
        {
            return None;
        }
        let path: Vec<_> = url
            .path()
            .trim_end_matches('/')
            .split('/')
            .skip(1)
            .collect();
        let [owner, repo, "releases", tail @ ..] = path.as_slice() else {
            return None;
        };
        if !segment(owner) || !segment(repo) {
            return None;
        }
        let tag = match tail {
            ["latest"] => None,
            ["tag" | "expanded_assets", parts @ ..] if !parts.is_empty() => {
                let tag = decode_path_component(&parts.join("/"))?;
                if !tag_ok(&tag) {
                    return None;
                }
                Some(tag)
            }
            _ => return None,
        };
        Some(Self {
            project: format!("{owner}/{repo}").to_ascii_lowercase(),
            tag,
        })
    }
    fn api(&self) -> String {
        format!("https://api.github.com/repos/{}/releases", self.project)
    }
}

pub(super) fn metadata_endpoint(url: &Url) -> bool {
    let path: Vec<_> = url.path().trim_start_matches('/').split('/').collect();
    let ["repos", owner, repo, "releases", rest @ ..] = path.as_slice() else {
        return false;
    };
    if !segment(owner) || !segment(repo) {
        return false;
    }
    match rest {
        ["latest"] => url.query().is_none(),
        ["tags", tag] => {
            url.query().is_none() && decode_path_component(tag).is_some_and(|t| tag_ok(&t))
        }
        [id, "assets"] => {
            id.parse::<u64>().is_ok_and(|n| n > 0)
                && url
                    .query()
                    .and_then(|q| q.strip_prefix("per_page=100&page="))
                    .is_some_and(|p| {
                        !p.is_empty()
                            && p.bytes().all(|c| c.is_ascii_digit())
                            && p.parse::<usize>().is_ok_and(|n| n > 0)
                    })
        }
        _ => false,
    }
}
#[derive(Debug, Clone, Deserialize, PartialEq)]
struct ApiAsset {
    id: u64,
    name: String,
    state: String,
    size: u64,
    url: String,
    browser_download_url: String,
    digest: Option<String>,
}
#[derive(Deserialize)]
struct Metadata {
    id: u64,
    tag_name: String,
    html_url: String,
    url: String,
    assets_url: String,
    published_at: String,
    draft: bool,
    prerelease: bool,
    assets: Vec<ApiAsset>,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReleaseFile {
    pub asset_id: u64,
    pub name: String,
    pub bytes: u64,
    pub sha256: Option<String>,
    pub download_url: Option<String>,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Inventory {
    pub project: String,
    pub release_id: u64,
    pub tag: String,
    pub release_url: String,
    pub published_at: String,
    pub prerelease: bool,
    pub asset_pages: usize,
    pub files: Vec<ReleaseFile>,
}
pub fn discover(page: &ReleasePage, cancelled: impl Fn() -> bool) -> Result<Inventory, Failure> {
    let http = PublicTransport::new().map_err(|e| Failure::Unavailable(e.to_string()))?;
    discover_with(page, &http, cancelled)
}
fn discover_with(
    page: &ReleasePage,
    http: &dyn SourceTransport,
    cancelled: impl Fn() -> bool,
) -> Result<Inventory, Failure> {
    let deadline = Instant::now() + Duration::from_secs(75);
    let timeout = || {
        let left = deadline.saturating_duration_since(Instant::now());
        if cancelled() || left.is_zero() {
            return Err(incomplete(
                "Public release discovery was cancelled or timed out.",
            ));
        }
        Ok(left.min(Duration::from_secs(12)))
    };
    let api = page.api();
    let endpoint = page
        .tag
        .as_ref()
        .map(|t| format!("{api}/tags/{}", encode(t)))
        .unwrap_or_else(|| format!("{api}/latest"));
    let response = http
        .release_metadata(&endpoint, timeout()?)
        .map_err(Failure::Unavailable)?;
    if response.status != 200 {
        return Err(Failure::Unavailable(format!(
            "GitHub release metadata returned HTTP {}.",
            response.status
        )));
    }
    let data: Metadata = serde_json::from_slice(&response.body)
        .map_err(|_| incomplete("GitHub supplied incomplete release metadata."))?;
    let public_page = Url::parse(&data.html_url).ok().and_then(|u| {
        if u.fragment().is_some() || !u.path().contains("/releases/tag/") {
            return None;
        }
        ReleasePage::recognize(&u)
    });
    let pinned = format!("{api}/{}", data.id);
    if data.id == 0
        || data.draft
        || !tag_ok(&data.tag_name)
        || page.tag.as_ref().is_some_and(|t| *t != data.tag_name)
        || !data.url.eq_ignore_ascii_case(&pinned)
        || !data
            .assets_url
            .eq_ignore_ascii_case(&format!("{pinned}/assets"))
        || public_page
            .is_none_or(|p| p.project != page.project || p.tag.as_deref() != Some(&data.tag_name))
        || chrono::DateTime::parse_from_rfc3339(&data.published_at).is_err()
    {
        return Err(incomplete(
            "GitHub did not confirm the selected published repository, release and tag.",
        ));
    }
    let mut embedded = HashMap::new();
    for item in data.assets {
        if embedded.insert(item.id, item).is_some() {
            return Err(incomplete("Duplicate embedded asset ID."));
        }
    }
    let mut seen = HashSet::new();
    let mut download_urls = HashSet::new();
    let mut files = Vec::new();
    let mut page_number = 0;
    let mut page_bytes = 0;
    loop {
        page_number += 1;
        let address = format!("{pinned}/assets?per_page=100&page={page_number}");
        let reply = http
            .get(&address, &Validators::default(), timeout()?)
            .map_err(Failure::Incomplete)?;
        if reply.status != 200 {
            return Err(incomplete(&format!(
                "GitHub asset page {page_number} returned HTTP {}. No partial inventory was accepted.",
                reply.status
            )));
        }
        page_bytes += reply.body.len();
        if page_bytes > 64 * 1024 * 1024 {
            return Err(incomplete(
                "GitHub release metadata exceeded the complete-inventory byte budget.",
            ));
        }
        let batch: Vec<ApiAsset> = serde_json::from_slice(&reply.body)
            .map_err(|_| incomplete("GitHub returned an incomplete asset page."))?;
        if batch.len() > 100 {
            return Err(incomplete("GitHub returned an invalid asset page size."));
        }
        // Navigation addresses are derived locally from the verified release
        // ID. A provider can return a short non-final page. Honor its next
        // relation without sending requests to URLs specified by a response.
        let more = reply.headers.get("link").is_some_and(|links| {
            links.split(',').any(|link| {
                link.split(';')
                    .skip(1)
                    .any(|param| param.trim() == "rel=\"next\"")
            })
        });
        if more && batch.is_empty() {
            return Err(incomplete("GitHub supplied a non-advancing asset page."));
        }
        let last = batch.len() < 100 && !more;
        for item in batch {
            if item.id == 0
                || !seen.insert(item.id)
                || item.name.is_empty()
                || item.name.len() > 960
                || item.name.chars().any(char::is_control)
                || !item
                    .url
                    .eq_ignore_ascii_case(&format!("{api}/assets/{}", item.id))
                || embedded.remove(&item.id).is_some_and(|old| old != item)
            {
                return Err(incomplete(
                    "GitHub asset identity changed or repeated during discovery.",
                ));
            }
            let expected = format!("{}/releases/download/", page.project);
            let exact_url = Url::parse(&item.browser_download_url).ok().filter(|url| {
                if url.origin().ascii_serialization() != "https://github.com"
                    || !url.username().is_empty()
                    || url.password().is_some()
                    || url.query().is_some()
                    || url.fragment().is_some()
                {
                    return false;
                }
                let path = url.path().trim_start_matches('/');
                let Some(prefix) = path.get(..expected.len()) else {
                    return false;
                };
                if !prefix.eq_ignore_ascii_case(&expected) {
                    return false;
                }
                let Some((tag, name)) = path[expected.len()..].rsplit_once('/') else {
                    return false;
                };
                decode_path_component(tag).as_ref() == Some(&data.tag_name)
                    && decode_path_component(name).as_ref() == Some(&item.name)
            });
            let download_url = exact_url
                .filter(|_| {
                    item.state == "uploaded"
                        && item.size > 0
                        && filename(&item.name)
                        && archive(&item.name)
                })
                .map(String::from);
            if download_url
                .as_ref()
                .is_some_and(|url| !download_urls.insert(url.clone()))
            {
                return Err(incomplete("Two GitHub assets claim the same archive URL."));
            }
            let sha256 = item
                .digest
                .as_deref()
                .and_then(|s| s.strip_prefix("sha256:"))
                .filter(|s| s.len() == 64 && s.bytes().all(|c| c.is_ascii_hexdigit()))
                .map(str::to_ascii_lowercase);
            files.push(ReleaseFile {
                asset_id: item.id,
                name: item.name,
                bytes: item.size,
                sha256,
                download_url,
            });
        }
        if last {
            break;
        }
    }
    timeout()?;
    if !embedded.is_empty() {
        return Err(incomplete(
            "GitHub pagination omitted assets present in the selected release; refresh the complete inventory.",
        ));
    }
    Ok(Inventory {
        project: page.project.clone(),
        release_id: data.id,
        tag: data.tag_name,
        release_url: data.html_url,
        published_at: data.published_at,
        prerelease: data.prerelease,
        asset_pages: page_number,
        files,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::source_adapters::HttpReply;
    use serde_json::{Value, json};
    use std::collections::BTreeMap;
    use std::sync::Mutex;

    const API: &str = "https://api.github.com/repos/owner/repo/releases";
    const SOURCE: &str = "https://github.com/Owner/Repo/releases/expanded_assets/v1";
    struct Fixture {
        header: Value,
        pages: Vec<Value>,
        next_page_after: Option<usize>,
        requests: Mutex<Vec<String>>,
    }
    impl SourceTransport for Fixture {
        fn get(&self, url: &str, _: &Validators, _: Duration) -> Result<HttpReply, String> {
            assert!(super::super::identity::api_url_allowed(url));
            self.requests.lock().unwrap().push(url.into());
            let mut status = 200;
            let mut headers = BTreeMap::new();
            let body = if url == format!("{API}/tags/v1") {
                self.header.clone()
            } else {
                let index = url
                    .strip_prefix(&format!("{API}/20/assets?per_page=100&page="))
                    .unwrap()
                    .parse::<usize>()
                    .unwrap()
                    - 1;
                if self.next_page_after == Some(index) {
                    headers.insert(
                        "link".into(),
                        "<https://untrusted.example/>; rel=\"next\"".into(),
                    );
                }
                self.pages.get(index).cloned().unwrap_or_else(|| {
                    status = 429;
                    Value::Null
                })
            };
            Ok(HttpReply {
                status,
                body: serde_json::to_vec(&body).unwrap(),
                headers,
            })
        }
    }
    fn fixture() -> Fixture {
        let mut rows: Vec<_> = (1..=230).map(|id| {
            let name = format!("file-{id}.zip");
            json!({"id":id,"name":name,"state":"uploaded","size":303,"url":format!("{API}/assets/{id}"),
                "browser_download_url":format!("https://github.com/owner/repo/releases/download/v1/{name}")})
        }).collect();
        rows[2]["browser_download_url"] =
            "https://github.com/owner/other/releases/download/v1/file-3.zip".into();
        rows[4]["state"] = "starter".into();
        rows[6]["name"] = "linux.tar.zst".into();
        let header = json!({"id":20,"tag_name":"v1","draft":false,"prerelease":false,"published_at":"2026-01-01T00:00:00Z",
            "html_url":"https://github.com/owner/repo/releases/tag/v1","url":format!("{API}/20"),
            "assets_url":format!("{API}/20/assets"),"assets":rows});
        Fixture {
            header,
            pages: rows.chunks(100).map(|x| json!(x)).collect(),
            next_page_after: None,
            requests: Mutex::default(),
        }
    }
    fn page() -> ReleasePage {
        ReleasePage::recognize(&Url::parse(SOURCE).unwrap()).unwrap()
    }

    #[test]
    fn retains_every_paged_asset_and_keeps_unreviewable_rows_manual() {
        let f = fixture();
        let inv = discover_with(&page(), &f, || false).unwrap();
        assert_eq!(
            (
                inv.asset_pages,
                inv.files.len(),
                inv.files
                    .iter()
                    .filter(|x| x.download_url.is_some())
                    .count()
            ),
            (3, 230, 227)
        );
        assert_eq!(inv.files.last().unwrap().asset_id, 230);
        assert_eq!(f.requests.lock().unwrap().len(), 4);
        assert!(
            inv.files[2].download_url.is_none()
                && inv.files[4].download_url.is_none()
                && inv.files[6].download_url.is_none()
        );
    }
    #[test]
    fn short_nonfinal_page_uses_derived_next_address() {
        let mut f = fixture();
        let subset = f.pages[0].as_array_mut().unwrap().split_off(40);
        f.pages[2].as_array_mut().unwrap().extend(subset);
        f.next_page_after = Some(0);
        let inv = discover_with(&page(), &f, || false).unwrap();
        assert_eq!(inv.files.len(), 230);
        assert!(
            f.requests
                .lock()
                .unwrap()
                .iter()
                .all(|url| url.starts_with(API))
        );
    }
    #[test]
    fn rejects_incomplete_mutated_cross_bound_and_cancelled_pages() {
        let mut f = fixture();
        f.pages.pop();
        assert!(matches!(
            discover_with(&page(), &f, || false),
            Err(Failure::Incomplete(_))
        ));
        for failure in 0..5 {
            let mut f = fixture();
            match failure {
                0 => {
                    f.pages[1].as_array_mut().unwrap().pop();
                }
                1 => f.pages[1][0] = f.pages[0][0].clone(),
                2 => f.pages[1][0]["size"] = json!(999),
                3 => f.header["tag_name"] = json!("v2"),
                _ => {
                    f.header["assets_url"] =
                        json!("https://api.github.com/repos/owner/other/releases/20/assets")
                }
            }
            assert!(matches!(
                discover_with(&page(), &f, || false),
                Err(Failure::Incomplete(_))
            ));
        }
        let f = fixture();
        assert!(discover_with(&page(), &f, || true).is_err());
        assert!(f.requests.lock().unwrap().is_empty());
    }
    #[test]
    fn only_exact_official_routes_are_anonymous_metadata_candidates() {
        for s in [
            SOURCE,
            "https://github.com/owner/repo/releases/latest",
            "https://github.com/owner/repo/releases/tag/feature%2Fv1",
        ] {
            assert!(ReleasePage::recognize(&Url::parse(s).unwrap()).is_some());
        }
        for s in [
            "https://github.com/owner/repo/releases",
            "https://github.com/user/repo/issues/1",
            "https://user@github.com/owner/repo/releases/latest",
            "https://github.com/owner/repo/releases/latest?token=x",
            "https://github.com.attacker.example/owner/repo/releases/latest",
        ] {
            assert!(ReleasePage::recognize(&Url::parse(s).unwrap()).is_none());
        }
        for s in [
            format!("{API}/20/assets?per_page=100&page=0"),
            format!("{API}/20/assets?per_page=100&page=1&token=x"),
            format!("{API}/download/v1/a.zip"),
        ] {
            assert!(!super::super::identity::api_url_allowed(&s));
        }
    }
}
