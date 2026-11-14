//! On-demand metadata inventory of files without verified mod-source ownership.
//! This module never opens payloads, follows links, mutates mods or infers an
//! overwrite directory. A complete result is cached by the native caller.
use crate::{
    acquisition::{reject_link, valid_component},
    content_vault,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};

const MAX_ENTRIES: usize = 200_000;
const MAX_RESULT_BYTES: usize = 24 * 1024 * 1024;
type Result<T> = std::result::Result<T, String>;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModOwner {
    pub id: String,
    pub name: String,
    pub source_verified: bool,
    pub enabled: bool,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Entry {
    pub id: String,
    pub relative_path: String,
    pub mod_name: Option<String>,
    pub item_id: Option<String>,
    pub enabled: Option<bool>,
    pub kind: String,
    pub reason: String,
    pub bytes: u64,
    pub can_reveal: bool,
}
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Inventory {
    pub schema: u32,
    pub complete: bool,
    pub root: PathBuf,
    pub entries: Vec<Entry>,
    pub files: usize,
    pub bytes: u64,
    pub verified_mods_excluded: usize,
    pub scope: &'static str,
}
fn io(error: impl std::fmt::Display) -> String {
    error.to_string()
}
fn linked(meta: &fs::Metadata) -> bool {
    if meta.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if meta.file_attributes() & 0x400 != 0 {
            return true;
        }
    }
    false
}
#[derive(Debug, PartialEq, Eq)]
struct MetadataStamp {
    bytes: u64,
    modified: Option<std::time::SystemTime>,
    created: Option<std::time::SystemTime>,
    readonly: bool,
    kind: &'static str,
    identity: (u64, u64),
}
fn stamp(metadata: &fs::Metadata) -> MetadataStamp {
    let kind = if linked(metadata) {
        "link"
    } else if metadata.is_dir() {
        "folder"
    } else if metadata.is_file() {
        "file"
    } else {
        "special"
    };
    #[cfg(unix)]
    let identity = {
        use std::os::unix::fs::MetadataExt;
        (metadata.dev(), metadata.ino())
    };
    #[cfg(not(unix))]
    let identity = (0, 0);
    MetadataStamp {
        bytes: if metadata.is_dir() { 0 } else { metadata.len() },
        modified: metadata.modified().ok(),
        created: metadata.created().ok(),
        readonly: metadata.permissions().readonly(),
        kind,
        identity,
    }
}
fn recheck_inventory(
    root: &Path,
    before: &BTreeMap<String, MetadataStamp>,
    directories: &BTreeMap<String, BTreeSet<String>>,
    cancel: &impl Fn() -> bool,
) -> Result<()> {
    for (relative, expected) in before {
        if cancel() {
            return Err(
                "Unattributed-file inspection cancelled; the previous complete result is retained."
                    .into(),
            );
        }
        let path = root.join(relative);
        // Fresh path metadata, not a cached Windows DirEntry attribute block.
        let metadata = fs::symlink_metadata(&path).map_err(io)?;
        if stamp(&metadata) != *expected {
            return Err(
                "File metadata changed during inspection; no partial inventory was accepted."
                    .into(),
            );
        }
        if let Some(children) = directories.get(relative) {
            let mut current = BTreeSet::new();
            for child in fs::read_dir(&path).map_err(io)? {
                if cancel() {
                    return Err("Unattributed-file inspection cancelled; the previous complete result is retained.".into());
                }
                let name = child.map_err(io)?.file_name().into_string().map_err(
                    |_| "A filename changed during inspection; no partial inventory was accepted.",
                )?;
                if !current.insert(name) || current.len() > MAX_ENTRIES {
                    return Err(
                        "The complete directory inventory changed or exceeded its bound.".into(),
                    );
                }
            }
            if current != *children || stamp(&fs::symlink_metadata(path).map_err(io)?) != *expected
            {
                return Err("Directory entries changed during inspection; no partial inventory was accepted.".into());
            }
        }
    }
    Ok(())
}
fn id(root: &Path, relative: &str) -> String {
    format!(
        "inventory-{:x}",
        Sha256::digest(format!("{}\0{relative}", root.to_string_lossy()).as_bytes())
    )
}
fn append(report: &mut Inventory, entry: Entry, budget: &mut usize) -> Result<()> {
    *budget = budget
        .checked_add(serde_json::to_vec(&entry).map_err(io)?.len())
        .ok_or("Inventory size overflow.")?;
    if report.entries.len() >= MAX_ENTRIES || *budget > MAX_RESULT_BYTES {
        return Err("The complete unattributed inventory exceeds its display limit. Choose one listed mod and inspect that scope instead; no partial inventory was accepted.".into());
    }
    if entry.kind == "file" {
        report.files += 1;
        report.bytes = report
            .bytes
            .checked_add(entry.bytes)
            .ok_or("Inventory byte count overflow.")?;
    }
    report.entries.push(entry);
    Ok(())
}
/// `selected_id` narrows a large profile to an exact catalog mod. An empty
/// selection covers every unverified-source mod, unregistered folder and loose
/// file immediately under the independently supplied native mods root.
pub fn inspect(
    root: &Path,
    owners: &[ModOwner],
    selected_id: Option<&str>,
    cancel: &impl Fn() -> bool,
) -> Result<Inventory> {
    let root = content_vault::checked_root(root)?;
    let mut names = BTreeMap::new();
    let mut ids = BTreeSet::new();
    for owner in owners {
        if !valid_component(&owner.name)
            || owner.id.is_empty()
            || !ids.insert(owner.id.clone())
            || names
                .insert(owner.name.to_lowercase(), owner.clone())
                .is_some()
        {
            return Err("Native mod ownership is ambiguous; refresh the current profile before inspecting files.".into());
        }
    }
    let selected = selected_id
        .map(|id| {
            owners
                .iter()
                .find(|owner| owner.id == id)
                .ok_or("The selected native mod is no longer in this profile.")
        })
        .transpose()?;
    let mut report = Inventory {
        schema: 1,
        complete: true,
        root: root.clone(),
        entries: Vec::new(),
        files: 0,
        bytes: 0,
        verified_mods_excluded: 0,
        scope: "Read-only file metadata under the native MO2 mods directory. Verified-source mods are excluded from the all-mod scope. Links are listed without traversal. Game directories, saves, and MO2 Overwrite are not scanned.",
    };
    let mut budget = 0;
    let mut seen = BTreeSet::new();
    let mut top_names = BTreeSet::new();
    let mut before = BTreeMap::from([(
        String::new(),
        stamp(&fs::symlink_metadata(&root).map_err(io)?),
    )]);
    let mut directories = BTreeMap::new();
    let mut stack = Vec::new();
    let mut selected_found = false;
    for child in fs::read_dir(&root).map_err(io)? {
        if cancel() {
            return Err(
                "Unattributed-file inspection cancelled; the previous complete result is retained."
                    .into(),
            );
        }
        let child = child.map_err(io)?;
        let name = child.file_name().into_string().map_err(
            |_| "An entry has an unsupported filename; no partial inventory was accepted.",
        )?;
        if !seen.insert(name.to_lowercase()) {
            return Err("Case-alias folders prevent exact native mod ownership.".into());
        }
        top_names.insert(name.clone());
        if top_names.len() > MAX_ENTRIES {
            return Err("The native mods directory exceeds its complete inventory bound.".into());
        }
        let owner = names.get(&name.to_lowercase()).cloned();
        if let Some(selected) = selected {
            if owner.as_ref().is_none_or(|owner| owner.id != selected.id) {
                continue;
            }
            let metadata = fs::symlink_metadata(child.path()).map_err(io)?;
            if !metadata.is_dir() || linked(&metadata) {
                return Err("The selected native mod no longer has a real owned folder. Refresh its profile.".into());
            }
            selected_found = true;
        } else if owner.as_ref().is_some_and(|owner| owner.source_verified) {
            report.verified_mods_excluded += 1;
            continue;
        }
        let reason=if selected.is_some(){"Explicitly selected native mod; source attribution is shown in its catalog."}else if owner.is_some(){"This mod has no verified source in the current catalog; a tracked page may provide additional context."}else{"This entry has no owning mod record in the current profile catalog."}.to_string();
        stack.push((child.path(), name, owner, reason));
    }
    if selected.is_some() && !selected_found {
        return Err(
            "The selected native mod folder is missing; no empty inventory was accepted.".into(),
        );
    }
    directories.insert(String::new(), top_names);
    while let Some((path, relative, owner, reason)) = stack.pop() {
        if cancel() {
            return Err(
                "Unattributed-file inspection cancelled; the previous complete result is retained."
                    .into(),
            );
        }
        let meta = fs::symlink_metadata(&path).map_err(io)?;
        before.insert(relative.clone(), stamp(&meta));
        let link = linked(&meta);
        let kind = if link {
            "link"
        } else if meta.is_dir() {
            "folder"
        } else if meta.is_file() {
            "file"
        } else {
            "special"
        };
        append(
            &mut report,
            Entry {
                id: id(&root, &relative),
                relative_path: relative.clone(),
                mod_name: owner.as_ref().map(|o| o.name.clone()),
                item_id: owner.as_ref().map(|o| o.id.clone()),
                enabled: owner.as_ref().map(|o| o.enabled),
                kind: kind.into(),
                reason: if link {
                    "Link or reparse point: its target was not followed and is outside this inventory's authority.".into()
                } else if kind == "special" {
                    "Special filesystem object: no content was opened.".into()
                } else {
                    reason.clone()
                },
                bytes: if meta.is_file() && !link {
                    meta.len()
                } else {
                    0
                },
                can_reveal: !link && kind != "special",
            },
            &mut budget,
        )?;
        if kind == "folder" {
            let mut children = BTreeSet::new();
            let mut exact_children = BTreeSet::new();
            for child in fs::read_dir(&path).map_err(io)? {
                let child = child.map_err(io)?;
                let name = child.file_name().into_string().map_err(
                    |_| "An entry has an unsupported filename; no partial inventory was accepted.",
                )?;
                if !children.insert(name.to_lowercase()) {
                    return Err(
                        "Case-alias paths prevent a complete unambiguous file inventory.".into(),
                    );
                }
                exact_children.insert(name.clone());
                stack.push((
                    child.path(),
                    format!("{relative}/{name}"),
                    owner.clone(),
                    reason.clone(),
                ));
                if stack.len() + report.entries.len() > MAX_ENTRIES {
                    return Err("The complete unattributed inventory is too large. Inspect one exact mod instead; no partial result was accepted.".into());
                }
            }
            directories.insert(relative, exact_children);
            let after = fs::symlink_metadata(&path).map_err(io)?;
            if linked(&after)
                || !after.is_dir()
                || meta.modified().map_err(io)? != after.modified().map_err(io)?
            {
                return Err("A folder changed during inspection. Refresh the inventory; no partial result was accepted.".into());
            }
        }
    }
    // Reopening a cached view never walks files. This second bounded metadata
    // pass belongs only to the explicit scan, and catches changes to entries
    // visited earlier before accepting the complete result.
    recheck_inventory(&root, &before, &directories, cancel)?;
    report
        .entries
        .sort_by(|a, b| a.relative_path.cmp(&b.relative_path));
    Ok(report)
}
/// Resolve only a cached exact entry, verifying every current ancestor without
/// rescanning unrelated files. The host uses reveal-in-folder, never execution.
pub fn reveal_path(report: &Inventory, entry_id: &str) -> Result<PathBuf> {
    let entry = report
        .entries
        .iter()
        .find(|entry| entry.id == entry_id)
        .ok_or("This inventory entry is no longer available.")?;
    if !entry.can_reveal {
        return Err("Linked and special entries cannot be opened through this inventory.".into());
    }
    let root = content_vault::checked_root(&report.root)?;
    let mut path = root;
    for component in entry.relative_path.split('/') {
        if !valid_component(component) {
            return Err("Inventory path is no longer a valid native file component.".into());
        }
        path.push(component);
        reject_link(&path)?;
    }
    if !path.exists() {
        return Err("This entry has moved or disappeared. Refresh the inventory.".into());
    }
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn owner(name: &str, verified: bool) -> ModOwner {
        ModOwner {
            id: format!("mod-{name}"),
            name: name.into(),
            source_verified: verified,
            enabled: false,
        }
    }
    #[test]
    fn selected_native_folder_must_still_exist_as_a_real_directory() {
        let temp = tempfile::tempdir().unwrap();
        let owners = [owner("Missing", false)];
        assert!(
            inspect(temp.path(), &owners, Some("mod-Missing"), &|| false)
                .unwrap_err()
                .contains("folder is missing")
        );
        fs::write(temp.path().join("Missing"), b"not a directory").unwrap();
        assert!(
            inspect(temp.path(), &owners, Some("mod-Missing"), &|| false)
                .unwrap_err()
                .contains("real owned folder")
        );
    }
    #[test]
    fn additions_after_directory_enumeration_invalidate_the_complete_scan() {
        use std::cell::Cell;
        let temp = tempfile::tempdir().unwrap();
        fs::create_dir(temp.path().join("Unknown")).unwrap();
        fs::write(temp.path().join("Unknown/first.bin"), b"first").unwrap();
        let calls = Cell::new(0);
        let result = inspect(temp.path(), &[owner("Unknown", false)], None, &|| {
            calls.set(calls.get() + 1);
            if calls.get() == 3 {
                fs::write(temp.path().join("Unknown/late.bin"), b"late").unwrap();
            }
            false
        });
        assert!(result.unwrap_err().contains("changed during inspection"));
    }
    #[test]
    fn changes_to_already_visited_files_invalidate_the_complete_scan() {
        use std::cell::Cell;
        let temp = tempfile::tempdir().unwrap();
        fs::create_dir(temp.path().join("Unknown")).unwrap();
        for name in ["one.bin", "two.bin"] {
            fs::write(temp.path().join("Unknown").join(name), b"x").unwrap();
        }
        let calls = Cell::new(0);
        let result = inspect(temp.path(), &[owner("Unknown", false)], None, &|| {
            calls.set(calls.get() + 1);
            if calls.get() == 4 {
                for name in ["one.bin", "two.bin"] {
                    fs::write(temp.path().join("Unknown").join(name), b"changed length").unwrap();
                }
            }
            false
        });
        assert!(result.unwrap_err().contains("changed during inspection"));
    }
    #[test]
    fn unknown_ownership_is_complete_and_known_mods_are_not_walked() {
        let temp = tempfile::tempdir().unwrap();
        for name in ["Known", "Unknown", "Orphan"] {
            fs::create_dir(temp.path().join(name)).unwrap();
            fs::write(temp.path().join(name).join("file.dds"), b"actual").unwrap();
        }
        fs::write(temp.path().join("loose.bin"), b"loose").unwrap();
        let report = inspect(
            temp.path(),
            &[owner("Known", true), owner("Unknown", false)],
            None,
            &|| false,
        )
        .unwrap();
        assert_eq!(report.files, 3);
        assert_eq!(report.bytes, 17);
        assert_eq!(report.verified_mods_excluded, 1);
        assert!(
            !report
                .entries
                .iter()
                .any(|e| e.relative_path.starts_with("Known/"))
        );
        assert!(
            report
                .entries
                .iter()
                .any(|e| e.relative_path == "Unknown/file.dds"
                    && e.item_id.as_deref() == Some("mod-Unknown"))
        );
        assert!(
            report
                .entries
                .iter()
                .any(|e| e.relative_path == "Orphan/file.dds" && e.item_id.is_none())
        );
    }
    #[test]
    fn cached_reveal_does_not_walk_again_and_changed_paths_are_refused() {
        let temp = tempfile::tempdir().unwrap();
        fs::create_dir(temp.path().join("Unknown")).unwrap();
        fs::write(temp.path().join("Unknown/a.txt"), b"a").unwrap();
        let report = inspect(temp.path(), &[owner("Unknown", false)], None, &|| false).unwrap();
        let item = report.entries.iter().find(|e| e.kind == "file").unwrap();
        fs::write(temp.path().join("Unknown/new.txt"), b"new").unwrap();
        assert!(reveal_path(&report, &item.id).unwrap().ends_with("a.txt"));
        assert_eq!(report.files, 1);
        fs::remove_file(temp.path().join("Unknown/a.txt")).unwrap();
        assert!(reveal_path(&report, &item.id).is_err());
    }
    #[test]
    fn cancelled_and_unknown_scope_never_publish_partial_results() {
        let temp = tempfile::tempdir().unwrap();
        fs::write(temp.path().join("loose"), b"x").unwrap();
        assert!(inspect(temp.path(), &[], None, &|| true).is_err());
        assert!(inspect(temp.path(), &[], Some("missing"), &|| false).is_err());
    }
    #[cfg(unix)]
    #[test]
    fn links_are_reported_without_following_targets() {
        let temp = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        fs::write(outside.path().join("private"), b"secret").unwrap();
        std::os::unix::fs::symlink(outside.path(), temp.path().join("Linked")).unwrap();
        let report = inspect(temp.path(), &[], None, &|| false).unwrap();
        assert_eq!(report.entries.len(), 1);
        assert_eq!(report.entries[0].kind, "link");
        assert!(reveal_path(&report, &report.entries[0].id).is_err());
    }
}
