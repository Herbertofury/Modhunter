//! Authenticated browser downloads retain their account lease but never its cookies.
//! All filesystem destinations are generated here; website filenames are labels only.
use crate::watchtower::ModAssociation;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};

pub const MAX_BYTES: u64 = 16 * 1024 * 1024 * 1024;
const MAX_ROWS: usize = 2000;
type Result<T> = std::result::Result<T, String>;
fn error(e: impl std::fmt::Display) -> String {
    e.to_string()
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Binding {
    pub watch_id: String,
    pub watch_generation: u64,
    pub session_id: Option<String>,
    pub session_generation: u64,
    pub source_origin: String,
    pub association: Option<ModAssociation>,
    #[serde(default)]
    pub source_file_key: Option<String>,
    #[serde(default)]
    pub content_intent: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Receipt {
    pub id: String,
    pub filename: String,
    pub binding: Binding,
    pub state: String,
    pub received: u64,
    pub total: Option<u64>,
    pub bytes: u64,
    pub sha256: Option<String>,
    pub extension: Option<String>,
    pub message: String,
    pub created_at: u64,
    pub updated_at: u64,
}
#[derive(Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct Document {
    schema: u32,
    revision: u64,
    jobs: Vec<Receipt>,
}

pub struct Store {
    root: PathBuf,
    doc: Document,
    warning: String,
}
impl Store {
    pub fn open(root: impl Into<PathBuf>) -> Self {
        let root = root.into();
        let loaded = (|| -> Result<Document> {
            fs::create_dir_all(&root).map_err(error)?;
            reject_link(&root)?;
            let path = root.join("receipts.json");
            if !path.exists() {
                return Ok(Document {
                    schema: 1,
                    ..Default::default()
                });
            }
            reject_link(&path)?;
            let mut bytes = Vec::new();
            File::open(path)
                .map_err(error)?
                .take(4 * 1024 * 1024 + 1)
                .read_to_end(&mut bytes)
                .map_err(error)?;
            if bytes.len() > 4 * 1024 * 1024 {
                return Err("Download receipts exceed their storage bound.".into());
            }
            let mut doc: Document = serde_json::from_slice(&bytes).map_err(error)?;
            if doc.schema != 1 || doc.jobs.len() > MAX_ROWS {
                return Err("Unsupported download receipt schema.".into());
            }
            let mut ids = std::collections::HashSet::new();
            for row in &mut doc.jobs {
                valid_id(&row.id)?;
                if !ids.insert(row.id.clone())
                    || !valid_filename(&row.filename)
                    || row.bytes > MAX_BYTES
                    || row.total.is_some_and(|n| n > MAX_BYTES)
                {
                    return Err("Invalid download receipt.".into());
                }
                if !matches!(
                    row.state.as_str(),
                    "pending"
                        | "downloading"
                        | "validating"
                        | "ready"
                        | "cancelled"
                        | "failed"
                        | "interrupted"
                ) {
                    return Err("Unknown download state.".into());
                }
                if matches!(row.state.as_str(), "pending" | "downloading" | "validating") {
                    row.state = "interrupted".into();
                    row.message="The app closed before verification completed. Download again from the page.".into();
                }
                if row.state == "ready"
                    && (row
                        .sha256
                        .as_ref()
                        .is_none_or(|h| h.len() != 64 || !h.bytes().all(|b| b.is_ascii_hexdigit()))
                        || !matches!(row.extension.as_deref(), Some("zip" | "7z" | "rar")))
                {
                    return Err("Ready download lacks verified file identity.".into());
                }
            }
            Ok(doc)
        })();
        let (doc, warning) = match loaded {
            Ok(d) => (d, String::new()),
            Err(e) => (
                Document {
                    schema: 1,
                    ..Default::default()
                },
                format!("Downloads are read-only; original receipts preserved. {e}"),
            ),
        };
        Self { root, doc, warning }
    }
    pub fn snapshot(&self) -> serde_json::Value {
        serde_json::json!({"revision":self.doc.revision,"jobs":self.doc.jobs,"warning":self.warning})
    }
    pub fn row(&self, id: &str) -> Result<Receipt> {
        self.doc
            .jobs
            .iter()
            .find(|r| r.id == id)
            .cloned()
            .ok_or("Download no longer exists.".into())
    }
    pub fn request(
        &mut self,
        filename: &str,
        binding: Binding,
        total: Option<u64>,
        now: u64,
    ) -> Result<Receipt> {
        self.writable()?;
        if !valid_filename(filename) {
            return Err(
                "Choose a ZIP, 7z or RAR archive. Executables and web pages are not accepted."
                    .into(),
            );
        }
        if total.is_some_and(|n| n == 0 || n > MAX_BYTES) {
            return Err("Archive exceeds the download size limit.".into());
        }
        if self.doc.jobs.len() >= MAX_ROWS
            || self
                .doc
                .jobs
                .iter()
                .filter(|r| matches!(r.state.as_str(), "pending" | "downloading" | "validating"))
                .count()
                >= 16
        {
            return Err("Finish or remove existing downloads before adding another.".into());
        }
        let directory = tempfile::Builder::new()
            .prefix("download-")
            .tempdir_in(&self.root)
            .map_err(error)?;
        let path = directory.keep();
        let id = path.file_name().unwrap().to_string_lossy().into_owned();
        let row = Receipt {
            id: id.clone(),
            filename: filename.into(),
            binding,
            state: "pending".into(),
            received: 0,
            total,
            bytes: 0,
            sha256: None,
            extension: None,
            message:
                "Approve this archive in Modhunter. No installation will follow automatically."
                    .into(),
            created_at: now,
            updated_at: now,
        };
        let mut draft = self.doc.clone();
        draft.jobs.push(row.clone());
        if let Err(e) = self.commit(draft) {
            let _ = fs::remove_dir(&path);
            return Err(e);
        }
        Ok(row)
    }
    pub fn approve(
        &mut self,
        id: &str,
        binding: &Binding,
        confirm: &str,
        now: u64,
    ) -> Result<PathBuf> {
        let row = self.row(id)?;
        if row.state != "pending"
            || &row.binding != binding
            || confirm != "DOWNLOAD"
            || now.saturating_sub(row.created_at) > 120000
        {
            return Err("Download approval expired or its page/account changed.".into());
        }
        let path = self.directory(id)?.join("payload.part");
        if path.exists() {
            return Err("Download staging file already exists.".into());
        }
        self.change(id, |r| {
            r.state = "downloading".into();
            r.message = "Downloading through the selected website session.".into();
            r.updated_at = now;
        })?;
        Ok(path)
    }
    /// Progress is transient: it must not rewrite the journal for every packet.
    pub fn progress(&mut self, id: &str, received: u64, total: Option<u64>) -> Result<()> {
        self.writable()?;
        if received > MAX_BYTES
            || total.is_some_and(|n| n > MAX_BYTES)
            || total.is_some_and(|n| received > n)
        {
            return Err("Download exceeded its declared or configured limit.".into());
        }
        let row = self
            .doc
            .jobs
            .iter_mut()
            .find(|r| r.id == id)
            .ok_or("Download no longer exists.")?;
        if row.state != "downloading" {
            return Err("Download is no longer active.".into());
        }
        row.received = received;
        row.total = total.or(row.total);
        self.doc.revision = self.doc.revision.saturating_add(1);
        Ok(())
    }
    pub fn begin_validation(&mut self, id: &str, binding: &Binding) -> Result<()> {
        let row = self.row(id)?;
        if row.state != "downloading" || row.binding != *binding {
            return Err("Download lease changed before verification.".into());
        }
        self.change(id, |r| {
            r.state = "validating".into();
            r.message = "Verifying archive bytes.".into();
        })
    }
    pub fn validate(&mut self, id: &str, cancel: impl Fn() -> bool) -> Result<Receipt> {
        let row = self.row(id)?;
        if row.state != "validating" {
            return Err("Download is not awaiting verification.".into());
        }
        let part = self.directory(id)?.join("payload.part");
        let identity = archive_identity(&part, &cancel)?;
        self.complete_validation(id, &row.binding, identity)
    }
    pub fn complete_validation(
        &mut self,
        id: &str,
        binding: &Binding,
        identity: (u64, String, &str),
    ) -> Result<Receipt> {
        let row = self.row(id)?;
        if row.state != "validating" || row.binding != *binding {
            return Err("Download cancelled or replaced during verification.".into());
        }
        let (bytes, digest, extension) = identity;
        if !matches!(extension, "zip" | "7z" | "rar")
            || digest.len() != 64
            || !digest.bytes().all(|b| b.is_ascii_hexdigit())
            || bytes > MAX_BYTES
        {
            return Err("Invalid archive verification result.".into());
        }
        if row.total.is_some_and(|n| n != bytes) {
            return Err("Archive length differs from the declared length.".into());
        }
        let part = self.directory(id)?.join("payload.part");
        let destination = self.directory(id)?.join(format!("archive.{extension}"));
        if destination.exists() {
            return Err("Archive destination already exists.".into());
        }
        fs::rename(&part, &destination).map_err(error)?;
        if let Err(e) = self.change(id, |r| {
            r.state = "ready".into();
            r.bytes = bytes;
            r.received = bytes;
            r.sha256 = Some(digest);
            r.extension = Some(extension.into());
            r.message = "Archive integrity verified. Installation requires separate review.".into();
        }) {
            let _ = fs::rename(&destination, &part);
            return Err(e);
        }
        self.row(id)
    }
    pub fn verify(&self, id: &str, cancel: impl Fn() -> bool) -> Result<PathBuf> {
        let row = self.row(id)?;
        if row.state != "ready" {
            return Err("Download is not ready.".into());
        }
        let path = self.directory(id)?.join(format!(
            "archive.{}",
            row.extension.as_deref().ok_or("Missing archive format.")?
        ));
        let (bytes, hash, _) = archive_identity(&path, cancel)?;
        if row.bytes != bytes || row.sha256.as_deref() != Some(&hash) {
            return Err("Archive changed since it was downloaded. Download it again.".into());
        }
        Ok(path)
    }
    pub fn finish_error(&mut self, id: &str, state: &str, message: &str) -> Result<()> {
        if !matches!(state, "cancelled" | "failed" | "interrupted") {
            return Err("Invalid terminal state.".into());
        }
        let previous = self.row(id)?.state;
        if previous == "ready" {
            return Err("Verified archive cannot be replaced by a late error.".into());
        }
        if matches!(previous.as_str(), "cancelled" | "failed" | "interrupted") && previous != state
        {
            // Cancellation, transport callbacks and shutdown can race. Preserve
            // the first durable outcome instead of changing its meaning later.
            return Ok(());
        }
        self.change(id, |r| {
            r.state = state.into();
            r.message = message.chars().take(500).collect();
        })?;
        self.cleanup_part(id)
    }
    /// Delete only the unfinished file owned by a terminal receipt. Browser
    /// cancellation may release its file handle later; callers may retry.
    pub fn cleanup_part(&self, id: &str) -> Result<()> {
        let row = self.row(id)?;
        if !matches!(row.state.as_str(), "cancelled" | "failed" | "interrupted") {
            return Err("Only a terminal unfinished download permits partial-file cleanup.".into());
        }
        let path = self.directory(id)?.join("payload.part");
        match fs::symlink_metadata(&path) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(e) => Err(error(e)),
            Ok(metadata) => {
                reject_link(&path)?;
                if !metadata.is_file() {
                    return Err("Unexpected partial-download content was preserved.".into());
                }
                fs::remove_file(path).map_err(error)
            }
        }
    }
    pub fn remove(&mut self, id: &str, confirm: &str) -> Result<()> {
        self.writable()?;
        let row = self.row(id)?;
        if confirm != "REMOVE" || matches!(row.state.as_str(), "downloading" | "validating") {
            return Err("Cancel the active download before removing it.".into());
        }
        let path = self.directory(id)?;
        let approved = removal_inventory(&path, &row)?;
        let trash = path.with_file_name(format!("retired-{id}"));
        match fs::symlink_metadata(&trash) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
            Err(e) => return Err(error(e)),
            Ok(_) => return Err("Previous removal needs review.".into()),
        }
        fs::rename(&path, &trash).map_err(error)?;
        let rechecked = removal_inventory(&trash, &row);
        if rechecked.as_ref().ok() != Some(&approved) {
            return Err(rollback_removal(
                &trash,
                &path,
                rechecked.err().unwrap_or_else(|| {
                    "Download content changed during removal; all remaining bytes were preserved."
                        .into()
                }),
            ));
        }
        let mut draft = self.doc.clone();
        draft.jobs.retain(|r| r.id != id);
        if let Err(e) = self.commit(draft) {
            return Err(rollback_removal(&trash, &path, e));
        }
        // Never recursively delete this directory. A foreign file appearing
        // after verification must survive, even if the receipt was removed.
        for (name, identity) in approved {
            let file = trash.join(name);
            if file_hash(&file, || false)? != identity {
                return Err(
                    "Retired download changed; its remaining evidence was preserved.".into(),
                );
            }
            fs::remove_file(file).map_err(error)?;
        }
        fs::remove_dir(&trash).map_err(|e| {
            format!("Download receipt removed; unexpected or locked retired contents were preserved: {e}")
        })
    }
    pub fn staging(&self, id: &str) -> Result<PathBuf> {
        Ok(self.directory(id)?.join("payload.part"))
    }
    fn directory(&self, id: &str) -> Result<PathBuf> {
        valid_id(id)?;
        reject_link(&self.root)?;
        let p = self.root.join(id);
        reject_link(&p)?;
        Ok(p)
    }
    fn writable(&self) -> Result<()> {
        if self.warning.is_empty() {
            Ok(())
        } else {
            Err(self.warning.clone())
        }
    }
    fn change(&mut self, id: &str, f: impl FnOnce(&mut Receipt)) -> Result<()> {
        self.writable()?;
        let mut d = self.doc.clone();
        f(d.jobs
            .iter_mut()
            .find(|r| r.id == id)
            .ok_or("Missing download.")?);
        self.commit(d)
    }
    fn commit(&mut self, mut doc: Document) -> Result<()> {
        self.writable()?;
        doc.revision = doc
            .revision
            .checked_add(1)
            .ok_or("Download revision exhausted.")?;
        atomic_json(&self.root.join("receipts.json"), &doc)?;
        self.doc = doc;
        Ok(())
    }
}

