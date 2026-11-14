//! Transient file authority. Only the native offer picker constructs this lease;
//! no raw address, access token, redirect chain, or browser credential is saved.
use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant};
use url::Url;

/// Decrypted metadata only. Node keys and account credentials never enter this
/// record; a shared link's access fragment remains in the transient page URL.
/// A folder choice additionally needs an exact provider node-to-blob observation.
#[derive(Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MegaFolderFile {
    pub public_id: String,
    pub root_id: String,
    pub file_id: String,
    pub parent_id: String,
    pub name: String,
    pub bytes: u64,
    pub modified_at: u64,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MegaBlobProof {
    pub file: MegaFolderFile,
    pub blob_url: String,
    pub observed_at: u64,
}

fn mega_folder_location(url: &Url) -> Option<(String, Option<(String, String)>)> {
    let path = url.path().trim_end_matches('/');
    let (public, hash) = if let Some(id) = path.strip_prefix("/folder/") {
        (id.to_owned(), url.fragment()?.to_owned())
    } else {
        if !matches!(path, "" | "/") {
            return None;
        }
        let rest = url.fragment()?.strip_prefix("F!")?;
        let (id, tail) = rest.split_once('!')?;
        let hash = if let Some((key, file)) = tail.split_once('?') {
            format!("{key}/file/{file}")
        } else if let Some((key, folder)) = tail.split_once('!') {
            format!("{key}/folder/{folder}")
        } else {
            tail.to_owned()
        };
        (id.to_owned(), hash)
    };
    if public.len() != 8 || !valid_id(&public) {
        return None;
    }
    let parts: Vec<_> = hash.split('/').collect();
    if parts[0].len() != 22 || !valid_id(parts[0]) {
        return None;
    }
    let target = match parts.as_slice() {
        [_] => None,
        [_, kind, id] if matches!(*kind, "file" | "folder") && id.len() == 8 && valid_id(id) => {
            Some(((*kind).into(), (*id).into()))
        }
        _ => return None,
    };
    Some((public, target))
}

pub fn mega_folder_file_identity(url: &Url) -> Option<String> {
    if !matches!(url.host_str(), Some("mega.nz" | "mega.io")) {
        return None;
    }
    let (public, Some((kind, file))) = mega_folder_location(url)? else {
        return None;
    };
    (kind == "file").then(|| format!("mega:folder:{public}:file:{file}"))
}

pub fn is_folder(url: &Url) -> bool {
    match url.host_str().unwrap_or("") {
        "drive.google.com" => url
            .path_segments()
            .is_some_and(|mut parts| parts.any(|p| p == "folders")),
        "mega.nz" | "mega.io" => {
            url.path().starts_with("/folder/")
                || url.fragment().is_some_and(|hash| hash.starts_with("F!"))
        }
        _ => false,
    }
}

fn drive_file(url: &Url) -> Option<String> {
    if !matches!(
        url.host_str(),
        Some("drive.google.com" | "drive.usercontent.google.com")
    ) || is_folder(url)
    {
        return None;
    }
    let parts = url.path_segments()?.collect::<Vec<_>>();
    if url.host_str() == Some("drive.google.com") && parts.len() >= 3 && parts[..2] == ["file", "d"]
    {
        return valid_id(parts[2]).then(|| parts[2].into());
    }
    if matches!(url.path(), "/uc" | "/download" | "/open") {
        let ids = url
            .query_pairs()
            .filter(|(key, _)| key == "id")
            .map(|(_, value)| value.into_owned())
            .collect::<Vec<_>>();
        return (ids.len() == 1 && valid_id(&ids[0])).then(|| ids[0].clone());
    }
    None
}

fn mega_file(url: &Url) -> Option<String> {
    if !matches!(url.host_str(), Some("mega.nz" | "mega.io")) || is_folder(url) {
        return None;
    }
    if let Some(id) = url
        .path()
        .strip_prefix("/file/")
        .and_then(|rest| rest.split('/').next())
    {
        return valid_id(id).then(|| id.into());
    }
    let id = url.fragment()?.strip_prefix('!')?.split('!').next()?;
    valid_id(id).then(|| id.into())
}

fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 200
        && id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-'))
}

