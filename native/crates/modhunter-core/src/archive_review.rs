//! Bounded archive inspection through the pinned official 7-Zip distribution.
//! Nothing here installs a mod. Source bytes and complete extracted manifests
//! are bound together before a caller may offer a native installer handoff.
use crate::acquisition::{archive_identity, atomic_json, file_hash, reject_link};
use crate::content_vault::checked_root;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::{Duration, Instant},
};

type Result<T> = std::result::Result<T, String>;
const MAX_FILES: usize = 200_000;
const MAX_EXPANDED: u64 = 64 * 1024 * 1024 * 1024;
const MAX_LIST: u64 = 128 * 1024 * 1024;
const MARKER: &str = ".modhunter-review-owned.json";
const OWNER: &str = "Modhunter-archive-review";
const LINUX_SHA: &str = "f94066e93f9a807547a3bff1021b3ec79d5ea4f57dfead018e9698182ad283b5";
const WINDOWS_SHA: &str = "dc0493429fe28136fcb177a0e77c9875cbb3b0bff5f2f8eeba4df302e98cef76";
const WINDOWS_DLL_SHA: &str = "500db9eb17f3c21b729ba41dd02ecfe1823da05028c7e4ba129084cc74cc364e";
fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Request {
    pub action: String,
    pub archive: PathBuf,
    pub stage: PathBuf,
    pub expected_sha256: String,
    pub expected_bytes: u64,
    #[serde(default)]
    pub selected: Vec<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct Entry {
    bytes: u64,
    sha256: String,
}
type Manifest = BTreeMap<String, Entry>;
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Marker {
    schema: u32,
    owner: String,
    archive: PathBuf,
    archive_bytes: u64,
    archive_sha256: String,
    prefix: String,
    layout: String,
    requires_installer: bool,
    files: Manifest,
    directories: BTreeSet<String>,
}
#[derive(Debug, Clone)]
struct Listed {
    files: BTreeMap<String, u64>,
    directories: BTreeSet<String>,
}

fn helper(cancel: &impl Fn() -> bool) -> Result<PathBuf> {
    let candidate = if let Some(path) = std::env::var_os("MODHUNTER_ARCHIVE_HELPER") {
        PathBuf::from(path)
    } else {
        std::env::current_exe()
            .map_err(err)?
            .parent()
            .ok_or("Missing executable directory.")?
            .join("archive-tools")
            .join(if cfg!(windows) { "7z.exe" } else { "7zz" })
    };
    if !candidate.is_absolute() {
        return Err("Archive helper must have an absolute path.".into());
    }
    checked_root(candidate.parent().ok_or("Missing helper directory.")?)?;
    let expected = if cfg!(windows) {
        WINDOWS_SHA
    } else {
        LINUX_SHA
    };
    if file_hash(&candidate, cancel)?.1 != expected {
        return Err("Archive helper does not match pinned official 7-Zip 26.06.".into());
    }
    if cfg!(windows) && file_hash(&candidate.with_file_name("7z.dll"), cancel)?.1 != WINDOWS_DLL_SHA
    {
        return Err("Archive helper library does not match pinned official 7-Zip 26.06.".into());
    }
    Ok(candidate)
}

fn run(
    helper: &Path,
    args: &[String],
    cwd: Option<&Path>,
    cancel: &impl Fn() -> bool,
) -> Result<String> {
    if cancel() {
        return Err("Archive review cancelled.".into());
    }
    let stdout = tempfile::tempfile().map_err(err)?;
    let stderr = tempfile::tempfile().map_err(err)?;
    let mut command = Command::new(helper);
    command
        .args(args)
        .stdin(Stdio::null())
        .stdout(stdout.try_clone().map_err(err)?)
        .stderr(stderr.try_clone().map_err(err)?);
    if let Some(cwd) = cwd {
        command.current_dir(cwd);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    struct KillOnDrop(std::process::Child);
    impl Drop for KillOnDrop {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }
    let mut child = KillOnDrop(command.spawn().map_err(err)?);
    let deadline = Instant::now() + Duration::from_secs(180);
    let status = loop {
        if cancel()
            || Instant::now() >= deadline
            || stdout.metadata().map_err(err)?.len() > MAX_LIST
            || stderr.metadata().map_err(err)?.len() > 1024 * 1024
        {
            let _ = child.0.kill();
            let _ = child.0.wait();
            return Err("Archive operation cancelled, exceeded its time bound, or produced an oversized listing.".into());
        }
        match child.0.try_wait().map_err(err)? {
            Some(status) => break status,
            None => std::thread::sleep(Duration::from_millis(20)),
        }
    };
    use std::io::{Seek, SeekFrom};
    let mut stdout = stdout;
    let mut stderr = stderr;
    stdout.seek(SeekFrom::Start(0)).map_err(err)?;
    stderr.seek(SeekFrom::Start(0)).map_err(err)?;
    let mut output = Vec::new();
    stdout
        .take(MAX_LIST + 1)
        .read_to_end(&mut output)
        .map_err(err)?;
    if output.len() as u64 > MAX_LIST {
        return Err("Archive listing exceeds its complete-result bound.".into());
    }
    if !status.success() {
        let mut message = String::new();
        stderr
            .take(4096)
            .read_to_string(&mut message)
            .map_err(err)?;
        return Err(format!(
            "Archive helper rejected the input ({}): {}",
            status.code().unwrap_or(-1),
            message.trim()
        ));
    }
    String::from_utf8(output)
        .map_err(|_| "Archive names must be valid UTF-8 for a reliable file review.".into())
}

pub(crate) fn safe_name(name: &str) -> Result<String> {
    if name.is_empty()
        || name.len() > 32760
        || name.chars().any(|c| c.is_control())
        || name.contains(['\\', ':'])
        || name.starts_with('/')
    {
        return Err("Archive contains an unsafe or ambiguous relative path.".into());
    }
    for component in name.split('/') {
        if component.is_empty()
            || matches!(component, "." | "..")
            || component.ends_with(['.', ' '])
        {
            return Err("Archive contains traversal or Windows path aliases.".into());
        }
        let stem = component
            .split('.')
            .next()
            .unwrap_or("")
            .trim_end_matches(' ')
            .to_ascii_uppercase();
        if matches!(
            stem.as_str(),
            "CON" | "PRN" | "AUX" | "NUL" | "CLOCK$" | "CONIN$" | "CONOUT$"
        ) || ["COM", "LPT"].iter().any(|p| {
            stem.strip_prefix(p).is_some_and(|v| {
                v.chars().count() == 1
                    && (matches!(v.as_bytes()[0], b'1'..=b'9') || matches!(v, "¹" | "²" | "³"))
            })
        }) {
            return Err("Archive contains a reserved Windows device name.".into());
        }
        if component.contains(['<', '>', '"', '|', '?', '*']) {
            return Err("Archive contains a name unsupported by Windows.".into());
        }
    }
    Ok(name.to_owned())
}

fn listing(text: &str, archive_bytes: u64) -> Result<Listed> {
    let mut files = BTreeMap::new();
    let mut directories = BTreeSet::new();
    let mut identities = BTreeMap::<String, (String, bool)>::new();
    let mut total = 0u64;
    for block in text
        .replace("\r\n", "\n")
        .split("\n\n")
        .filter(|b| !b.trim().is_empty())
    {
        let mut fields = BTreeMap::new();
        for line in block.lines() {
            let (key, value) = line
                .split_once(" = ")
                .ok_or("Archive listing contains ambiguous metadata or names.")?;
            if fields.insert(key, value).is_some() {
                return Err("Archive listing contains duplicate or injected metadata.".into());
            }
        }
        let name = safe_name(fields.get("Path").ok_or("Archive entry has no path.")?)?;
        let folder = fields.get("Folder").copied() == Some("+")
            || fields.get("Attributes").is_some_and(|s| s.starts_with('D'));
        if fields.get("Encrypted").copied().is_some_and(|v| v != "-")
            || fields.iter().any(|(k, v)| {
                (k.to_ascii_lowercase().contains("link")
                    || k.to_ascii_lowercase().contains("reparse"))
                    && !v.is_empty()
                    && *v != "-"
            })
            || fields.get("Attributes").is_some_and(|s| {
                s.split_whitespace()
                    .any(|v| matches!(v.chars().next(), Some('l' | 'b' | 'c' | 'p' | 's')))
            })
        {
            return Err(
                "Encrypted entries, links, reparse points, and special files need manual handling."
                    .into(),
            );
        }
        let size = fields
            .get("Size")
            .ok_or("Archive entry has no complete size.")?
            .parse::<u64>()
            .map_err(err)?;
        if folder && size != 0 {
            return Err("Directory entry has unexpected payload bytes.".into());
        }
        if identities
            .insert(name.to_uppercase(), (name.clone(), folder))
            .is_some()
        {
            return Err("Archive contains duplicate or case-colliding entries.".into());
        }
        if folder {
            directories.insert(name);
        } else {
            total = total
                .checked_add(size)
                .ok_or("Expanded archive size overflow.")?;
            files.insert(name, size);
        }
        if files.len() + directories.len() > MAX_FILES
            || total > MAX_EXPANDED
            || total > archive_bytes.saturating_mul(1000).max(64 * 1024 * 1024)
        {
            return Err("Archive exceeds the complete-entry or expansion bound.".into());
        }
    }
    if files.is_empty() {
        return Err("Archive contains no installable regular files.".into());
    }
    for name in identities.values().map(|(name, _)| name) {
        for parent in Path::new(name)
            .ancestors()
            .skip(1)
            .filter(|p| !p.as_os_str().is_empty())
        {
            let p = parent
                .to_str()
                .ok_or("Unreadable archive directory.")?
                .replace('\\', "/");
            if let Some((existing, folder)) = identities.get(&p.to_uppercase())
                && (!folder || existing != &p)
            {
                return Err("Archive file/directory or case aliases overlap.".into());
            }
            directories.insert(p);
        }
    }
    // Implicit directories must have one spelling too, even if no directory
    // records were stored in the container.
    let mut seen = BTreeMap::new();
    for name in &directories {
        if seen.insert(name.to_uppercase(), name).is_some() {
            return Err("Archive contains aliased parent directory spellings.".into());
        }
        if files.keys().any(|f| f.eq_ignore_ascii_case(name)) {
            return Err("Archive file/directory paths overlap.".into());
        }
    }
    Ok(Listed { files, directories })
}

fn archive_type(path: &Path, kind: &str) -> Result<String> {
    if kind != "rar" {
        return Ok(format!("-t{kind}"));
    }
    let mut bytes = [0u8; 8];
    fs::File::open(path)
        .map_err(err)?
        .read_exact(&mut bytes)
        .map_err(err)?;
    Ok(if bytes[6] == 1 { "-tRar5" } else { "-tRar" }.into())
}
fn list(
    helper: &Path,
    path: &Path,
    kind: &str,
    bytes: u64,
    cancel: &impl Fn() -> bool,
) -> Result<Listed> {
    listing(
        &run(
            helper,
            &[
                "l".into(),
                archive_type(path, kind)?,
                "-slt".into(),
                "-ba".into(),
                "-sccUTF-8".into(),
                "--".into(),
                path.to_string_lossy().into_owned(),
            ],
            None,
            cancel,
        )?,
        bytes,
    )
}
fn extracted(root: &Path, expected: &Listed, cancel: &impl Fn() -> bool) -> Result<Manifest> {
    let mut files = Manifest::new();
    let mut dirs = BTreeSet::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        if cancel() {
            return Err("Archive review cancelled.".into());
        }
        reject_link(&dir)?;
        for child in fs::read_dir(&dir).map_err(err)? {
            let child = child.map_err(err)?;
            let path = child.path();
            reject_link(&path)?;
            let name = safe_name(
                path.strip_prefix(root)
                    .map_err(err)?
                    .to_str()
                    .ok_or("Non-UTF-8 extracted path.")?
                    .replace('\\', "/")
                    .as_str(),
            )?;
            let kind = child.file_type().map_err(err)?;
            if kind.is_dir() {
                if !expected.directories.contains(&name) {
                    return Err("Extraction created an unexpected directory.".into());
                }
                dirs.insert(name);
                stack.push(path);
            } else if kind.is_file() {
                let expected_size = expected
                    .files
                    .get(&name)
                    .ok_or("Extraction created a file absent from the approved listing.")?;
                let (bytes, sha256) = file_hash(&path, cancel)?;
                if bytes != *expected_size {
                    return Err("Extracted file size differs from its complete listing.".into());
                }
                files.insert(name, Entry { bytes, sha256 });
            } else {
                return Err("Extraction created a non-regular filesystem entry.".into());
            }
        }
    }
    if files.len() != expected.files.len() || dirs != expected.directories {
        return Err("Extraction did not reproduce the complete approved file tree.".into());
    }
    Ok(files)
}
fn extract(
    helper: &Path,
    archive: &Path,
    kind: &str,
    root: &Path,
    cancel: &impl Fn() -> bool,
) -> Result<()> {
    fs::create_dir(root).map_err(err)?;
    run(
        helper,
        &[
            "x".into(),
            archive_type(archive, kind)?,
            "-y".into(),
            "-bd".into(),
            "-bb0".into(),
            "-mmt=2".into(),
            "-sccUTF-8".into(),
            format!("-o{}", root.display()),
            "--".into(),
            archive.to_string_lossy().into_owned(),
        ],
        None,
        cancel,
    )?;
    Ok(())
}
fn known_dir(name: &str) -> bool {
    matches!(
        name.to_ascii_lowercase().as_str(),
        "textures"
            | "meshes"
            | "scripts"
            | "skse"
            | "interface"
            | "sound"
            | "music"
            | "strings"
            | "seq"
            | "shaders"
            | "video"
            | "grass"
            | "fomod"
            | "calientetools"
            | "nemesis_engine"
            | "dyndolod"
            | "netscriptframework"
    )
}
fn normalize(files: &Manifest, directories: &BTreeSet<String>) -> (String, String, bool) {
    let installer = files
        .keys()
        .any(|n| n.to_lowercase().ends_with("fomod/moduleconfig.xml"));
    let mut prefix = String::new();
    for _ in 0..4 {
        let names: Vec<_> = files
            .keys()
            .map(|n| n.strip_prefix(&prefix).unwrap_or(n))
            .collect();
        let roots: BTreeSet<_> = names
            .iter()
            .filter_map(|n| n.split_once('/').map(|(p, _)| p))
            .collect();
        if names.iter().any(|n| !n.contains('/')) || roots.len() != 1 {
            break;
        }
        let first = *roots.first().unwrap();
        if known_dir(first) {
            break;
        }
        // A single wrapper or Data directory is unambiguous only if no other
        // explicit empty tree sits alongside it.
        if directories
            .iter()
            .filter_map(|d| d.strip_prefix(&prefix))
            .any(|d| d.split('/').next() != Some(first))
        {
            break;
        }
        prefix.push_str(first);
        prefix.push('/');
    }
    let names: Vec<_> = files
        .keys()
        .map(|n| n.strip_prefix(&prefix).unwrap_or(n))
        .collect();
    let dirs_ok = names
        .iter()
        .filter_map(|n| n.split_once('/').map(|(d, _)| d))
        .all(known_dir);
    let game_content = names.iter().any(|n| {
        n.contains('/')
            || [
                ".esp", ".esm", ".esl", ".bsa", ".ba2", ".nif", ".dds", ".pex", ".hkx",
            ]
            .iter()
            .any(|ext| n.to_ascii_lowercase().ends_with(ext))
    });
    let layout = if dirs_ok && game_content {
        "gameData"
    } else {
        "needsReview"
    };
    (prefix, layout.into(), installer)
}
fn manifest_digest(marker: &Marker) -> Result<String> {
    Ok(format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(marker).map_err(err)?)
    ))
}
fn entries(marker: &Marker) -> Vec<Value> {
    marker.files.iter().map(|(path,e)|json!({"path":path.strip_prefix(&marker.prefix).unwrap_or(path),"bytes":e.bytes,"sha256":e.sha256})).collect()
}
fn output(stage: &Path, marker: &Marker) -> Result<Value> {
    Ok(
        json!({"ok":true,"archive":{"path":marker.archive,"bytes":marker.archive_bytes,"sha256":marker.archive_sha256},"archiveIdentity":{"path":marker.archive,"bytes":marker.archive_bytes,"sha256":marker.archive_sha256},"stage":stage,"incomingRoot":stage.join("raw").join(marker.prefix.trim_end_matches('/')),"layout":marker.layout,"requiresInstaller":marker.requires_installer,"entries":entries(marker),"manifestDigest":manifest_digest(marker)?,"selectionEnforced":false,"scope":"Complete archive bytes and paths are reviewed. FOMOD and uncertain layouts require native installer choices."}),
    )
}
fn checked_marker(request: &Request, cancel: &impl Fn() -> bool) -> Result<(PathBuf, Marker)> {
    let stage = checked_root(&request.stage)?;
    let path = stage.join(MARKER);
    reject_link(&path)?;
    let bytes = fs::metadata(&path).map_err(err)?.len();
    if bytes > MAX_LIST {
        return Err("Archive receipt exceeds its bound.".into());
    }
    let data = fs::read(path).map_err(err)?;
    let marker: Marker = serde_json::from_slice(&data).map_err(err)?;
    if serde_json::to_vec(&marker).map_err(err)? != data
        || marker.files.len() > MAX_FILES
        || marker.directories.len() > MAX_FILES
    {
        return Err("Review marker is not the exact bounded canonical receipt.".into());
    }
    let normalized = normalize(&marker.files, &marker.directories);
    if normalized
        != (
            marker.prefix.clone(),
            marker.layout.clone(),
            marker.requires_installer,
        )
    {
        return Err("Review marker normalization no longer matches its complete manifest.".into());
    }
    let archive = checked_root(request.archive.parent().ok_or("Missing archive parent.")?)?
        .join(request.archive.file_name().ok_or("Missing archive name.")?);
    if marker.schema != 1
        || marker.owner != OWNER
        || marker.archive != archive
        || marker.archive_bytes != request.expected_bytes
        || marker.archive_sha256 != request.expected_sha256
    {
        return Err("Archive review belongs to different source bytes.".into());
    }
    verify_source(request, cancel)?;
    let expected = Listed {
        files: marker
            .files
            .iter()
            .map(|(k, v)| (k.clone(), v.bytes))
            .collect(),
        directories: marker.directories.clone(),
    };
    if extracted(&stage.join("raw"), &expected, cancel)? != marker.files {
        return Err("Reviewed archive content changed after inspection.".into());
    }
    Ok((stage, marker))
}
fn verify_source(request: &Request, cancel: &impl Fn() -> bool) -> Result<&'static str> {
    checked_root(request.archive.parent().ok_or("Missing archive parent.")?)?;
    let (bytes, hash, kind) = archive_identity(&request.archive, cancel)?;
    if bytes != request.expected_bytes || hash != request.expected_sha256 || hash.len() != 64 {
        return Err("Archive no longer matches its verified download receipt.".into());
    }
    Ok(kind)
}

