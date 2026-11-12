//! Origin and tab policy is independent of the WebView runtime so it is tested on every OS.
#![cfg_attr(not(windows), allow(dead_code))]
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use url::Url;

pub const BAR_HEIGHT: f64 = 112.0;
pub const MAX_TABS: usize = 12;

pub fn eslifier_data_directory(path: &Path) -> Result<PathBuf, String> {
    let path = path
        .canonicalize()
        .map_err(|e| format!("Could not open the ESLifier data folder: {e}"))?;
    if !path.is_dir()
        || !path.file_name().and_then(|n| n.to_str()).is_some_and(|n| {
            n.eq_ignore_ascii_case("ESLifier_Data") || n.eq_ignore_ascii_case("ESLifier_FO4_Data")
        })
    {
        return Err(
            "Select the ESLifier_Data or ESLifier_FO4_Data folder beside ESLifier's executable."
                .into(),
        );
    }
    Ok(path)
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    #[default]
    Dashboard,
    Siege,
    Realm,
}

impl Mode {
    pub fn title(self) -> &'static str {
        match self {
            Self::Dashboard => "✦ Mod Vault",
            Self::Siege => "⚔ Modhunter Siege",
            Self::Realm => "ᛟ Shiverfell",
        }
    }
    pub fn query(self) -> &'static str {
        match self {
            Self::Dashboard => "",
            Self::Siege => "?realm=1&siege=1",
            Self::Realm => "?realm=1",
        }
    }
    pub fn from_url(url: &Url) -> Self {
        if url.query_pairs().any(|(k, v)| k == "siege" && v == "1") {
            Self::Siege
        } else if url.query_pairs().any(|(k, v)| k == "realm" && v == "1") {
            Self::Realm
        } else {
            Self::Dashboard
        }
    }
}

pub fn local_asset(url: &Url, path: &str) -> bool {
    let origin = (url.scheme() == "http" && url.host_str() == Some("tauri.localhost"))
        || (url.scheme() == "tauri" && url.host_str() == Some("localhost"));
    origin
        && url.port().is_none()
        && url.username().is_empty()
        && url.password().is_none()
        && url.path() == path
}

pub fn owned_page(url: &Url, endpoint: Option<&str>) -> bool {
    if local_asset(url, "/Modhunter.html") {
        return true;
    }
    let Some(base) = endpoint.and_then(|e| Url::parse(e).ok()) else {
        return false;
    };
    url.scheme() == "http"
        && url.host_str() == Some("127.0.0.1")
        && url.origin() == base.origin()
        && url.path() == "/"
        && url.username().is_empty()
        && url.password().is_none()
}

pub fn external_url(url: &Url) -> bool {
    matches!(url.scheme(), "https" | "http")
        && url.host_str().is_some()
        && url.username().is_empty()
        && url.password().is_none()
        && !matches!(url.host_str(), Some("tauri.localhost" | "ipc.localhost"))
}

pub fn export_blob(url: &Url, endpoint: Option<&str>) -> bool {
    if url.scheme() != "blob" {
        return false;
    }
    let Ok(inner) = Url::parse(url.path()) else {
        return false;
    };
    let local = inner.scheme() == "http"
        && inner.host_str() == Some("tauri.localhost")
        && inner.port().is_none();
    let live = endpoint
        .and_then(|e| Url::parse(e).ok())
        .is_some_and(|base| {
            inner.scheme() == "http"
                && inner.host_str() == Some("127.0.0.1")
                && inner.origin() == base.origin()
        });
    (local || live) && inner.username().is_empty() && inner.password().is_none()
}

