//! Verify the complete installer resource against the manifest pinned into the
//! desktop executable. A manifest edited alongside a plugin is not authority.
#![cfg_attr(not(windows), allow(dead_code))]

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::Read,
    path::{Path, PathBuf},
};

type Result<T> = std::result::Result<T, String>;
const MANIFEST: &str = "SHA256SUMS.txt";
pub const PLUGIN_PREFIX: &str = "MO2/mo2_update_arsenal/";
const REQUIRED: &[&str] = &[
    "BUILD_INFO.json",
    "Install_MO2_Plugin.ps1",
    "MO2/mo2_update_arsenal/__init__.py",
    "MO2/mo2_update_arsenal/plugin.py",
    "MO2/mo2_update_arsenal/version.py",
    "MO2/mo2_update_arsenal/web/index.html",
    "MO2/mo2_update_arsenal/bin/modhunter-cli.exe",
    "MO2/mo2_update_arsenal/bin/modhunter-qt-bridge.dll",
    "MO2/mo2_update_arsenal/bin/archive-tools/7z.exe",
    "MO2/mo2_update_arsenal/bin/archive-tools/7z.dll",
    "MO2/mo2_update_arsenal/bin/archive-tools/License.txt",
    "MO2/mo2_update_arsenal/bin/archive-tools/provenance.json",
];

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BuildIdentity {
    pub version: String,
    pub commit: String,
    pub workflow: String,
}

#[derive(Clone, Debug)]
pub struct VerifiedBundle {
    pub root: PathBuf,
    pub identity: BuildIdentity,
    pub manifest_sha256: String,
    pub files: BTreeMap<String, String>,
}

pub fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn linked(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        metadata.file_type().is_symlink()
    }
}

pub fn reject_link_ancestors(path: &Path) -> Result<()> {
    if !path.is_absolute() {
        return Err("A complete native filesystem path is required.".into());
    }
    for ancestor in path.ancestors() {
        let metadata = fs::symlink_metadata(ancestor)
            .map_err(|error| format!("Cannot inspect {}: {error}", ancestor.display()))?;
        if linked(&metadata) {
            return Err(format!(
                "Linked paths cannot be used for plugin installation: {}",
                ancestor.display()
            ));
        }
    }
    Ok(())
}

fn regular_bytes(path: &Path, limit: usize) -> Result<Vec<u8>> {
    reject_link_ancestors(path)?;
    let metadata = fs::metadata(path).map_err(|error| error.to_string())?;
    if !metadata.is_file() || metadata.len() > limit as u64 {
        return Err(format!(
            "Invalid or oversized installer metadata: {}",
            path.display()
        ));
    }
    let mut bytes = Vec::with_capacity(metadata.len() as usize);
    fs::File::open(path)
        .map_err(|error| error.to_string())?
        .take(limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    if bytes.len() > limit {
        return Err("Installer metadata changed while being read.".into());
    }
    Ok(bytes)
}

pub fn hash_file(path: &Path) -> Result<String> {
    reject_link_ancestors(path)?;
    if !fs::metadata(path).is_ok_and(|metadata| metadata.is_file()) {
        return Err(format!(
            "Required regular file is missing: {}",
            path.display()
        ));
    }
    let mut file = fs::File::open(path).map_err(|error| error.to_string())?;
    let mut sha = Sha256::new();
    let mut buffer = [0_u8; 65_536];
    loop {
        let count = file.read(&mut buffer).map_err(|error| error.to_string())?;
        if count == 0 {
            break;
        }
        sha.update(&buffer[..count]);
    }
    Ok(format!("{:x}", sha.finalize()))
}

fn safe_relative(value: &str) -> bool {
    !value.is_empty()
        && !value.contains(['\\', ':'])
        && !value.chars().any(char::is_control)
        && value.split('/').all(|part| {
            !part.is_empty() && part != "." && part != ".." && !part.ends_with([' ', '.'])
        })
}

pub fn parse_manifest(bytes: &[u8]) -> Result<BTreeMap<String, String>> {
    let text = std::str::from_utf8(bytes.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(bytes))
        .map_err(|_| "The installer manifest must be UTF-8.".to_string())?;
    let mut files = BTreeMap::new();
    let mut identities = BTreeSet::new();
    for line in text.lines() {
        let (hash, name) = line
            .split_once("  ")
            .ok_or("The installer manifest contains an invalid record.")?;
        if hash.len() != 64
            || !hash.bytes().all(|byte| byte.is_ascii_hexdigit())
            || !safe_relative(name)
            || name.eq_ignore_ascii_case(MANIFEST)
            || !identities.insert(name.to_lowercase())
        {
            return Err("The installer manifest contains an unsafe or duplicate file.".into());
        }
        files.insert(name.to_owned(), hash.to_ascii_lowercase());
    }
    if files.is_empty() {
        return Err("The installer manifest is empty.".into());
    }
    Ok(files)
}

fn collect_regular_files(root: &Path) -> Result<BTreeSet<String>> {
    let mut result = BTreeSet::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        reject_link_ancestors(&directory)?;
        for entry in fs::read_dir(&directory).map_err(|error| error.to_string())? {
            let entry = entry.map_err(|error| error.to_string())?;
            let path = entry.path();
            let metadata = fs::symlink_metadata(&path).map_err(|error| error.to_string())?;
            if linked(&metadata) {
                return Err(format!(
                    "The installer resource contains a linked path: {}",
                    path.display()
                ));
            }
            if metadata.is_dir() {
                pending.push(path);
            } else if metadata.is_file() {
                let relative = path
                    .strip_prefix(root)
                    .map_err(|error| error.to_string())?
                    .to_str()
                    .ok_or("An installer resource filename cannot be represented safely.")?
                    .replace('\\', "/");
                result.insert(relative);
            } else {
                return Err("The installer resource contains a non-regular entry.".into());
            }
        }
    }
    Ok(result)
}