fn cleanup_failed_stage(stage: &Path, parent: &Path, expected: &Listed) -> Result<()> {
    if checked_root(stage.parent().ok_or("Missing stage parent.")?)? != parent
        || checked_root(stage)? != stage
    {
        return Err("Changed stage identity preserved.".into());
    }
    let marker = stage.join(MARKER);
    reject_link(&marker)?;
    if fs::read(&marker).map_err(err)?
        != b"{\"schema\":1,\"owner\":\"Modhunter-archive-review\",\"state\":\"extracting\"}"
    {
        return Err("Changed stage ownership preserved.".into());
    }
    for item in fs::read_dir(stage).map_err(err)? {
        let item = item.map_err(err)?;
        reject_link(&item.path())?;
        if !matches!(item.file_name().to_str(), Some("raw" | MARKER)) {
            return Err("Unexpected stage files preserved.".into());
        }
    }
    let raw = stage.join("raw");
    if raw.exists() {
        let mut stack = vec![raw.clone()];
        while let Some(dir) = stack.pop() {
            reject_link(&dir)?;
            for item in fs::read_dir(dir).map_err(err)? {
                let item = item.map_err(err)?;
                let path = item.path();
                reject_link(&path)?;
                let name = path
                    .strip_prefix(&raw)
                    .map_err(err)?
                    .to_str()
                    .ok_or("Unknown extraction path.")?
                    .replace('\\', "/");
                let meta = item.metadata().map_err(err)?;
                if meta.is_dir() && expected.directories.contains(&name) {
                    stack.push(path);
                } else if !meta.is_file()
                    || expected
                        .files
                        .get(&name)
                        .is_none_or(|size| meta.len() > *size)
                {
                    return Err("Unexpected extracted content preserved.".into());
                }
            }
        }
    }
    fs::remove_dir_all(stage).map_err(err)
}

