use serde::{Deserialize, Serialize};
use url::{Host, Url};

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectIdentity {
    pub provider: String,
    pub project_id: String,
    pub source_url: String,
    pub community: Option<String>,
    pub project_type: Option<String>,
}

pub fn canonical_source_url(raw: &str) -> Option<String> {
    if raw.len() > 1800 || raw.chars().any(|c| c.is_control() || c == '\\') {
        return None;
    }
    let mut url = Url::parse(raw.trim()).ok()?;
    if url.scheme() != "https"
        || !url.username().is_empty()
        || url.password().is_some()
        || url.port().is_some()
    {
        return None;
    }
    match url.host() {
        Some(Host::Domain(host))
            if host.contains('.')
                && !host.ends_with('.')
                && !host.ends_with(".local")
                && !host.ends_with(".localhost")
                && host != "localhost" => {}
        _ => return None,
    }
    if let Some(query) = url.query() {
        let kept = query
            .split('&')
            .filter(|part| {
                let key = url::form_urlencoded::parse(part.as_bytes())
                    .next()
                    .map(|(k, _)| k.to_ascii_lowercase())
                    .unwrap_or_default();
                !key.starts_with("utm_")
                    && !key.starts_with("x-amz-")
                    && !key.starts_with("x-goog-")
                    && !key.starts_with("__cf_chl_")
                    && !matches!(
                        key.as_str(),
                        "token"
                            | "access_token"
                            | "refresh_token"
                            | "id_token"
                            | "private_token"
                            | "auth_token"
                            | "oauth_token"
                            | "download_key"
                            | "apikey"
                            | "api_key"
                            | "api-key"
                            | "key"
                            | "auth"
                            | "authorization"
                            | "password"
                            | "passwd"
                            | "secret"
                            | "client_secret"
                            | "session"
                            | "sessionid"
                            | "session_id"
                            | "sid"
                            | "signature"
                            | "sig"
                            | "fbclid"
                            | "gclid"
                            | "dclid"
                            | "msclkid"
                            | "igshid"
                            | "ref_src"
                            | "ref_url"
                    )
            })
            .collect::<Vec<_>>()
            .join("&");
        url.set_query((!kept.is_empty()).then_some(kept.as_str()));
    }
    url.set_fragment(None);
    Some(url.into())
}

pub fn provider_of(raw: &str) -> &'static str {
    let Some(clean) = canonical_source_url(raw) else {
        return "unknown";
    };
    let url = Url::parse(&clean).expect("already validated URL");
    let host = url
        .host_str()
        .unwrap_or_default()
        .trim_start_matches("www.");
    for (domain, provider) in [
        ("nexusmods.com", "nexus"),
        ("github.com", "github"),
        ("gitlab.com", "gitlab"),
        ("modrinth.com", "modrinth"),
        ("thunderstore.io", "thunderstore"),
        ("curseforge.com", "curseforge"),
        ("patreon.com", "patreon"),
        ("loverslab.com", "loverslab"),
        ("moddb.com", "moddb"),
        ("creations.bethesda.net", "bethesda"),
        ("dwemermods.com", "dwemer"),
        ("modding-guild.com", "modding-guild"),
        ("boosty.to", "boosty"),
        ("ko-fi.com", "ko-fi"),
        ("afdian.com", "afdian"),
        ("afdian.net", "afdian"),
        ("subscribestar.com", "subscribestar"),
        ("subscribestar.adult", "subscribestar"),
        ("modcollective.gg", "modcollective"),
        ("simsfinds.com", "simsfinds"),
        ("itch.io", "itch"),
        ("tumblr.com", "tumblr"),
        ("steamcommunity.com", "steam-workshop"),
        ("f95zone.to", "f95"),
        ("aml.name", "aml"),
    ] {
        if host == domain
            || (matches!(provider, "itch" | "tumblr" | "thunderstore")
                && host.ends_with(&format!(".{domain}")))
        {
            return provider;
        }
    }
    "website"
}

pub fn segment(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 120
        && value != "."
        && value != ".."
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"_.-".contains(&c))
}