fn rollback_removal(retired: &Path, original: &Path, reason: String) -> String {
    if fs::symlink_metadata(original).is_err_and(|e| e.kind() == std::io::ErrorKind::NotFound)
        && fs::rename(retired, original).is_ok()
    {
        reason
    } else {
        format!("{reason} Retired evidence could not be put back and remains preserved for review.")
    }
}
fn removal_inventory(directory: &Path, row: &Receipt) -> Result<BTreeMap<String, (u64, String)>> {
    reject_link(directory)?;
    let ready = row.state == "ready";
    let allowed = if ready {
        let extension = row
            .extension
            .as_deref()
            .filter(|extension| matches!(*extension, "zip" | "7z" | "rar"))
            .ok_or("Ready download lacks a recognized archive identity.")?;
        Some(format!("archive.{extension}"))
    } else if matches!(row.state.as_str(), "cancelled" | "failed" | "interrupted") {
        Some("payload.part".into())
    } else if row.state == "pending" {
        None
    } else {
        return Err("An active or unknown download cannot be removed.".into());
    };
    let mut files = BTreeMap::new();
    for child in fs::read_dir(directory).map_err(error)? {
        let child = child.map_err(error)?;
        let name = child
            .file_name()
            .into_string()
            .map_err(|_| "Unrecognized download contents were preserved.")?;
        let path = child.path();
        reject_link(&path)?;
        let metadata = fs::symlink_metadata(&path).map_err(error)?;
        if allowed.as_deref() != Some(&name) || !metadata.is_file() || metadata.len() > MAX_BYTES {
            return Err("Unrecognized download contents were preserved for manual review.".into());
        }
        let identity = if ready {
            let (bytes, hash, extension) = archive_identity(&path, || false)?;
            if bytes != row.bytes
                || row.sha256.as_deref() != Some(&hash)
                || row.extension.as_deref() != Some(extension)
            {
                return Err(
                    "Verified archive changed; removal preserved its receipt and bytes.".into(),
                );
            }
            (bytes, hash)
        } else {
            file_hash(&path, || false)?
        };
        files.insert(name, identity);
    }
    if ready && files.len() != 1 {
        return Err("Verified archive is missing; preserve its receipt for review.".into());
    }
    Ok(files)
}
fn valid_id(id: &str) -> Result<()> {
    if !id.starts_with("download-")
        || id.len() > 90
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    {
        Err("Invalid download identity.".into())
    } else {
        Ok(())
    }
}
pub fn valid_filename(name: &str) -> bool {
    valid_component(name)
        && [".zip", ".7z", ".rar"]
            .iter()
            .any(|ext| name.to_ascii_lowercase().ends_with(ext))
}
pub fn valid_component(name: &str) -> bool {
    if name.is_empty()
        || name.len() > 240
        || name
            .chars()
            .any(|c| c.is_control() || "<>:\"/\\|?*".contains(c))
        || name.ends_with(['.', ' '])
    {
        return false;
    }
    let lower = name.to_ascii_lowercase();
    let stem = lower.split('.').next().unwrap_or("");
    if matches!(stem, "con" | "prn" | "aux" | "nul")
        || (stem.len() == 4
            && (stem.starts_with("com") || stem.starts_with("lpt"))
            && stem.as_bytes()[3].is_ascii_digit())
    {
        return false;
    }
    true
}
pub fn reject_link(path: &Path) -> Result<()> {
    let m = fs::symlink_metadata(path).map_err(error)?;
    if m.file_type().is_symlink() {
        return Err("Symbolic links are not accepted in managed file storage.".into());
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if m.file_attributes() & 0x400 != 0 {
            return Err("Reparse points are not accepted in managed file storage.".into());
        }
    }
    Ok(())
}
pub fn atomic_json(path: &Path, value: &impl Serialize) -> Result<()> {
    if path.exists() {
        reject_link(path)?
    }
    let parent = path.parent().ok_or("Missing storage parent.")?;
    reject_link(parent)?;
    let mut tmp = tempfile::NamedTempFile::new_in(parent).map_err(error)?;
    serde_json::to_writer(&mut tmp, value).map_err(error)?;
    tmp.flush().map_err(error)?;
    tmp.as_file().sync_all().map_err(error)?;
    tmp.persist(path).map_err(error)?;
    Ok(())
}
pub fn file_hash(path: &Path, cancel: impl Fn() -> bool) -> Result<(u64, String)> {
    reject_link(path)?;
    let mut f = File::open(path).map_err(error)?;
    let before = f.metadata().map_err(error)?;
    if !before.is_file() {
        return Err("Expected a regular file.".into());
    }
    let mut h = Sha256::new();
    let mut buf = [0u8; 128 * 1024];
    let mut size = 0u64;
    loop {
        if cancel() {
            return Err("File operation cancelled.".into());
        }
        let n = f.read(&mut buf).map_err(error)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
        size = size.checked_add(n as u64).ok_or("File too large.")?;
    }
    let after = f.metadata().map_err(error)?;
    if before.len() != size
        || after.len() != size
        || before.modified().ok() != after.modified().ok()
    {
        return Err("File changed while reading.".into());
    }
    Ok((size, format!("{:x}", h.finalize())))
}
pub fn archive_identity(
    path: &Path,
    cancel: impl Fn() -> bool,
) -> Result<(u64, String, &'static str)> {
    reject_link(path)?;
    let mut f = OpenOptions::new().read(true).open(path).map_err(error)?;
    let size = f.metadata().map_err(error)?.len();
    if size == 0 || size > MAX_BYTES {
        return Err("Archive is empty or exceeds 16 GiB.".into());
    }
    let mut magic = [0; 8];
    let n = f.read(&mut magic).map_err(error)?;
    let kind = if n >= 4
        && (magic[..4] == *b"PK\x03\x04"
            || magic[..4] == *b"PK\x05\x06"
            || magic[..4] == *b"PK\x07\x08")
    {
        "zip"
    } else if n >= 6 && magic[..6] == *b"7z\xbc\xaf\x27\x1c" {
        "7z"
    } else if n >= 7 && magic[..7] == *b"Rar!\x1a\x07\x00"
        || n == 8 && magic == *b"Rar!\x1a\x07\x01\x00"
    {
        "rar"
    } else {
        return Err("The downloaded file is not a supported archive; a login page or error may have been returned.".into());
    };
    let (bytes, hash) = file_hash(path, cancel)?;
    Ok((bytes, hash, kind))
}
#[cfg(test)]
mod tests;