fn loverslab_file(url: &Url) -> Option<String> {
    let host = url.host_str()?;
    if host != "loverslab.com" && !host.ends_with(".loverslab.com") {
        return None;
    }
    let parts = url.path_segments()?.collect::<Vec<_>>();
    if parts.len() < 3 || parts[..2] != ["files", "file"] {
        return None;
    }
    let id = parts[2].split('-').next()?;
    (!id.is_empty() && id.bytes().all(|b| b.is_ascii_digit())).then(|| id.into())
}

/// Known provider file IDs are derived from the address by the native host.
/// Generic addresses retain their full request identity in the hash input, so
/// an access-token-shaped query cannot secretly identify another file while
/// inheriting a previously saved choice. None of this input is persisted raw.
pub fn stable_identity(source: &Url, target: &Url, reported: &str) -> Result<String, String> {
    if let Some(identity) = mega_folder_file_identity(target) {
        return Ok(identity);
    }
    if is_folder(target) {
        let legacy = target
            .fragment()
            .and_then(|hash| hash.strip_prefix("F!"))
            .and_then(|rest| rest.split('!').next())
            .filter(|id| valid_id(id))
            .unwrap_or("");
        return Ok(format!(
            "container:{}{}:{legacy}",
            target.origin().ascii_serialization(),
            target.path()
        ));
    }
    match target.host_str().unwrap_or("") {
        "drive.google.com" | "drive.usercontent.google.com" => {
            return drive_file(target)
                .map(|id| format!("drive:file:{id}"))
                .ok_or_else(|| "The Drive link does not identify exactly one file.".into());
        }
        "mega.nz" | "mega.io" => {
            return mega_file(target)
                .map(|id| format!("mega:file:{id}"))
                .ok_or_else(|| "The MEGA link does not identify exactly one file.".into());
        }
        _ => {}
    }
    if let Some(page) = loverslab_file(source)
        && target
            .host_str()
            .is_some_and(|host| host == "loverslab.com" || host.ends_with(".loverslab.com"))
    {
        if loverslab_file(target).as_ref() != Some(&page) {
            return Err("The attachment belongs to a different LoversLab release page.".into());
        }
        let actions = target
            .query_pairs()
            .filter(|(key, _)| key == "do")
            .map(|(_, value)| value.into_owned())
            .collect::<Vec<_>>();
        let files = target
            .query_pairs()
            .filter(|(key, _)| key == "r")
            .map(|(_, value)| value.into_owned())
            .collect::<Vec<_>>();
        if actions != ["download"]
            || files.len() > 1
            || files
                .first()
                .is_some_and(|id| id.is_empty() || !id.bytes().all(|b| b.is_ascii_digit()))
        {
            return Err(
                "The LoversLab address does not identify one exact attachment request.".into(),
            );
        }
        return Ok(files.first().map_or_else(
            || format!("loverslab:file:{page}:direct"),
            |file| format!("loverslab:file:{page}:attachment:{file}"),
        ));
    }
    let host = source.host_str().unwrap_or("");
    if (host == "patreon.com" || host.ends_with(".patreon.com"))
        && reported.starts_with("patreon:post:")
    {
        // This is the browser's same-session relationship record, scoped by the
        // canonical watched post and by an exact native download URL lease.
        return Ok(reported.into());
    }
    let mut exact = target.clone();
    exact.set_fragment(None);
    Ok(format!("url:{exact}"))
}

fn same_request(a: &Url, b: &Url) -> bool {
    if mega_file(a).is_some() || mega_file(b).is_some() {
        return a.origin() == b.origin() && mega_file(a).is_some() && mega_file(a) == mega_file(b);
    }
    let mut a = a.clone();
    let mut b = b.clone();
    a.set_fragment(None);
    b.set_fragment(None);
    a == b
}

/// Single-use, bounded authority for one browser-owned selected file.
/// Do not derive Serialize/Debug: selected URLs may contain private access keys.
pub struct SelectedDownloadScope {
    selected: Url,
    created: Instant,
    invalid: bool,
    claimed: bool,
    navigation: Option<u64>,
    pending: bool,
    last_target: Option<Url>,
    redirect_count: usize,
    document: Option<Url>,
    exact_document_loaded: bool,
    mega_folder: Option<MegaFolderFile>,
    observer_key: Option<String>,
    fixture_origin: Option<String>,
}