pub fn export_name(name: &str) -> Option<String> {
    let clean: String = name
        .chars()
        .filter(|c| !c.is_control() && !"<>:\"/\\|?*".contains(*c))
        .take(180)
        .collect();
    let clean = clean.trim().trim_end_matches(['.', ' ']);
    let extension = clean.rsplit_once('.')?.1.to_ascii_lowercase();
    if clean.is_empty() || !matches!(extension.as_str(), "json" | "csv" | "ps1") {
        return None;
    }
    Some(clean.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn eslifier_picker_accepts_real_data_folders_and_rejects_executable_roots() {
        let root = tempfile::tempdir().unwrap();
        for name in ["ESLifier_Data", "ESLifier_FO4_Data"] {
            let data = root.path().join(name);
            std::fs::create_dir(&data).unwrap();
            assert_eq!(
                eslifier_data_directory(&data).unwrap(),
                data.canonicalize().unwrap()
            );
        }
        assert!(eslifier_data_directory(root.path()).is_err());
        assert!(eslifier_data_directory(&root.path().join("missing/ESLifier_Data")).is_err());
        let file_parent = root.path().join("file-parent");
        std::fs::create_dir(&file_parent).unwrap();
        let file = file_parent.join("ESLifier_Data");
        std::fs::write(&file, b"not a directory").unwrap();
        assert!(eslifier_data_directory(&file).is_err());
    }
    #[test]
    fn exports_accept_only_owned_blob_origins_and_known_data_formats() {
        let endpoint = Some("http://127.0.0.1:51234/");
        assert!(export_blob(
            &Url::parse("blob:http://tauri.localhost/fixture").unwrap(),
            endpoint
        ));
        assert!(export_blob(
            &Url::parse("blob:http://127.0.0.1:51234/fixture").unwrap(),
            endpoint
        ));
        for text in [
            "blob:https://example.com/fixture",
            "blob:http://127.0.0.1:51235/fixture",
            "https://example.com/progress.json",
            "blob:null/fixture",
        ] {
            assert!(!export_blob(&Url::parse(text).unwrap(), endpoint), "{text}");
        }
        assert_eq!(
            export_name("My profile.json"),
            Some("My profile.json".into())
        );
        assert!(export_name("installer.exe").is_none());
        assert!(export_name("progress.json.exe").is_none());
        assert!(!export_name("../../progress.json").unwrap().contains('/'));
    }
    #[test]
    fn only_exact_owned_origins_and_documents_can_navigate() {
        let endpoint = Some("http://127.0.0.1:51234/");
        for text in [
            "http://127.0.0.1:51234/?realm=1",
            "http://tauri.localhost/Modhunter.html?realm=1",
            "tauri://localhost/Modhunter.html",
        ] {
            assert!(owned_page(&Url::parse(text).unwrap(), endpoint), "{text}");
        }
        for text in [
            "http://127.0.0.1:51235/",
            "http://127.0.0.1:51234/api/snapshot",
            "http://tauri.localhost/shell.html",
            "https://example.com/",
            "file:///Modhunter.html",
            "http://user@127.0.0.1:51234/",
            "http://tauri.localhost:90/Modhunter.html",
        ] {
            assert!(!owned_page(&Url::parse(text).unwrap(), endpoint), "{text}");
        }
        assert!(!owned_page(
            &Url::parse("http://127.0.0.1:51234/").unwrap(),
            None
        ));
    }
    #[test]
    fn shell_has_its_own_exact_origin() {
        assert!(local_asset(
            &Url::parse("http://tauri.localhost/shell.html").unwrap(),
            "/shell.html"
        ));
        assert!(!local_asset(
            &Url::parse("http://127.0.0.1:51234/shell.html").unwrap(),
            "/shell.html"
        ));
    }
    #[test]
    fn external_launch_rejects_protocol_handlers_and_credentials() {
        for text in [
            "file:///c:/windows/system32/cmd.exe",
            "javascript:alert(1)",
            "ms-settings:foo",
            "https://user:pass@example.com/",
            "http://ipc.localhost/",
        ] {
            assert!(!external_url(&Url::parse(text).unwrap()));
        }
        assert!(external_url(
            &Url::parse("https://www.nexusmods.com/skyrimspecialedition/mods/44").unwrap()
        ));
    }
    #[test]
    fn mode_survives_connection_changes() {
        for mode in [Mode::Dashboard, Mode::Realm, Mode::Siege] {
            let u = Url::parse(&format!("http://127.0.0.1:51234/{}", mode.query())).unwrap();
            assert_eq!(mode, Mode::from_url(&u));
        }
    }
}