pub fn verify(root: &Path, pinned_manifest: &[u8], version: &str) -> Result<VerifiedBundle> {
    reject_link_ancestors(root)?;
    let manifest = regular_bytes(&root.join(MANIFEST), 4 * 1024 * 1024)?;
    if manifest != pinned_manifest {
        return Err("The MO2 installer resource does not match this Modhunter executable. Reinstall one complete matching release.".into());
    }
    let files = parse_manifest(&manifest)?;
    for required in REQUIRED {
        if !files.contains_key(*required) {
            return Err(format!(
                "The matching plugin bundle is incomplete: {required}"
            ));
        }
    }
    let actual = collect_regular_files(root)?;
    let mut expected: BTreeSet<_> = files.keys().cloned().collect();
    expected.insert(MANIFEST.into());
    if actual != expected {
        return Err("The MO2 installer resource contains missing or unexpected files. Reinstall the complete matching release.".into());
    }
    for (relative, expected) in &files {
        if hash_file(&root.join(relative))? != *expected {
            return Err(format!(
                "The bundled plugin checksum does not match: {relative}"
            ));
        }
    }
    let build = regular_bytes(&root.join("BUILD_INFO.json"), 65_536)?;
    let identity: BuildIdentity =
        serde_json::from_slice(build.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(&build))
            .map_err(|_| "The plugin bundle build identity is invalid.".to_string())?;
    if identity.version != version
        || !matches!(identity.commit.len(), 40 | 64)
        || !identity.commit.bytes().all(|byte| byte.is_ascii_hexdigit())
        || identity.workflow.is_empty()
    {
        return Err("The plugin bundle belongs to a different or invalid release.".into());
    }
    Ok(VerifiedBundle {
        root: root.to_path_buf(),
        identity,
        manifest_sha256: digest(&manifest),
        files,
    })
}