impl SelectedDownloadScope {
    pub fn new(selected: Url) -> Result<Self, String> {
        let transport = selected.scheme() == "https"
            || (selected.scheme() == "http"
                && selected.host_str().is_some_and(|host| {
                    host.parse::<std::net::IpAddr>()
                        .is_ok_and(|ip| ip.is_loopback())
                }));
        if is_folder(&selected)
            || !transport
            || !selected.username().is_empty()
            || selected.password().is_some()
        {
            return Err("Choose an exact file link before assigning an update target.".into());
        }
        if matches!(
            selected.host_str(),
            Some("drive.google.com" | "drive.usercontent.google.com")
        ) && drive_file(&selected).is_none()
            || matches!(selected.host_str(), Some("mega.nz" | "mega.io"))
                && mega_file(&selected).is_none()
        {
            return Err("The selected provider address does not identify exactly one file.".into());
        }
        Ok(Self {
            selected,
            created: Instant::now(),
            invalid: false,
            claimed: false,
            navigation: None,
            pending: false,
            last_target: None,
            redirect_count: 0,
            document: None,
            exact_document_loaded: false,
            mega_folder: None,
            observer_key: None,
            fixture_origin: None,
        })
    }

    pub fn new_mega_folder_file(
        selected: Url,
        file: MegaFolderFile,
        observer_key: String,
        fixture_origin: Option<&str>,
    ) -> Result<Self, String> {
        let real = selected.scheme() == "https"
            && matches!(selected.host_str(), Some("mega.nz" | "mega.io"));
        let fixture = fixture_origin
            .is_some_and(|origin| origin == selected.origin().ascii_serialization())
            && selected.scheme() == "http"
            && selected.host_str().is_some_and(|host| {
                host.parse::<std::net::IpAddr>()
                    .is_ok_and(|ip| ip.is_loopback())
            });
        let exact = mega_folder_location(&selected).is_some_and(|(public, target)| {
            public == file.public_id && target == Some(("file".into(), file.file_id.clone()))
        });
        if !(real || fixture)
            || !exact
            || !selected.username().is_empty()
            || selected.password().is_some()
            || [
                &file.public_id,
                &file.root_id,
                &file.file_id,
                &file.parent_id,
            ]
            .iter()
            .any(|id| id.len() != 8 || !valid_id(id))
            || file.name.is_empty()
            || file.name.len() > 960
            || file
                .name
                .chars()
                .any(|c| c.is_control() || matches!(c, '/' | '\\'))
            || ![".zip", ".7z", ".rar"]
                .iter()
                .any(|extension| file.name.to_ascii_lowercase().ends_with(extension))
            || file.bytes == 0
            || file.bytes > 16 * 1024 * 1024 * 1024
            || observer_key.is_empty()
            || observer_key.len() > 100
            || !valid_id(&observer_key)
        {
            return Err("The folder did not resolve to one valid exact archive.".into());
        }
        // Use the same bounded navigation lifecycle as every selected file;
        // normal new() deliberately continues to reject folder addresses.
        Ok(Self {
            selected,
            created: Instant::now(),
            invalid: false,
            claimed: false,
            navigation: None,
            pending: false,
            last_target: None,
            redirect_count: 0,
            document: None,
            exact_document_loaded: false,
            mega_folder: Some(file),
            observer_key: Some(observer_key),
            fixture_origin: fixture.then(|| fixture_origin.unwrap().to_owned()),
        })
    }

    pub fn mega_folder(&self) -> Option<(MegaFolderFile, String, Url)> {
        Some((
            self.mega_folder.clone()?,
            self.observer_key.clone()?,
            self.selected.clone(),
        ))
    }

    pub fn live(&self) -> bool {
        self.live_for_age(self.created.elapsed())
    }

    fn live_for_age(&self, age: Duration) -> bool {
        // MEGA produces the final browser blob after its own encrypted transfer.
        // The bounded lease covers that preparation without granting authority
        // to another file, navigation, account generation or late blob proof.
        let seconds = if self.mega_folder.is_some() || mega_file(&self.selected).is_some() {
            1800
        } else {
            300
        };
        !self.invalid && (self.claimed || age <= Duration::from_secs(seconds))
    }

    pub fn invalidate(&mut self) {
        self.invalid = true;
        self.exact_document_loaded = false;
    }