pub fn inspect(request: &Request, cancel: &impl Fn() -> bool) -> Result<Value> {
    if request.action == "verify" {
        let (stage, marker) = checked_marker(request, cancel)?;
        return Ok(
            json!({"ok":true,"verified":true,"manifestDigest":manifest_digest(&marker)?,"incomingRoot":stage.join("raw").join(marker.prefix.trim_end_matches('/')),"archiveIdentity":{"path":marker.archive,"bytes":marker.archive_bytes,"sha256":marker.archive_sha256},"layout":marker.layout,"requiresInstaller":marker.requires_installer}),
        );
    }
    if request.action == "prepareSelected" {
        return prepare_selected(request, cancel);
    }
    if request.action != "inspect" {
        return Err("Unknown archive-review action.".into());
    }
    let kind = verify_source(request, cancel)?;
    let helper = helper(cancel)?;
    let complete = list(
        &helper,
        &request.archive,
        kind,
        request.expected_bytes,
        cancel,
    )?;
    let parent = checked_root(request.stage.parent().ok_or("Missing stage parent.")?)?;
    let stage = parent.join(request.stage.file_name().ok_or("Missing stage name.")?);
    if stage == request.archive || request.archive.starts_with(&stage) {
        return Err("Archive and extraction stage must be separate.".into());
    }
    if stage.exists() {
        checked_root(&stage)?;
        if fs::read_dir(&stage).map_err(err)?.next().is_some() {
            return Err("Choose an empty extraction stage.".into());
        }
    } else {
        fs::create_dir(&stage).map_err(err)?;
    }
    let marker_path = stage.join(MARKER);
    let mut marker_file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&marker_path)
        .map_err(err)?;
    marker_file
        .write_all(
            b"{\"schema\":1,\"owner\":\"Modhunter-archive-review\",\"state\":\"extracting\"}",
        )
        .map_err(err)?;
    marker_file.sync_all().map_err(err)?;
    let result = (|| {
        extract(&helper, &request.archive, kind, &stage.join("raw"), cancel)?;
        let files = extracted(&stage.join("raw"), &complete, cancel)?;
        verify_source(request, cancel)?;
        let (prefix, layout, requires_installer) = normalize(&files, &complete.directories);
        let archive = checked_root(request.archive.parent().unwrap())?
            .join(request.archive.file_name().unwrap());
        let marker = Marker {
            schema: 1,
            owner: OWNER.into(),
            archive,
            archive_bytes: request.expected_bytes,
            archive_sha256: request.expected_sha256.clone(),
            prefix,
            layout,
            requires_installer,
            files,
            directories: complete.directories.clone(),
        };
        atomic_json(&marker_path, &marker)?;
        output(&stage, &marker)
    })();
    if result.is_err() {
        let _ = cleanup_failed_stage(&stage, &parent, &complete);
    }
    result
}