pub fn project_identity(raw: &str) -> Option<ProjectIdentity> {
    let source_url = canonical_source_url(raw)?;
    let url = Url::parse(&source_url).ok()?;
    let provider = provider_of(&source_url);
    let parts: Vec<_> = url.path_segments()?.filter(|p| !p.is_empty()).collect();
    let mut id = ProjectIdentity {
        provider: provider.into(),
        project_id: String::new(),
        source_url,
        community: None,
        project_type: None,
    };
    match provider {
        "github" if parts.len() >= 2 && parts[..2].iter().all(|p| segment(p)) => {
            // Redirects for renamed projects require explicit source relinking.
            id.project_id =
                format!("{}/{}", parts[0], parts[1].trim_end_matches(".git")).to_ascii_lowercase();
        }
        "gitlab" => {
            let end = parts.iter().position(|p| *p == "-").unwrap_or(parts.len());
            let group = &parts[..end];
            if !(2..=8).contains(&group.len()) || !group.iter().all(|p| segment(p)) {
                return None;
            }
            id.project_id = group.join("/").trim_end_matches(".git").to_string();
        }
        "modrinth" if parts.len() >= 2 => {
            if !matches!(
                parts[0],
                "mod" | "plugin" | "modpack" | "resourcepack" | "shader" | "datapack"
            ) || !segment(parts[1])
            {
                return None;
            }
            id.project_id = parts[1].into();
            id.project_type = Some(parts[0].into());
        }
        "thunderstore" => {
            let host = url.host_str().unwrap_or_default();
            let package = if parts.len() >= 5 && parts[0] == "c" && parts[2] == "p" {
                if !segment(parts[1]) {
                    return None;
                }
                id.community = Some(parts[1].into());
                &parts[3..5]
            } else if parts.len() >= 3 && matches!(parts[0], "package" | "p") {
                if let Some(community) = host.strip_suffix(".thunderstore.io")
                    && !matches!(community, "www" | "old")
                    && segment(community)
                {
                    id.community = Some(community.into());
                }
                &parts[1..3]
            } else {
                return None;
            };
            if !package.iter().all(|p| segment(p)) {
                return None;
            }
            id.project_id = package.join("/");
        }
        _ => return None,
    }
    if id.project_id.split('/').any(|p| !segment(p)) {
        return None;
    }
    Some(id)
}

impl ProjectIdentity {
    pub fn key(&self) -> String {
        format!(
            "{}:{}:{}",
            self.provider,
            self.project_id,
            self.community.as_deref().unwrap_or("")
        )
    }
    pub fn api_url(&self) -> String {
        match self.provider.as_str() {
            "github" => format!(
                "https://api.github.com/repos/{}/releases/latest",
                self.project_id
            ),
            "gitlab" => {
                let encoded = url::form_urlencoded::byte_serialize(self.project_id.as_bytes())
                    .collect::<String>();
                format!("https://gitlab.com/api/v4/projects/{encoded}/releases/permalink/latest")
            }
            "modrinth" => format!("https://api.modrinth.com/v2/project/{}", self.project_id),
            "thunderstore" => format!(
                "https://thunderstore.io/api/experimental/package/{}/",
                self.project_id
            ),
            _ => String::new(),
        }
    }
}

pub fn api_url_allowed(raw: &str) -> bool {
    let Ok(url) = Url::parse(raw) else {
        return false;
    };
    if url.scheme() != "https"
        || !url.username().is_empty()
        || url.password().is_some()
        || url.port().is_some()
        || url.fragment().is_some()
        || raw.len() > 2000
    {
        return false;
    }
    matches!(
        url.host_str(),
        Some("api.github.com" | "gitlab.com" | "api.modrinth.com" | "thunderstore.io")
    ) && match url.host_str().unwrap_or_default() {
        "api.github.com" => {
            url.path().starts_with("/repos/") && url.path().ends_with("/releases/latest")
        }
        "gitlab.com" => {
            url.path().starts_with("/api/v4/projects/")
                && url.path().ends_with("/releases/permalink/latest")
        }
        "api.modrinth.com" => {
            url.path().starts_with("/v2/project/") || url.path().starts_with("/v2/version/")
        }
        "thunderstore.io" => url.path().starts_with("/api/experimental/package/"),
        _ => false,
    }
}