    fn selected_document(&self, url: &Url) -> bool {
        if let Some(file) = &self.mega_folder {
            let host = matches!(url.host_str(), Some("mega.nz" | "mega.io"))
                && url.scheme() == "https"
                || self
                    .fixture_origin
                    .as_ref()
                    .is_some_and(|origin| url.origin().ascii_serialization() == *origin);
            return host
                && mega_folder_location(url).is_some_and(|(public, target)| {
                    public == file.public_id
                        && match target {
                            None => true,
                            Some((kind, id)) => {
                                kind == "file" && id == file.file_id
                                    || kind == "folder"
                                        && (id == file.parent_id || id == file.root_id)
                            }
                        }
                });
        }
        if is_folder(url) {
            return false;
        }
        if let Some(id) = mega_file(&self.selected) {
            return mega_file(url).as_ref() == Some(&id);
        }
        if url.origin() != self.selected.origin() {
            return false;
        }
        if let Some(id) = drive_file(&self.selected) {
            return drive_file(url).as_ref() == Some(&id);
        }
        same_request(&self.selected, url)
    }

    fn provider_download(&self, url: &Url) -> bool {
        drive_file(&self.selected).is_some_and(|id| drive_file(url).as_ref() == Some(&id))
    }

    pub fn navigation_started(&mut self, id: u64, url: &Url, redirected: bool) {
        if !self.live() {
            return;
        }
        // The host creates every view at about:blank, then installs observers.
        if url.as_str() == "about:blank" && self.navigation.is_none() {
            return;
        }
        let transport = url.scheme() == self.selected.scheme()
            && (!is_folder(url) || (self.mega_folder.is_some() && self.selected_document(url)))
            && url.username().is_empty()
            && url.password().is_none();
        if redirected {
            if !transport
                || self.navigation != Some(id)
                || !self.pending
                || self.redirect_count >= 12
            {
                self.invalidate();
                return;
            }
            self.redirect_count += 1;
        } else if !transport
            || !(self.selected_document(url)
                || (self.exact_document_loaded && self.provider_download(url)))
        {
            self.invalidate();
            return;
        } else {
            self.navigation = Some(id);
            self.redirect_count = 0;
            self.pending = true;
        }
        self.last_target = Some(url.clone());
    }

    pub fn source_changed(&mut self, url: &Url) {
        if !self.live() || (url.as_str() == "about:blank" && self.document.is_none()) {
            return;
        }
        if self.mega_folder.is_some() && !self.selected_document(url) {
            self.invalidate();
            return;
        }
        if self.selected_document(url) {
            // A same-document change may select another MEGA file through its
            // hash. selected_document checks its actual file ID, not just host.
            self.document = Some(url.clone());
        } else if self.pending
            && self
                .last_target
                .as_ref()
                .is_some_and(|last| same_request(last, url))
        {
            self.document = Some(url.clone());
            self.exact_document_loaded = false;
        } else {
            self.invalidate();
        }
    }

    pub fn navigation_completed(&mut self, id: u64, success: bool, url: &Url) {
        if !self.live() || self.navigation != Some(id) || !success {
            // A navigation converted into a download can complete as cancelled.
            // It grants no loaded-page/blob authority; DownloadStarting still
            // has to match the exact native-observed request chain.
            return;
        }
        self.pending = false;
        if self.selected_document(url) {
            self.document = Some(url.clone());
            self.exact_document_loaded = true;
        } else {
            // A sign-in/challenge/other HTML redirect cannot issue a download
            // on behalf of the file that was originally selected.
            self.invalidate();
        }
    }

    pub fn claim(&mut self, download: &Url, current_document: &Url) -> Result<(), String> {
        if self.mega_folder.is_some() {
            self.invalidate();
            return Err("This folder file requires its exact provider node-to-blob proof.".into());
        }
        let document_current = self.selected_document(current_document)
            || (current_document.as_str() == "about:blank"
                && self.document.is_none()
                && self.pending)
            || (self.pending
                && self
                    .last_target
                    .as_ref()
                    .is_some_and(|last| same_request(last, current_document)));
        let exact_request = self.navigation.is_some()
            && ((if mega_file(&self.selected).is_some() {
                self.selected_document(download)
            } else {
                same_request(&self.selected, download)
            }) || self.provider_download(download)
                || (self.pending
                    && self
                        .last_target
                        .as_ref()
                        .is_some_and(|last| same_request(last, download))));
        let expected_blob = mega_file(&self.selected).is_some()
            && self.exact_document_loaded
            && self.selected_document(current_document)
            && download.scheme() == "blob"
            && download
                .as_str()
                .strip_prefix("blob:")
                .and_then(|raw| Url::parse(raw).ok())
                .is_some_and(|url| url.origin() == current_document.origin());
        if !self.live() || self.claimed || !document_current || !(exact_request || expected_blob) {
            self.invalidate();
            return Err("This download no longer matches the exact file you selected. Find files again and open the intended attachment.".into());
        }
        self.claimed = true;
        Ok(())
    }