pub fn verify_installed(bundle: &VerifiedBundle, application: &Path) -> Result<()> {
    reject_link_ancestors(application)?;
    let destination = application.join("plugins/mo2_update_arsenal");
    for (name, expected) in &bundle.files {
        let Some(relative) = name.strip_prefix(PLUGIN_PREFIX) else {
            continue;
        };
        if hash_file(&destination.join(relative))? != *expected {
            return Err(format!(
                "Installed plugin verification failed for {relative}. Keep the recorded backup and review the result."
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> (tempfile::TempDir, Vec<u8>) {
        let root = tempfile::tempdir().unwrap();
        let mut entries = Vec::new();
        for relative in REQUIRED {
            let path = root.path().join(relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            let bytes = if *relative == "BUILD_INFO.json" {
                serde_json::to_vec(&serde_json::json!({"version":"0.13.0","commit":"a".repeat(40),"workflow":"fixture"})).unwrap()
            } else {
                format!("Owned fixture: {relative}\n").into_bytes()
            };
            fs::write(path, &bytes).unwrap();
            entries.push(format!("{}  {relative}\n", digest(&bytes)));
        }
        entries.sort();
        let manifest = entries.concat().into_bytes();
        fs::write(root.path().join(MANIFEST), &manifest).unwrap();
        (root, manifest)
    }

    #[test]
    fn pins_all_payload_bytes_and_rejects_modified_manifest_and_script() {
        let (root, manifest) = fixture();
        assert!(verify(root.path(), &manifest, "0.13.0").is_ok());
        fs::write(root.path().join("Install_MO2_Plugin.ps1"), b"changed").unwrap();
        assert!(verify(root.path(), &manifest, "0.13.0").is_err());
        let edited = String::from_utf8(manifest.clone()).unwrap().replace(
            &digest(b"Owned fixture: Install_MO2_Plugin.ps1\n"),
            &digest(b"changed"),
        );
        fs::write(root.path().join(MANIFEST), edited).unwrap();
        assert!(
            verify(root.path(), &manifest, "0.13.0")
                .unwrap_err()
                .contains("does not match this")
        );
    }

    #[test]
    fn rejects_extras_missing_components_wrong_build_and_unsafe_paths() {
        let (root, manifest) = fixture();
        assert!(verify(root.path(), &manifest, "0.12.1").is_err());
        fs::write(root.path().join("surprise.py"), b"extra").unwrap();
        assert!(verify(root.path(), &manifest, "0.13.0").is_err());
        fs::remove_file(root.path().join("surprise.py")).unwrap();
        fs::remove_file(root.path().join("MO2/mo2_update_arsenal/plugin.py")).unwrap();
        assert!(verify(root.path(), &manifest, "0.13.0").is_err());
        for name in [
            "../outside",
            "/absolute",
            "a//b",
            "a\\b",
            "C:/escape",
            "a/../b",
            "file:stream",
            "file.",
        ] {
            assert!(parse_manifest(format!("{}  {name}\n", "a".repeat(64)).as_bytes()).is_err());
        }
        assert!(
            parse_manifest(
                format!("{}  a.py\n{}  A.py\n", "a".repeat(64), "b".repeat(64)).as_bytes()
            )
            .is_err()
        );
    }

    #[test]
    fn verifies_installed_payload_without_requiring_data_to_match_package() {
        let (root, manifest) = fixture();
        let bundle = verify(root.path(), &manifest, "0.13.0").unwrap();
        let target = tempfile::tempdir().unwrap();
        for name in bundle.files.keys() {
            if let Some(relative) = name.strip_prefix(PLUGIN_PREFIX) {
                let path = target
                    .path()
                    .join("plugins/mo2_update_arsenal")
                    .join(relative);
                fs::create_dir_all(path.parent().unwrap()).unwrap();
                fs::copy(root.path().join(name), path).unwrap();
            }
        }
        let data = target.path().join("plugins/mo2_update_arsenal/data");
        fs::create_dir_all(&data).unwrap();
        fs::write(data.join("chronicle.json"), b"personal progress").unwrap();
        assert!(verify_installed(&bundle, target.path()).is_ok());
        assert_eq!(
            fs::read(data.join("chronicle.json")).unwrap(),
            b"personal progress"
        );
        fs::write(
            target.path().join("plugins/mo2_update_arsenal/plugin.py"),
            b"changed after install",
        )
        .unwrap();
        assert!(verify_installed(&bundle, target.path()).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn refuses_linked_resource_and_target_ancestors() {
        let (root, manifest) = fixture();
        let holder = tempfile::tempdir().unwrap();
        let linked = holder.path().join("linked");
        std::os::unix::fs::symlink(root.path(), &linked).unwrap();
        assert!(verify(&linked, &manifest, "0.13.0").is_err());
        assert!(reject_link_ancestors(&linked.join("MO2/mo2_update_arsenal/plugin.py")).is_err());
    }
}