fn prepare_selected(request: &Request, cancel: &impl Fn() -> bool) -> Result<Value> {
    let (stage, marker) = checked_marker(request, cancel)?;
    if marker.requires_installer || marker.layout != "gameData" {
        return Err(
            "FOMOD and uncertain layouts keep the original archive for MO2's installer.".into(),
        );
    }
    if request.selected.is_empty() || request.selected.len() > MAX_FILES {
        return Err("Select a nonempty bounded set of incoming files.".into());
    }
    let mut selected = Manifest::new();
    for name in &request.selected {
        safe_name(name)?;
        if name.eq_ignore_ascii_case("meta.ini") {
            return Err("MO2 metadata cannot be selected from an archive.".into());
        }
        let entry = marker
            .files
            .get(&format!("{}{name}", marker.prefix))
            .ok_or("Selected path is absent from the exact reviewed manifest.")?;
        if selected.insert(name.clone(), entry.clone()).is_some() {
            return Err("A selected path was repeated.".into());
        }
    }
    let selection_digest=format!("{:x}",Sha256::digest(serde_json::to_vec(&json!({"originalSha256":marker.archive_sha256,"manifestDigest":manifest_digest(&marker)?,"selected":selected})).map_err(err)?));
    let destination = stage.join(format!("selected-{selection_digest}.zip"));
    let helper = helper(cancel)?;
    if destination.exists() {
        let receipt_path = stage.join(format!("selected-{selection_digest}.json"));
        reject_link(&receipt_path)?;
        if fs::metadata(&receipt_path).map_err(err)?.len() > MAX_LIST {
            return Err("Selected receipt exceeds its bound.".into());
        }
        let receipt: Value =
            serde_json::from_slice(&fs::read(&receipt_path).map_err(err)?).map_err(err)?;
        let (bytes, sha256, kind) = archive_identity(&destination, cancel)?;
        let expected = json!({"path":destination,"bytes":bytes,"sha256":sha256,"originalSha256":marker.archive_sha256,"selectionDigest":selection_digest,"entries":selected.iter().map(|(path,e)|json!({"path":path,"bytes":e.bytes,"sha256":e.sha256})).collect::<Vec<_>>()});
        if receipt != expected {
            return Err(
                "Existing selected bytes or their receipt changed; preserve for review.".into(),
            );
        }
        let complete = list(&helper, &destination, kind, bytes, cancel)?;
        let temp = tempfile::Builder::new()
            .prefix("selection-recheck-")
            .tempdir_in(&stage)
            .map_err(err)?;
        let extracted_root = temp.path().join("files");
        extract(&helper, &destination, kind, &extracted_root, cancel)?;
        if extracted(&extracted_root, &complete, cancel)? != selected {
            return Err(
                "Existing prepared archive no longer contains exactly the selected files.".into(),
            );
        }
        checked_marker(request, cancel)?;
        return selected_result(
            &stage,
            &marker,
            receipt,
            &selection_digest,
            &request.selected,
        );
    }
    let temp = tempfile::Builder::new()
        .prefix("selection-")
        .tempdir_in(&stage)
        .map_err(err)?;
    let files = temp.path().join("files");
    fs::create_dir(&files).map_err(err)?;
    for (name, entry) in &selected {
        if cancel() {
            return Err("Archive selection cancelled.".into());
        }
        let source = stage.join("raw").join(&marker.prefix).join(name);
        reject_link(&source)?;
        let dest = files.join(name);
        fs::create_dir_all(dest.parent().unwrap()).map_err(err)?;
        let mut input = fs::File::open(source).map_err(err)?;
        let mut output = fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&dest)
            .map_err(err)?;
        let mut copied = 0u64;
        let mut buffer = [0u8; 128 * 1024];
        loop {
            if cancel() {
                return Err("Archive selection cancelled.".into());
            }
            let n = input.read(&mut buffer).map_err(err)?;
            if n == 0 {
                break;
            }
            copied += n as u64;
            if copied > entry.bytes {
                return Err("Selected file grew during preparation.".into());
            }
            output.write_all(&buffer[..n]).map_err(err)?;
        }
        output.sync_all().map_err(err)?;
        if file_hash(&dest, cancel)? != (entry.bytes, entry.sha256.clone()) {
            return Err("Selected content changed while preparing its ZIP.".into());
        }
    }
    let packed = temp.path().join("selected.zip");
    run(
        &helper,
        &[
            "a".into(),
            "-tzip".into(),
            "-mx=1".into(),
            "-bd".into(),
            "-bb0".into(),
            "-y".into(),
            "--".into(),
            packed.to_string_lossy().into_owned(),
            ".".into(),
        ],
        Some(&files),
        cancel,
    )?;
    let (bytes, sha256, kind) = archive_identity(&packed, cancel)?;
    let complete = list(&helper, &packed, kind, bytes, cancel)?;
    let verify = temp.path().join("verify");
    extract(&helper, &packed, kind, &verify, cancel)?;
    if extracted(&verify, &complete, cancel)? != selected {
        return Err("Derived archive did not preserve exactly the approved content.".into());
    }
    checked_marker(request, cancel)?;
    fs::rename(&packed, &destination).map_err(err)?;
    let prepared = json!({"path":destination,"bytes":bytes,"sha256":sha256,"originalSha256":marker.archive_sha256,"selectionDigest":selection_digest,"entries":selected.iter().map(|(path,e)|json!({"path":path,"bytes":e.bytes,"sha256":e.sha256})).collect::<Vec<_>>()});
    atomic_json(
        &stage.join(format!("selected-{selection_digest}.json")),
        &prepared,
    )?;
    selected_result(
        &stage,
        &marker,
        prepared,
        &selection_digest,
        &request.selected,
    )
}
fn selected_result(
    stage: &Path,
    marker: &Marker,
    prepared: Value,
    selection_digest: &str,
    selected: &[String],
) -> Result<Value> {
    let mut value = output(stage, marker)?;
    value["selectionEnforced"] = json!(true);
    value["selectedArchive"] = prepared.clone();
    value["preparedArchive"] = prepared;
    value["selectionDigest"] = json!(selection_digest);
    value["selectedPaths"] = json!(selected);
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unsafe_paths_and_windows_aliases_are_rejected() {
        for p in [
            "../a",
            "/a",
            "a/../b",
            "a\\b",
            "a:stream",
            "CON",
            "nul.txt",
            "a. ",
            "a//b",
            "a\nb",
            "a/COM1.txt",
        ] {
            assert!(safe_name(p).is_err(), "{p}");
        }
        assert!(safe_name("textures/body.dds").is_ok());
    }
    #[test]
    fn listing_requires_complete_unique_nonlinked_tree() {
        let block = |p: &str| format!("Path = {p}\nFolder = -\nSize = 3\nEncrypted = -\n\n");
        assert!(listing(&(block("A/x.dds") + &block("a/y.dds")), 100).is_err());
        assert!(listing(&(block("a") + &block("a/b")), 100).is_err());
        assert!(listing(&(block("a") + &block("a")), 100).is_err());
        assert!(listing(&(block("mesh/σ.nif") + &block("mesh/ς.nif")), 100).is_err());
        assert!(listing("Path = a\nFolder = -\nSize = 3\nEncrypted = +\n\n", 100).is_err());
        assert!(
            listing(
                "Path = a\nFolder = -\nSize = 3\nSymbolic Link = elsewhere\n\n",
                100
            )
            .is_err()
        );
        let good = listing(&block("textures/a.dds"), 100).unwrap();
        assert!(good.directories.contains("textures"));
    }
    #[test]
    fn normalization_preserves_sidecars_and_installer_scope() {
        let m = |names: &[&str]| {
            names
                .iter()
                .map(|n| {
                    (
                        n.to_string(),
                        Entry {
                            bytes: 1,
                            sha256: "a".repeat(64),
                        },
                    )
                })
                .collect::<Manifest>()
        };
        let f = m(&["Wrapper/Data/textures/a.dds"]);
        let d = BTreeSet::from([
            "Wrapper".into(),
            "Wrapper/Data".into(),
            "Wrapper/Data/textures".into(),
        ]);
        assert_eq!(normalize(&f, &d).0, "Wrapper/Data/");
        let f = m(&["Data/textures/a.dds", "Readme.txt"]);
        assert_eq!(normalize(&f, &BTreeSet::new()).0, "");
        assert_eq!(normalize(&f, &BTreeSet::new()).1, "needsReview");
        assert!(
            normalize(
                &m(&["fomod/moduleconfig.xml", "textures/a.dds"]),
                &BTreeSet::new()
            )
            .2
        );
    }
}