    pub fn claim_mega_blob(
        &mut self,
        download: &Url,
        current_document: &Url,
        proof: &MegaBlobProof,
        now: u64,
    ) -> Result<(), String> {
        let valid = self.live()
            && !self.claimed
            && self.exact_document_loaded
            && self.selected_document(current_document)
            && self.mega_folder.as_ref() == Some(&proof.file)
            && proof.blob_url == download.as_str()
            && proof.observed_at <= now
            && now.saturating_sub(proof.observed_at) <= 15_000
            && download.scheme() == "blob"
            && download
                .as_str()
                .strip_prefix("blob:")
                .and_then(|raw| Url::parse(raw).ok())
                .is_some_and(|url| url.origin() == current_document.origin());
        if !valid {
            self.invalidate();
            return Err("MEGA's browser download did not match the unique folder archive and exact blob you reviewed. Find files again.".into());
        }
        self.claimed = true;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn url(raw: &str) -> Url {
        Url::parse(raw).unwrap()
    }
    fn scope(raw: &str) -> SelectedDownloadScope {
        SelectedDownloadScope::new(url(raw)).unwrap()
    }
    fn folder_file() -> MegaFolderFile {
        MegaFolderFile {
            public_id: "pub12345".into(),
            root_id: "root1234".into(),
            file_id: "file1234".into(),
            parent_id: "root1234".into(),
            name: "Release.zip".into(),
            bytes: 64,
            modified_at: 100,
        }
    }
    fn folder_scope() -> (SelectedDownloadScope, Url) {
        let selected = url("https://mega.nz/folder/pub12345#abcdefghijklmnopqrstuv/file/file1234");
        let mut scope = SelectedDownloadScope::new_mega_folder_file(
            selected.clone(),
            folder_file(),
            "owned_observer".into(),
            None,
        )
        .unwrap();
        scope.navigation_started(9, &selected, false);
        let current = url("https://mega.nz/folder/pub12345#abcdefghijklmnopqrstuv/folder/root1234");
        scope.source_changed(&current);
        scope.navigation_completed(9, true, &current);
        (scope, current)
    }
    #[test]
    fn a_folder_child_url_still_requires_complete_native_selection_and_exact_blob_proof() {
        let selected = url("https://mega.nz/folder/pub12345#abcdefghijklmnopqrstuv/file/file1234");
        assert!(SelectedDownloadScope::new(selected.clone()).is_err());
        assert_eq!(
            stable_identity(
                &url("https://mods.example.com/release"),
                &selected,
                "forged"
            )
            .unwrap(),
            "mega:folder:pub12345:file:file1234"
        );
        let (mut scope, current) = folder_scope();
        let blob = url("blob:https://mega.nz/exact");
        assert!(
            scope.claim(&blob, &current).is_err(),
            "a normal same-origin blob claim cannot bypass folder proof"
        );
        let (mut scope, current) = folder_scope();
        let proof = MegaBlobProof {
            file: folder_file(),
            blob_url: blob.to_string(),
            observed_at: 1000,
        };
        assert!(scope.claim_mega_blob(&blob, &current, &proof, 1001).is_ok());
        assert!(
            scope
                .claim_mega_blob(&blob, &current, &proof, 1002)
                .is_err(),
            "folder authority is single-use"
        );
    }
    #[test]
    fn another_child_blob_metadata_or_expired_observation_cannot_borrow_folder_authority() {
        let blob = url("blob:https://mega.nz/exact");
        for case in 0..6 {
            let (mut scope, mut current) = folder_scope();
            let mut proof = MegaBlobProof {
                file: folder_file(),
                blob_url: blob.to_string(),
                observed_at: 1000,
            };
            match case {
                0 => proof.file.file_id = "wrong123".into(),
                1 => proof.blob_url = "blob:https://mega.nz/another".into(),
                2 => proof.file.bytes = 65,
                3 => {
                    current =
                        url("https://mega.nz/folder/pub12345#abcdefghijklmnopqrstuv/file/wrong123")
                }
                4 => proof.observed_at = 20_000,
                _ => proof.observed_at = 1,
            }
            let at = if case == 5 { 17_000 } else { 1001 };
            assert!(scope.claim_mega_blob(&blob, &current, &proof, at).is_err());
            assert!(!scope.live());
        }
        let (mut scope, current) = folder_scope();
        let proof = MegaBlobProof {
            file: folder_file(),
            blob_url: "blob:https://attacker.example/wrong".into(),
            observed_at: 1000,
        };
        assert!(
            scope
                .claim_mega_blob(&url(&proof.blob_url), &current, &proof, 1001)
                .is_err()
        );
    }
    #[test]
    fn folder_navigation_to_another_node_permanently_invalidates_the_exact_child() {
        let (mut scope, current) = folder_scope();
        scope.source_changed(&url(
            "https://mega.nz/folder/pub12345#abcdefghijklmnopqrstuv/file/wrong123",
        ));
        scope.source_changed(&current);
        assert!(!scope.live());
        let selected = url("https://mega.nz/folder/pub12345#abcdefghijklmnopqrstuv/file/file1234");
        let mut wrong = folder_file();
        wrong.file_id = "wrong123".into();
        assert!(
            SelectedDownloadScope::new_mega_folder_file(
                selected,
                wrong,
                "owned_observer".into(),
                None
            )
            .is_err()
        );
    }
    #[test]
    fn mega_browser_preparation_has_a_bounded_thirty_minute_exact_file_lease() {
        let (folder, _) = folder_scope();
        let direct = scope("https://mega.nz/file/file1234#abcdefghijklmnopqrstuv");
        for selected in [folder, direct] {
            assert!(selected.live_for_age(Duration::from_secs(1800)));
            assert!(!selected.live_for_age(Duration::from_secs(1800) + Duration::from_nanos(1)));
        }
        let direct = scope("https://mods.example.com/Release.zip");
        assert!(direct.live_for_age(Duration::from_secs(300)));
        assert!(!direct.live_for_age(Duration::from_secs(301)));
        let selected = url("https://mega.nz/folder/pub12345#abcdefghijklmnopqrstuv/file/file1234");
        for name in ["../Release.zip", "sub\\Release.zip", "Release.html"] {
            let mut file = folder_file();
            file.name = name.into();
            assert!(
                SelectedDownloadScope::new_mega_folder_file(
                    selected.clone(),
                    file,
                    "owned_observer".into(),
                    None
                )
                .is_err()
            );
        }
    }
    #[test]
    fn folders_cannot_acquire_exact_file_authority_even_if_metadata_calls_them_attachments() {
        for raw in [
            "https://drive.google.com/drive/folders/abc12345",
            "https://drive.google.com/drive/u/0/folders/abc12345",
            "https://mega.nz/folder/abc12345#key",
            "https://mega.nz/#F!abc12345!key",
        ] {
            assert!(is_folder(&url(raw)));
            assert!(SelectedDownloadScope::new(url(raw)).is_err());
        }
    }
    #[test]
    fn native_identity_ignores_forged_provider_ids_and_ambiguous_drive_queries() {
        let source = url("https://mods.example.com/release");
        assert_eq!(
            stable_identity(
                &source,
                &url("https://drive.google.com/file/d/actual123/view?resourcekey=secret"),
                "drive:file:forged"
            )
            .unwrap(),
            "drive:file:actual123"
        );
        assert_eq!(
            stable_identity(
                &source,
                &url("https://mega.nz/file/actual123#private-key"),
                "mega:file:forged"
            )
            .unwrap(),
            "mega:file:actual123"
        );
        assert!(
            stable_identity(
                &source,
                &url("https://drive.google.com/uc?id=one&id=two"),
                "drive:file:one"
            )
            .is_err()
        );
        assert!(
            SelectedDownloadScope::new(url("https://drive.google.com/uc?id=one&id=two")).is_err()
        );
    }
    #[test]
    fn generic_token_queries_cannot_merge_different_file_requests_into_one_decision() {
        let source = url("https://mods.example.com/release");
        let first = stable_identity(
            &source,
            &url("https://mods.example.com/download?token=one"),
            "url:https://mods.example.com/download",
        )
        .unwrap();
        let second = stable_identity(
            &source,
            &url("https://mods.example.com/download?token=two"),
            "url:https://mods.example.com/download",
        )
        .unwrap();
        assert_ne!(first, second);
        let source = url("https://www.loverslab.com/files/file/111-mod/");
        assert_eq!(
            stable_identity(
                &source,
                &url(
                    "https://www.loverslab.com/files/file/111-mod/?do=download&r=77&csrfKey=secret"
                ),
                "loverslab:file:111:attachment:other"
            )
            .unwrap(),
            "loverslab:file:111:attachment:77"
        );
    }
    #[test]
    fn an_exact_direct_archive_and_its_observed_redirect_are_single_use() {
        let mut selected = scope("https://www.patreon.com/files/10.zip?token=private");
        selected.navigation_started(
            41,
            &url("https://www.patreon.com/files/10.zip?token=private"),
            false,
        );
        selected.navigation_started(
            41,
            &url("https://c10.patreonusercontent.com/private/release.zip?token=rotated"),
            true,
        );
        assert!(
            selected
                .claim(
                    &url("https://c10.patreonusercontent.com/private/release.zip?token=rotated"),
                    &url("about:blank")
                )
                .is_ok()
        );
        assert!(selected.live());
        assert!(
            selected
                .claim(
                    &url("https://c10.patreonusercontent.com/private/release.zip?token=rotated"),
                    &url("about:blank")
                )
                .is_err()
        );
    }
    #[test]
    fn arbitrary_same_host_archive_cannot_inherit_a_selected_file() {
        let mut selected = scope("https://mods.example.com/one.zip");
        selected.navigation_started(7, &url("https://mods.example.com/one.zip"), false);
        assert!(
            selected
                .claim(
                    &url("https://mods.example.com/two.zip"),
                    &url("about:blank")
                )
                .is_err()
        );
    }
    #[test]
    fn unrelated_navigation_permanently_revokes_authority_even_after_returning() {
        let mut selected = scope("https://mods.example.com/one.zip");
        selected.navigation_started(7, &url("https://mods.example.com/one.zip"), false);
        selected.navigation_started(8, &url("https://mods.example.com/other"), false);
        selected.navigation_started(9, &url("https://mods.example.com/one.zip"), false);
        assert!(!selected.live());
        assert!(
            selected
                .claim(
                    &url("https://mods.example.com/one.zip"),
                    &url("about:blank")
                )
                .is_err()
        );
    }
    #[test]
    fn a_forged_redirect_with_another_navigation_id_cannot_extend_the_chain() {
        let mut selected = scope("https://mods.example.com/one.zip");
        selected.navigation_started(7, &url("https://mods.example.com/one.zip"), false);
        selected.navigation_started(8, &url("https://mods.example.com/two.zip"), true);
        assert!(!selected.live());
    }
    #[test]
    fn drive_download_endpoints_must_name_the_exact_selected_id() {
        let page = url("https://drive.google.com/file/d/abc12345/view?resourcekey=private");
        let mut selected = scope(page.as_str());
        selected.navigation_started(7, &page, false);
        selected.navigation_completed(7, true, &page);
        assert!(selected.claim(&url("https://drive.usercontent.google.com/download?id=abc12345&export=download&confirm=one"), &page).is_ok());
        for raw in [
            "https://drive.usercontent.google.com/download?id=another",
            "https://drive.usercontent.google.com/download?id=abc12345&id=another",
            "https://drive.google.com/anything?id=abc12345",
        ] {
            let mut selected = scope(page.as_str());
            selected.navigation_started(7, &page, false);
            selected.navigation_completed(7, true, &page);
            assert!(selected.claim(&url(raw), &page).is_err(), "{raw}");
        }
    }
    #[test]
    fn mega_blob_requires_the_loaded_exact_file_document_and_same_origin() {
        let page = url("https://mega.nz/file/abc12345#private-key");
        let mut selected = scope(page.as_str());
        selected.navigation_started(7, &page, false);
        assert!(
            selected
                .claim(&url("blob:https://mega.nz/opaque"), &page)
                .is_err()
        );
        let mut selected = scope(page.as_str());
        selected.navigation_started(7, &page, false);
        selected.navigation_completed(7, true, &page);
        assert!(
            selected
                .claim(&url("blob:https://mega.nz/opaque"), &page)
                .is_ok()
        );
        for current in [
            "https://mega.nz/file/another#key",
            "https://mega.nz/folder/abc12345#key",
        ] {
            let mut selected = scope(page.as_str());
            selected.navigation_started(7, &page, false);
            selected.navigation_completed(7, true, &page);
            assert!(
                selected
                    .claim(&url("blob:https://mega.nz/opaque"), &url(current))
                    .is_err()
            );
        }
    }
    #[test]
    fn same_document_legacy_mega_file_change_revokes_blob_authority() {
        let page = url("https://mega.nz/#!abc12345!private-key");
        let mut selected = scope(page.as_str());
        selected.navigation_started(7, &page, false);
        selected.navigation_completed(7, true, &page);
        selected.source_changed(&url("https://mega.nz/#!another!private-key"));
        assert!(!selected.live());
    }
    #[test]
    fn mega_domain_alias_retains_exact_file_identity_but_never_accepts_another_blob_origin() {
        let original = url("https://mega.io/file/abc12345#private-key");
        let canonical = url("https://mega.nz/file/abc12345#private-key");
        let mut selected = scope(original.as_str());
        selected.navigation_started(7, &original, false);
        selected.navigation_started(7, &canonical, true);
        selected.navigation_completed(7, true, &canonical);
        assert!(
            selected
                .claim(&url("blob:https://mega.nz/opaque"), &canonical)
                .is_ok()
        );
        let mut selected = scope(original.as_str());
        selected.navigation_started(7, &original, false);
        selected.navigation_started(7, &canonical, true);
        selected.navigation_completed(7, true, &canonical);
        assert!(
            selected
                .claim(&url("blob:https://other.example.com/opaque"), &canonical)
                .is_err()
        );
    }
    #[test]
    fn a_fragment_file_change_during_navigation_never_matches_the_pending_file_chain() {
        let page = url("https://mega.nz/#!abc12345!private-key");
        let changed = url("https://mega.nz/#!another!private-key");
        let mut selected = scope(page.as_str());
        selected.navigation_started(7, &page, false);
        selected.source_changed(&changed);
        assert!(!selected.live());
        assert!(selected.claim(&changed, &changed).is_err());
    }
    #[test]
    fn unused_file_scope_expires_without_interrupting_an_already_owned_transfer() {
        let page = url("https://mods.example.com/one.zip");
        let mut selected = scope(page.as_str());
        selected.navigation_started(7, &page, false);
        selected.created = Instant::now() - Duration::from_secs(301);
        assert!(!selected.live());
        assert!(selected.claim(&page, &url("about:blank")).is_err());
        let mut selected = scope(page.as_str());
        selected.navigation_started(7, &page, false);
        selected.claim(&page, &url("about:blank")).unwrap();
        selected.created = Instant::now() - Duration::from_secs(301);
        assert!(selected.live());
    }
    #[test]
    fn login_redirects_and_changed_current_documents_cannot_issue_classified_archives() {
        let mut selected = scope("https://mods.example.com/one.zip");
        selected.navigation_started(7, &url("https://mods.example.com/one.zip"), false);
        selected.navigation_started(7, &url("https://mods.example.com/login"), true);
        selected.navigation_completed(7, true, &url("https://mods.example.com/login"));
        assert!(!selected.live());
        let mut selected = scope("https://mods.example.com/one.zip");
        selected.navigation_started(7, &url("https://mods.example.com/one.zip"), false);
        assert!(
            selected
                .claim(
                    &url("https://mods.example.com/one.zip"),
                    &url("https://mods.example.com/other")
                )
                .is_err()
        );
    }
    #[test]
    fn a_selected_address_alone_does_not_prove_browser_navigation() {
        let mut selected = scope("https://mods.example.com/one.zip");
        assert!(
            selected
                .claim(
                    &url("https://mods.example.com/one.zip"),
                    &url("about:blank")
                )
                .is_err()
        );
    }
}
